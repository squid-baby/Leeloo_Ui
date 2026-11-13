const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRelay } = require('../server.js');
const { Client, startRelay, tempDataFile, fakeClock, post, quietLog } = require('./helpers.js');

// Everything opened by a test is closed here, so the run exits cleanly.
const cleanup = [];
function track(item) {
  cleanup.push(item);
  return item;
}
afterEach(async () => {
  while (cleanup.length) await cleanup.pop().close();
});

async function relayUp(options) {
  return track(await startRelay(options));
}
async function connect(relay, wsOptions) {
  return track(await Client.open(relay.wsUrl, wsOptions));
}

// Crew with Alice (a1, creator) and Bob (b1) both online.
async function crewOfTwo(relay) {
  const alice = await connect(relay);
  const created = await alice.createCrew('a1', 'Alice');
  const bob = await connect(relay);
  const joined = await bob.joinCrew(created.crew_code, 'b1', 'Bob');
  await alice.next('member_joined');
  return { alice, bob, code: created.crew_code, created, joined };
}

test('create + join crew and live relay between two devices', async () => {
  const relay = await relayUp();
  const { alice, bob, code, created, joined } = await crewOfTwo(relay);

  assert.equal(created.type, 'crew_created');
  assert.equal(created.device_id, 'a1');
  assert.match(code, /^LEELOO-[A-Z2-9]{4}$/);

  assert.equal(joined.type, 'crew_joined');
  assert.equal(joined.device_id, 'b1');
  assert.equal(joined.crew_code, code);
  assert.equal(joined.crew_members, 2);
  assert.deepEqual(joined.member_names, ['Alice']);

  alice.sendMessage('text', { text: 'hello' });
  const msg = await bob.next('message');
  assert.equal(msg.from_device, 'a1');
  assert.equal(msg.from_name, 'Alice');
  assert.equal(msg.msg_type, 'text');
  assert.deepEqual(msg.payload, { text: 'hello' });
  assert.equal(typeof msg.timestamp, 'number');
  assert.equal('queued' in msg, false);

  // Sender does not get its own message; nothing was queued
  assert.deepEqual(await alice.drain('message'), []);
  assert.equal(relay.state.queues.size, 0);

  // Lower-case codes and app-level ping still work
  const carol = await connect(relay);
  const carolJoin = await carol.joinCrew(code.toLowerCase(), 'c1', 'Carol');
  assert.equal(carolJoin.type, 'crew_joined');
  assert.equal(carolJoin.crew_members, 3);
  carol.send({ type: 'ping' });
  assert.equal((await carol.next()).type, 'pong');
});

test('device-supplied crew code, legacy messages and member_offline', async () => {
  const relay = await relayUp();
  const alice = await connect(relay);
  const created = await alice.createCrew('a1', 'Alice', 'leeloo-test');
  assert.equal(created.crew_code, 'LEELOO-TEST');

  const bob = await connect(relay);
  await bob.joinCrew('LEELOO-TEST', 'b1', 'Bob');
  const joinedNote = await alice.next('member_joined');
  assert.deepEqual(joinedNote, { type: 'member_joined', device_id: 'b1', display_name: 'Bob' });

  alice.send({ type: 'share_music', spotify_uri: 'spotify:track:1', artist: 'A', track: 'T', album: 'L' });
  const shared = await bob.next('music_shared');
  assert.equal(shared.pushed_by, 'Alice');
  assert.equal(shared.spotify_uri, 'spotify:track:1');

  alice.send({ type: 'reaction', reaction_type: 'fire' });
  const reaction = await bob.next('reaction');
  assert.equal(reaction.from, 'Alice');
  assert.equal(reaction.reaction_type, 'fire');

  await bob.close();
  const offline = await alice.next('member_offline');
  assert.deepEqual(offline, { type: 'member_offline', device_id: 'b1', display_name: 'Bob' });
  assert.equal(relay.state.devices.has('b1'), false);
});

test('offline queue is delivered on rejoin, in order, with queued: true', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { alice, bob, code } = await crewOfTwo(relay);

  await bob.close();
  await alice.next('member_offline');

  alice.sendMessage('text', { text: 'one' });
  await alice.sync();
  clock.advance(1000);
  alice.sendMessage('song_push', { artist: 'Cinnamon', track: 'Two' });
  await alice.sync();
  clock.advance(1000);
  alice.sendMessage('hang_propose', { datetime: 'fri 8pm' });
  await alice.sync();
  clock.advance(1000);
  alice.sendMessage('hang_confirm', { datetime: 'fri 8pm' });
  await alice.sync();
  const sentAt = clock() - 3000;
  clock.advance(60000);

  const bob2 = await connect(relay);
  // crew_joined comes first, then the queue (joinCrew sends Bob's device key)
  const first = await bob2.joinCrew(code, 'b1', 'Bob');
  assert.equal(first.type, 'crew_joined');
  const queued = await bob2.drain('message');
  assert.deepEqual(queued.map((m) => m.msg_type), ['text', 'song_push', 'hang_propose', 'hang_confirm']);
  assert.deepEqual(queued.map((m) => m.timestamp), [sentAt, sentAt + 1000, sentAt + 2000, sentAt + 3000]);
  for (const m of queued) {
    assert.equal(m.queued, true);
    assert.equal(m.from_device, 'a1');
    assert.equal(m.from_name, 'Alice');
  }
  assert.deepEqual(queued[0].payload, { text: 'one' });

  // Queue is cleared: a second rejoin gets nothing
  assert.equal(relay.state.queues.has('b1'), false);
  await bob2.close();
  const bob3 = await connect(relay);
  await bob3.joinCrew(code, 'b1', 'Bob');
  assert.deepEqual(await bob3.drain('message'), []);
});

test('nudge and reaction are not queued', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);
  await bob.close();
  await alice.next('member_offline');

  alice.sendMessage('nudge', {});
  alice.sendMessage('reaction', { reaction: 'love' });
  alice.sendMessage('text', { text: 'kept' });
  await alice.sync();

  const bob2 = await connect(relay);
  await bob2.joinCrew(code, 'b1', 'Bob');
  const queued = await bob2.drain('message');
  assert.deepEqual(queued.map((m) => m.msg_type), ['text']);
});

test('queue cap drops the oldest messages', async () => {
  const relay = await relayUp({ queueLimit: 3 });
  const { alice, bob, code } = await crewOfTwo(relay);
  await bob.close();
  await alice.next('member_offline');

  for (let i = 1; i <= 5; i++) alice.sendMessage('text', { text: `m${i}` });
  await alice.sync();
  assert.equal(relay.state.queues.get('b1').length, 3);

  const bob2 = await connect(relay);
  await bob2.joinCrew(code, 'b1', 'Bob');
  const queued = await bob2.drain('message');
  assert.deepEqual(queued.map((m) => m.payload.text), ['m3', 'm4', 'm5']);
});

test('default queue cap is 50 and default TTL is 24 h', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { alice, bob, code } = await crewOfTwo(relay);
  await bob.close();
  await alice.next('member_offline');

  alice.sendMessage('text', { text: 'old' });
  await alice.sync();
  clock.advance(60 * 60000);
  for (let i = 1; i <= 55; i++) alice.sendMessage('text', { text: `m${i}` });
  await alice.sync();
  assert.equal(relay.state.queues.get('b1').length, 50);

  // 23 h after the batch: still there. One more hour: expired.
  clock.advance(23 * 3600000);
  relay.sweep();
  assert.equal(relay.state.queues.get('b1').length, 50);
  clock.advance(3600000);
  relay.sweep();
  assert.equal(relay.state.queues.has('b1'), false);

  const bob2 = await connect(relay);
  await bob2.joinCrew(code, 'b1', 'Bob');
  assert.deepEqual(await bob2.drain('message'), []);
});

test('expired messages are not delivered, fresh ones are', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock, queueTtlMs: 1000 });
  const { alice, bob, code } = await crewOfTwo(relay);
  await bob.close();
  await alice.next('member_offline');

  alice.sendMessage('text', { text: 'stale' });
  await alice.sync();
  clock.advance(5000);
  alice.sendMessage('text', { text: 'fresh' });
  await alice.sync();
  clock.advance(500);

  const bob2 = await connect(relay);
  await bob2.joinCrew(code, 'b1', 'Bob');
  const queued = await bob2.drain('message');
  assert.deepEqual(queued.map((m) => m.payload.text), ['fresh']);
});

test('state survives close + recreate from the same data file', async () => {
  const dataFile = tempDataFile();
  const clock = fakeClock();
  const relay1 = await relayUp({ dataFile, now: clock });
  const { alice, bob, code } = await crewOfTwo(relay1);

  const tgJoin = await post(relay1, '/api/telegram/crew/join',
    { telegram_user_id: 4242, crew_code: code, display_name: 'Tess' });
  assert.equal(tgJoin.status, 200);

  await bob.close();
  await alice.next('member_offline');
  alice.sendMessage('text', { text: 'while you were away' });
  await alice.sync();
  relay1.state.pendingSpotifyTokens.set('z9', { tokens: { access_token: 'tok' }, timestamp: clock() });

  await alice.close();
  await relay1.close();

  // File on disk is plain JSON in the documented format
  const saved = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  assert.equal(saved.version, 1);
  assert.deepEqual(saved.crews[code].members, { a1: 'Alice', b1: 'Bob' });
  assert.deepEqual(saved.crews[code].telegram_users, [[4242, 'Tess']]);
  assert.equal(saved.queues.b1.length, 1);
  assert.equal(saved.queues.b1[0].crew_code, code);
  assert.equal(saved.pendingSpotifyTokens.z9.tokens.access_token, 'tok');
  assert.deepEqual(fs.readdirSync(path.dirname(dataFile)), ['state.json']);

  clock.advance(60000);
  const relay2 = await relayUp({ dataFile, now: clock });
  const crew = relay2.state.crews.get(code);
  assert.ok(crew);
  assert.deepEqual(Array.from(crew.members.entries()), [['a1', 'Alice'], ['b1', 'Bob']]);
  // Numeric Telegram id still works as a lookup key
  assert.equal(crew.telegram_users.get(4242), 'Tess');
  assert.equal(relay2.state.pendingSpotifyTokens.get('z9').tokens.access_token, 'tok');

  const tgMsg = await post(relay2, '/api/telegram/message',
    { telegram_user_id: 4242, crew_code: code, sender_name: 'Tess', msg_type: 'text', payload: { text: 'hi' } });
  assert.equal(tgMsg.status, 200);
  assert.equal(tgMsg.body.devices_reached, 0);

  // Bob rejoins the crew that only exists because it was persisted
  const bob2 = await connect(relay2);
  const joined = await bob2.joinCrew(code, 'b1', 'Bob');
  assert.equal(joined.type, 'crew_joined');
  assert.deepEqual(joined.member_names, []);
  assert.deepEqual(joined.all_member_names.sort(), ['Alice', 'Tess']);
  const queued = await bob2.drain('message');
  assert.deepEqual(queued.map((m) => [m.from_name, m.payload.text, m.queued]),
    [['Alice', 'while you were away', true], ['Tess', 'hi', true]]);
});

test('debounced save writes without waiting for shutdown', async () => {
  const relay = await relayUp({ saveDebounceMs: 20 });
  const alice = await connect(relay);
  const created = await alice.createCrew('a1', 'Alice');
  await new Promise((resolve) => setTimeout(resolve, 150));
  const saved = JSON.parse(fs.readFileSync(relay.dataFile, 'utf8'));
  assert.deepEqual(saved.crews[created.crew_code].members, { a1: 'Alice' });
});

test('missing or corrupt data file starts empty', async () => {
  const dataFile = tempDataFile();
  fs.writeFileSync(dataFile, '{"crews": {"LEELOO-AAAA": ');
  const relay = track(createRelay({ dataFile, log: quietLog }));
  assert.equal(relay.state.crews.size, 0);

  fs.writeFileSync(dataFile, '[1, 2, 3]');
  const relay2 = track(createRelay({ dataFile, log: quietLog }));
  assert.equal(relay2.state.crews.size, 0);

  const relay3 = track(createRelay({ dataFile: tempDataFile(), log: quietLog }));
  assert.equal(relay3.state.crews.size, 0);
});

test('reconnect with the same device_id keeps the new socket', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);

  // Alice comes back on a new socket while the old one is still open
  const alice2 = await connect(relay);
  const rejoined = await alice2.joinCrew(code, 'a1', 'Alice');
  assert.equal(rejoined.type, 'crew_joined');
  assert.equal(rejoined.crew_members, 2);

  // The server closes the old socket
  assert.equal(await alice.closed, 4000);
  await bob.sync();
  await alice2.sync();

  assert.equal(relay.state.devices.get('a1').ws.readyState, 1);
  assert.equal(relay.state.devices.size, 2);
  assert.deepEqual(await bob.drain('member_offline'), []);

  // Both directions still work through the new socket
  bob.sendMessage('text', { text: 'still there?' });
  assert.equal((await alice2.next('message')).payload.text, 'still there?');
  alice2.sendMessage('text', { text: 'yes' });
  const reply = await bob.next('message');
  assert.equal(reply.payload.text, 'yes');
  assert.equal('queued' in reply, false);
  assert.equal(relay.state.queues.size, 0);

  // A real disconnect of the new socket is still announced
  await alice2.close();
  assert.equal((await bob.next('member_offline')).device_id, 'a1');
});

test('heartbeat terminates sockets that do not answer pings', async () => {
  const relay = await relayUp({ heartbeatMs: 150 });
  const alice = await connect(relay);
  const created = await alice.createCrew('a1', 'Alice');
  const dead = await connect(relay, { autoPong: false });
  await dead.joinCrew(created.crew_code, 'b1', 'Bob');
  await alice.next('member_joined');

  const offline = await alice.next('member_offline');
  assert.equal(offline.device_id, 'b1');
  assert.equal(relay.state.devices.has('b1'), false);

  // The healthy client survives several heartbeat rounds
  await new Promise((resolve) => setTimeout(resolve, 500));
  await alice.sync();
  assert.equal(relay.state.devices.has('a1'), true);
});

test('WebSocket join rate limit triggers and resets', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { code } = await crewOfTwo(relay);
  // (creating the crew used one unit of this IP's budget; start a fresh window)
  clock.advance(11 * 60000);

  const guesser = await connect(relay);
  for (let i = 0; i < 10; i++) {
    const res = await guesser.joinCrew(`LEELOO-BAD${i}`, 'g1', 'Guess');
    assert.equal(res.type, 'error');
    assert.equal(res.error, 'invalid_crew_code');
  }

  const limited = await guesser.joinCrew('LEELOO-BAD', 'g1', 'Guess');
  assert.equal(limited.type, 'error');
  assert.equal(limited.error, 'rate_limited');

  // Even a correct code is refused while limited, and nothing leaks
  const refused = await guesser.joinCrew(code, 'g1', 'Guess');
  assert.equal(refused.error, 'rate_limited');
  // create_crew / register with a code cannot be used to get around it
  assert.equal((await guesser.createCrew('g1', 'Guess', code)).error, 'rate_limited');
  guesser.send({ type: 'register', device_id: 'g1', crew_code: code });
  assert.equal((await guesser.next()).error, 'rate_limited');
  assert.equal(relay.state.crews.get(code).members.has('g1'), false);

  // A device already on the roster can still rejoin from the same IP
  const bobAgain = await connect(relay);
  assert.equal((await bobAgain.joinCrew(code, 'b1', 'Bob')).type, 'crew_joined');

  // Still limited just before the window ends, free after it
  clock.advance(9 * 60000);
  assert.equal((await guesser.joinCrew(code, 'g1', 'Guess')).error, 'rate_limited');
  clock.advance(2 * 60000);
  assert.equal((await guesser.joinCrew(code, 'g1', 'Guess')).type, 'crew_joined');

  relay.sweep();
  assert.equal(relay.state.joinFailures.size, 0);
});

test('successful joins are not limited and the limit is per client IP', async () => {
  const relay = await relayUp();
  const { code } = await crewOfTwo(relay);

  for (let i = 0; i < 15; i++) {
    const device = await connect(relay);
    assert.equal((await device.joinCrew(code, `d${i}`, `D${i}`)).type, 'crew_joined');
    await device.close();
  }

  // Exhaust the budget of one forwarded address
  const bad = await connect(relay, { headers: { 'X-Forwarded-For': '203.0.113.7, 10.0.0.1' } });
  for (let i = 0; i < 10; i++) await bad.joinCrew('LEELOO-NOPE', 'x1', 'X');
  assert.equal((await bad.joinCrew(code, 'x1', 'X')).error, 'rate_limited');

  // Another address is unaffected
  const other = await connect(relay, { headers: { 'X-Forwarded-For': '203.0.113.8' } });
  assert.equal((await other.joinCrew(code, 'y1', 'Y')).type, 'crew_joined');
});

test('Telegram join rate limit returns 429 and resets', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const created = await post(relay, '/api/telegram/crew/create', { telegram_user_id: 1, display_name: 'Owner' });
  const code = created.body.crew_code;

  for (let i = 0; i < 10; i++) {
    const res = await post(relay, '/api/telegram/crew/join', { telegram_user_id: 2, crew_code: 'LEELOO-ZZZZ' });
    assert.equal(res.status, 404);
    assert.equal(res.body.error, 'crew_not_found');
  }
  const limited = await post(relay, '/api/telegram/crew/join', { telegram_user_id: 2, crew_code: code });
  assert.equal(limited.status, 429);
  assert.equal(limited.body.error, 'rate_limited');
  assert.equal(relay.state.crews.get(code).telegram_users.has(2), false);

  clock.advance(10 * 60000 + 1);
  const ok = await post(relay, '/api/telegram/crew/join', { telegram_user_id: 2, crew_code: code, display_name: 'Two' });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.success, true);
});

test('with an API secret the Telegram limit is per Telegram user', async () => {
  const relay = await relayUp({ telegramApiSecret: 's3cret' });
  const auth = { 'X-Api-Secret': 's3cret' };
  assert.equal((await post(relay, '/api/telegram/crew/create', { telegram_user_id: 1 })).status, 401);
  const code = (await post(relay, '/api/telegram/crew/create', { telegram_user_id: 1 }, auth)).body.crew_code;

  for (let i = 0; i < 10; i++) {
    await post(relay, '/api/telegram/crew/join', { telegram_user_id: 2, crew_code: 'LEELOO-ZZZZ' }, auth);
  }
  assert.equal((await post(relay, '/api/telegram/crew/join', { telegram_user_id: 2, crew_code: code }, auth)).status, 429);
  // Another user behind the same bot is not locked out
  assert.equal((await post(relay, '/api/telegram/crew/join', { telegram_user_id: 3, crew_code: code }, auth)).status, 200);
});

test('Telegram message route reaches devices, queues for offline ones and requires membership', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);

  const outsider = await post(relay, '/api/telegram/message',
    { telegram_user_id: 77, crew_code: code, sender_name: 'Mallory', payload: { text: 'x' } });
  assert.equal(outsider.status, 403);
  assert.equal(outsider.body.error, 'not_in_crew');

  const missing = await post(relay, '/api/telegram/message', { telegram_user_id: 77 });
  assert.equal(missing.status, 400);

  const join = await post(relay, '/api/telegram/crew/join',
    { telegram_user_id: 77, crew_code: code.toLowerCase(), display_name: 'Tess' });
  assert.deepEqual(join.body, { success: true, crew_code: code, devices_online: 2 });

  await bob.close();
  await alice.next('member_offline');

  const sent = await post(relay, '/api/telegram/message',
    { telegram_user_id: 77, crew_code: code, sender_name: 'Tess', msg_type: 'text', payload: { text: 'from phone' } });
  assert.equal(sent.status, 200);
  assert.equal(sent.body.success, true);
  assert.equal(sent.body.devices_reached, 1);

  const live = await alice.next('message');
  assert.equal(live.from_device, 'telegram');
  assert.equal(live.from_name, 'Tess');
  assert.deepEqual(live.payload, { text: 'from phone' });

  // A Telegram nudge is live only
  await post(relay, '/api/telegram/message',
    { telegram_user_id: 77, crew_code: code, sender_name: 'Tess', msg_type: 'nudge', payload: {} });
  assert.equal((await alice.next('message')).msg_type, 'nudge');

  const bob2 = await connect(relay);
  await bob2.joinCrew(code, 'b1', 'Bob');
  const queued = await bob2.drain('message');
  assert.equal(queued.length, 1);
  assert.equal(queued[0].from_device, 'telegram');
  assert.equal(queued[0].queued, true);

  const status = await (await fetch(`${relay.httpUrl}/api/telegram/crew/status?crew_code=${code}`)).json();
  assert.equal(status.exists, true);
  assert.equal(status.devices_online, 2);
  assert.deepEqual(status.telegram_user_names, ['Tess']);
});

test('all_member_names lists roster and Telegram names without self or duplicates', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);
  const carol = await connect(relay);
  await carol.joinCrew(code, 'c1', 'Carol');
  await carol.close();
  await bob.close();

  await post(relay, '/api/telegram/crew/join', { telegram_user_id: 10, crew_code: code, display_name: 'Tess' });
  // Same name as a device: must appear once
  await post(relay, '/api/telegram/crew/join', { telegram_user_id: 11, crew_code: code, display_name: 'Carol' });
  await alice.sync();

  const dave = await connect(relay);
  const joined = await dave.joinCrew(code, 'd1', 'Dave');
  assert.deepEqual(joined.member_names, ['Alice']);
  assert.deepEqual(joined.all_member_names.slice().sort(), ['Alice', 'Bob', 'Carol', 'Tess']);

  // A device does not see itself, even with a new name
  await dave.close();
  const dave2 = await connect(relay);
  const again = await dave2.joinCrew(code, 'd1', 'David');
  assert.equal(again.all_member_names.includes('Dave'), false);
  assert.equal(again.all_member_names.includes('David'), false);
  assert.equal(relay.state.crews.get(code).members.get('d1'), 'David');
});

test('oversized and non-object WebSocket messages are ignored safely', async () => {
  const relay = await relayUp();
  const { alice, bob } = await crewOfTwo(relay);

  for (const junk of ['null', '[1,2]', '42', '"text"', 'not json', '{"type":"join_crew","crew_code":5}']) {
    bob.send(junk);
  }
  bob.send({ type: 'message', msg_type: 'text', payload: 'not an object' });
  const relayed = await alice.next('message');
  assert.deepEqual(relayed.payload, {});
  await bob.sync(); // connection is still alive

  // Over 64 KB: the socket is closed with 1009 and the server keeps running
  bob.send({ type: 'message', msg_type: 'text', payload: { text: 'x'.repeat(70 * 1024) } });
  assert.equal(await bob.closed, 1009);
  assert.equal((await alice.next('member_offline')).device_id, 'b1');
  await alice.sync();
});

test('Spotify callback rejects error responses and forwards good tokens', async () => {
  let reply;
  const fakeFetch = async () => ({ ok: reply.ok, status: reply.status, json: async () => reply.body });
  const relay = await relayUp({ fetch: fakeFetch });
  const alice = await connect(relay);
  alice.send({ type: 'register', device_id: 'a1', device_name: 'LEELOO' });
  const registered = await alice.next();
  assert.match(registered.device_key, /^[0-9a-f]{64}$/); // first bind of this id
  assert.deepEqual(registered, { type: 'registered', device_id: 'a1', device_key: registered.device_key });

  // Spotify error
  reply = { ok: false, status: 400, body: { error: 'invalid_grant' } };
  let res = await fetch(`${relay.httpUrl}/spotify/callback?code=abc&state=a1`);
  assert.equal(res.status, 502);
  assert.match(res.headers.get('content-type'), /text\/plain/);
  await res.text();

  // 200 without an access token
  reply = { ok: true, status: 200, body: { token_type: 'Bearer' } };
  res = await fetch(`${relay.httpUrl}/spotify/callback?code=abc&state=offline1`);
  assert.equal(res.status, 502);
  await res.text();
  assert.deepEqual(await alice.drain('spotify_auth_complete'), []);
  assert.equal(relay.state.pendingSpotifyTokens.size, 0);

  // Success, device online
  reply = { ok: true, status: 200, body: { access_token: 'AT', refresh_token: 'RT', expires_in: 3600 } };
  res = await fetch(`${relay.httpUrl}/spotify/callback?code=abc&state=a1`);
  assert.equal(res.status, 200);
  assert.match(await res.text(), /Spotify Connected!/);
  const auth = await alice.next('spotify_auth_complete');
  assert.deepEqual(auth.tokens, { access_token: 'AT', refresh_token: 'RT', expires_in: 3600 });

  // Success, device offline: stored, then delivered on connect
  res = await fetch(`${relay.httpUrl}/spotify/callback?code=abc&state=late1`);
  assert.equal(res.status, 200);
  await res.text();
  assert.equal(relay.state.pendingSpotifyTokens.has('late1'), true);
  const late = await connect(relay);
  late.send({ type: 'register', device_id: 'late1', device_name: 'LEELOO' });
  await late.next('registered');
  assert.equal((await late.next('spotify_auth_complete')).tokens.access_token, 'AT');
  assert.equal(relay.state.pendingSpotifyTokens.size, 0);

  assert.equal((await fetch(`${relay.httpUrl}/spotify/callback`)).status, 400);
});

test('/health reports live counts', async () => {
  const relay = await relayUp();
  await crewOfTwo(relay);
  const health = await (await fetch(`${relay.httpUrl}/health`)).json();
  assert.equal(health.status, 'ok');
  assert.equal(health.devices, 2);
  assert.equal(health.crews, 1);
  assert.equal(typeof health.uptime, 'number');
});
