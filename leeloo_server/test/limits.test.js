// Second attack review: legacy ids, queue growth, fetch rate, roster fill, limiter
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync, spawnSync } = require('node:child_process');
const { createRelay, ipLimitKey } = require('../server.js');
const { Client, startRelay, tempDataFile, fakeClock, post, quietLog, deviceKeys } = require('./helpers.js');

const RESET_TOOL = path.join(__dirname, '..', 'reset_device_key.js');

const cleanup = [];
function track(item) {
  cleanup.push(item);
  return item;
}
afterEach(async () => {
  while (cleanup.length) await cleanup.pop().close();
});

async function relayUp(options) {
  return track(await startRelay({ sourceTickMs: 0, ...options }));
}
async function connect(relay, wsOptions) {
  return track(await Client.open(relay.wsUrl, wsOptions));
}
const from = (ip) => ({ headers: { 'X-Forwarded-For': ip } });

async function waitFor(check, what) {
  for (let i = 0; i < 200; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timeout waiting for ${what}`);
}

async function mcpStatus(relay, token) {
  const res = await fetch(`${relay.httpUrl}/mcp/${token}`, {
    method: 'POST',
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'get_status', arguments: {} } })
  });
  return res.status;
}

const HOST = 'https://site.api.espn.com';
function packWith(n) {
  return { pack: 1, id: `p${n}`, name: 'P', sources: [{ id: 's', url: `${HOST}/x?n=${n}`, every: 60, fields: { v: 'value' } }] };
}
function fakeFetch() {
  const fake = async (url) => {
    fake.calls.push(String(url));
    if (fake.fail) throw new Error('down');
    return new Response(JSON.stringify({ value: 'ok' }), { status: 200 });
  };
  fake.calls = [];
  fake.fail = false;
  return fake;
}

// A state file as a relay from before the device keys wrote it: Vic and Mal
// on a roster, with a queued message, pending Spotify tokens and an agent link.
const LEGACY_TOKEN = 'ab'.repeat(32);
function legacyState(now = Date.now()) {
  const dataFile = tempDataFile();
  fs.writeFileSync(dataFile, JSON.stringify({
    version: 1,
    crews: { 'LEELOO-AAAA': { created_at: 1, telegram_users: [], members: { leeloo_vic: 'Vic', leeloo_mal: 'Mal' } } },
    queues: {
      leeloo_vic: [{ crew_code: 'LEELOO-AAAA', message: { type: 'message', msg_type: 'text', from_name: 'Mal', payload: { text: 'private' }, timestamp: now } }]
    },
    pendingSpotifyTokens: { leeloo_vic: { tokens: { access_token: 'spotify-secret-1', refresh_token: 'RT' }, timestamp: now } },
    pendingPackOps: { leeloo_vic: { message: { type: 'pack_reset' }, timestamp: now } },
    agentTokens: {
      [crypto.createHash('sha256').update(LEGACY_TOKEN).digest('hex')]: { device_id: 'leeloo_vic', crew_code: 'LEELOO-AAAA', created_at: 1 }
    }
  }));
  return dataFile;
}

// --- 1. Ids from before the device keys (attack a2) ---

test('legacy roster id: an outsider without the crew code cannot take it', async () => {
  const relay = await relayUp({ dataFile: legacyState() });
  assert.equal(await mcpStatus(relay, LEGACY_TOKEN), 200);

  const outsider = await connect(relay);
  const attempts = [
    { type: 'register', device_id: 'leeloo_vic' },
    { type: 'register', device_id: 'leeloo_vic', crew_code: 'LEELOO-ZZZZ' },
    { type: 'create_crew', device_id: 'leeloo_vic', display_name: 'Vic' },
    { type: 'create_crew', device_id: 'leeloo_vic', crew_code: 'LEELOO-MINE', display_name: 'Vic' },
    { type: 'join_crew', device_id: 'leeloo_vic', crew_code: 'LEELOO-ZZZZ' },
    { type: 'register', device_id: 'leeloo_vic', device_key: 'f'.repeat(64) }
  ];
  for (const attempt of attempts) {
    outsider.send(attempt);
    const reply = await outsider.next();
    assert.equal(reply.error, 'device_key_required', JSON.stringify(attempt));
    assert.equal('device_key' in reply, false);
  }
  // Each one counted for the address
  assert.equal(relay.state.joinFailures.get('ip:127.0.0.1').length, attempts.length);

  // Nothing of Vic's came out, nothing of Vic's was changed
  outsider.send({ type: 'agent_connect_request' });
  assert.equal((await outsider.next()).error, 'not_in_crew');
  outsider.send({ type: 'agent_disconnect' });
  assert.deepEqual(await outsider.drain(), []);
  assert.equal(relay.state.deviceKeys.size, 0);
  assert.equal(relay.state.crews.has('LEELOO-MINE'), false);
  assert.equal(relay.state.queues.get('leeloo_vic').length, 1);
  assert.equal(relay.state.pendingSpotifyTokens.has('leeloo_vic'), true);
  assert.equal(await mcpStatus(relay, LEGACY_TOKEN), 200);
});

test('legacy roster id: the first bind with the crew code gets a key and nothing else', async () => {
  const lines = [];
  const log = { log: (...args) => lines.push(args.join(' ')), error() {} };
  const relay = await relayUp({ dataFile: legacyState(), log });

  // Mal knows the crew code, so the relay cannot tell her from Vic. What it
  // can do: give this bind no secrets of Vic.
  const mal = await connect(relay);
  mal.send({ type: 'join_crew', crew_code: 'LEELOO-AAAA', device_id: 'leeloo_vic', display_name: 'Vic' });
  const joined = await mal.next();
  assert.equal(joined.type, 'crew_joined');
  assert.match(joined.device_key, /^[0-9a-f]{64}$/);
  assert.deepEqual(await mal.drain(), [], 'no queue, no Spotify tokens, no pack operation');

  assert.equal(relay.state.queues.has('leeloo_vic'), false);
  assert.equal(relay.state.pendingSpotifyTokens.has('leeloo_vic'), false);
  assert.equal(relay.state.pendingPackOps.has('leeloo_vic'), false);
  assert.equal(relay.state.agentTokens.size, 0);
  assert.equal(await mcpStatus(relay, LEGACY_TOKEN), 401);
  assert.equal(relay.state.queuedBytes, 0);

  const logged = lines.filter((l) => l.includes('First keyed bind of roster device leeloo_vic'));
  assert.equal(logged.length, 1);
  assert.ok(!lines.join('\n').includes(joined.device_key));
  assert.ok(!lines.join('\n').includes('spotify-secret-1'));

  // The same goes for register / create_crew that name the roster's crew
  for (const type of ['register', 'create_crew']) {
    const other = await relayUp({ dataFile: legacyState() });
    const client = await connect(other);
    client.send({ type, crew_code: 'LEELOO-AAAA', device_id: 'leeloo_vic', display_name: 'Vic', device_name: 'Vic' });
    const reply = await client.next();
    assert.match(reply.device_key, /^[0-9a-f]{64}$/, type);
    assert.deepEqual(await client.drain(), [], type);
    assert.equal(other.state.pendingSpotifyTokens.size, 0, type);
    assert.equal(other.state.agentTokens.size, 0, type);
  }

  // Later binds (with the key) work as always: queue and Spotify tokens arrive
  await mal.close();
  await waitFor(() => !relay.state.devices.has('leeloo_vic'), 'offline');
  relay.state.pendingSpotifyTokens.set('leeloo_vic', { tokens: { access_token: 'NEW' }, timestamp: Date.now() });
  const again = await connect(relay);
  again.send({ type: 'join_crew', crew_code: 'LEELOO-AAAA', device_id: 'leeloo_vic', device_key: joined.device_key });
  assert.equal((await again.next()).type, 'crew_joined');
  assert.equal((await again.next('spotify_auth_complete')).tokens.access_token, 'NEW');
});

test('an id that the relay does not know: the bind that makes the key gets no Spotify tokens', async () => {
  const relay = await relayUp();
  relay.state.pendingSpotifyTokens.set('new1', { tokens: { access_token: 'AT' }, timestamp: Date.now() });

  const squatter = await connect(relay);
  squatter.send({ type: 'create_crew', device_id: 'new1', display_name: 'X' });
  const created = await squatter.next();
  assert.match(created.device_key, /^[0-9a-f]{64}$/);
  assert.deepEqual(await squatter.drain('spotify_auth_complete'), []);
  assert.equal(relay.state.pendingSpotifyTokens.has('new1'), true);
});

test('impersonation with odd device_key values is refused (attack a1)', async () => {
  const relay = await relayUp({ joinMaxFailures: 1000 });
  const vic = await connect(relay);
  const created = await vic.createCrew('v1', 'Vic');
  const key = deviceKeys.get('v1');
  const odd = [null, '', 1, 0, true, {}, { toString: 'x' }, [key], [], 'a'.repeat(60000),
    relay.state.deviceKeys.get('v1'), key.toUpperCase(), ` ${key}`, `${key} `, key.slice(0, 63)];

  const attacker = await connect(relay);
  for (const type of ['register', 'create_crew', 'join_crew']) {
    for (const value of odd) {
      for (const crew_code of [created.crew_code, undefined]) {
        attacker.send({ type, device_id: 'v1', device_key: value, crew_code, display_name: 'X' });
        const reply = await attacker.next();
        assert.equal(reply.error, 'device_key_required', `${type} ${JSON.stringify(value).slice(0, 30)}`);
      }
    }
  }
  attacker.send({ type: 'agent_connect_request' });
  assert.equal((await attacker.next()).error, 'not_in_crew');
  await vic.sync();
  assert.equal(relay.state.devices.size, 1);
});

test('reset_device_key.js removes the key and the agent links of one device', async () => {
  const dataFile = tempDataFile();
  const first = await relayUp({ dataFile });
  const vic = await connect(first);
  const created = await vic.createCrew('v1', 'Vic');
  const bob = await connect(first);
  await bob.joinCrew(created.crew_code, 'b1', 'Bob');
  for (const client of [vic, bob]) {
    client.send({ type: 'agent_connect_request' });
    const { code } = await client.next('agent_connect');
    assert.equal((await fetch(`${first.httpUrl}/connect/${code}`, { method: 'POST' })).status, 200);
  }
  await first.close();
  const before = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  const hashes = [before.deviceKeys.v1, ...Object.keys(before.agentTokens)];

  const out = execFileSync(process.execPath, [RESET_TOOL, dataFile, 'v1'], { encoding: 'utf8' });
  assert.match(out, /device key: removed/);
  assert.match(out, /agent links: 1 removed/);
  assert.ok(out.includes(created.crew_code));
  for (const secret of [...hashes, deviceKeys.get('v1')]) assert.ok(!out.includes(secret), 'no secrets in the output');

  const after = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  assert.deepEqual(Object.keys(after.deviceKeys), ['b1']);
  assert.deepEqual(Object.values(after.agentTokens).map((t) => t.device_id), ['b1']);
  assert.deepEqual(after.crews, before.crews);
  assert.deepEqual(fs.readdirSync(path.dirname(dataFile)), ['state.json'], 'no temp file left');
  assert.equal(fs.statSync(dataFile).mode & 0o777, 0o600);

  // A second run has nothing to do
  assert.match(execFileSync(process.execPath, [RESET_TOOL, dataFile, 'v1'], { encoding: 'utf8' }), /Nothing to change/);

  // The device can register again: with the crew code only
  const second = await relayUp({ dataFile });
  const lost = await connect(second);
  lost.send({ type: 'register', device_id: 'v1' });
  assert.equal((await lost.next()).error, 'device_key_required');
  lost.send({ type: 'join_crew', crew_code: created.crew_code, device_id: 'v1', display_name: 'Vic' });
  const rejoined = await lost.next();
  assert.equal(rejoined.type, 'crew_joined');
  assert.match(rejoined.device_key, /^[0-9a-f]{64}$/);
  assert.notEqual(rejoined.device_key, deviceKeys.get('b1'));

  // Refuses bad input and leaves the file as it is
  const missing = spawnSync(process.execPath, [RESET_TOOL, `${dataFile}.nope`, 'v1'], { encoding: 'utf8' });
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /cannot read/);
  const corrupt = tempDataFile();
  fs.writeFileSync(corrupt, '{"crews": ');
  const bad = spawnSync(process.execPath, [RESET_TOOL, corrupt, 'v1'], { encoding: 'utf8' });
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /not valid JSON/);
  assert.equal(fs.readFileSync(corrupt, 'utf8'), '{"crews": ');
  fs.writeFileSync(corrupt, '[1, 2]');
  assert.equal(spawnSync(process.execPath, [RESET_TOOL, corrupt, 'v1']).status, 1);
  assert.equal(spawnSync(process.execPath, [RESET_TOOL, dataFile]).status, 2);
});

// --- 2. Queues (attack a3 iii) ---

test('message payloads have a size limit: message_too_large, nothing relayed or queued', async () => {
  const relay = await relayUp();
  const alice = await connect(relay);
  const { crew_code: code } = await alice.createCrew('a1', 'Alice');
  const bob = await connect(relay);
  await bob.joinCrew(code, 'b1', 'Bob');
  const carol = await connect(relay);
  await carol.joinCrew(code, 'c1', 'Carol');
  await carol.close();
  await waitFor(() => !relay.state.devices.has('c1'), 'carol offline');
  await alice.drain();
  await bob.drain();

  const fits = { text: 'x'.repeat(2048 - JSON.stringify({ text: '' }).length) };
  assert.equal(Buffer.byteLength(JSON.stringify(fits)), 2048);
  alice.sendMessage('text', fits);
  assert.equal((await bob.next('message')).payload.text.length, fits.text.length);

  const tooBig = [
    ['text', { text: `${fits.text}y` }],
    ['text', { text: 'A'.repeat(63000) }],
    ['song_push', { artist: 'a', junk: 'j'.repeat(3000) }],
    ['nudge', { junk: 'j'.repeat(3000) }],
    ['pack_offer', { pack: { pack: 1, id: 'a', name: 'A' }, junk: 'j'.repeat(9000) }]
  ];
  for (const [msgType, payload] of tooBig) {
    alice.sendMessage(msgType, payload);
    const reply = await alice.next();
    assert.equal(reply.type, 'error', msgType);
    assert.equal(reply.error, 'message_too_large', msgType);
  }
  alice.send({ type: 'share_music', artist: 'a'.repeat(3000) });
  assert.equal((await alice.next()).error, 'message_too_large');
  alice.send({ type: 'reaction', reaction_type: 'r'.repeat(3000) });
  assert.equal((await alice.next()).error, 'message_too_large');

  assert.deepEqual(await bob.drain(), []);
  assert.equal(relay.state.queues.get('c1').length, 1, 'only the message that fits');

  // A pack offer may be larger than a text (a pack is 7,800 bytes at most)
  const bigPack = { pack: 1, id: 'big', name: 'Big', panels: Array.from({ length: 6 }, () => ({ slot: 'weather', rows: [{ left: 'a', bar: { value: 'v'.repeat(1200), max: 1 } }] })) };
  alice.sendMessage('pack_offer', { pack: bigPack });
  assert.equal((await bob.next('message')).msg_type, 'pack_offer');

  // The Telegram route has the same limit
  const tg = await post(relay, '/api/telegram/crew/join', { telegram_user_id: 7, crew_code: code, display_name: 'T' });
  assert.equal(tg.status, 200);
  const big = await post(relay, '/api/telegram/message',
    { telegram_user_id: 7, crew_code: code, sender_name: 'T', msg_type: 'text', payload: { text: 'x'.repeat(5000) } });
  assert.equal(big.status, 413);
  assert.equal(big.body.error, 'message_too_large');
  assert.deepEqual(await bob.drain('message'), []);
});

test('a device relays at most 30 messages per minute, also across reconnects', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const alice = await connect(relay);
  const { crew_code: code } = await alice.createCrew('a1', 'Alice');
  const bob = await connect(relay);
  await bob.joinCrew(code, 'b1', 'Bob');
  await alice.drain();

  for (let i = 0; i < 28; i++) alice.sendMessage('nudge', {});
  alice.send({ type: 'share_music', artist: 'a', track: 't' });
  alice.send({ type: 'reaction', reaction_type: 'love' });
  await alice.sync();
  assert.deepEqual(await alice.drain('error'), []);
  alice.sendMessage('text', { text: 'one too many' });
  const limited = await alice.next('error');
  assert.equal(limited.error, 'rate_limited');
  assert.equal((await bob.drain()).length, 30);

  // A new socket of the same device has the same count
  await alice.close();
  const alice2 = await connect(relay);
  await alice2.joinCrew(code, 'a1', 'Alice');
  alice2.sendMessage('text', { text: 'still too many' });
  assert.equal((await alice2.next('error')).error, 'rate_limited');
  // Bob has his own count
  bob.sendMessage('text', { text: 'hi' });
  assert.equal((await alice2.next('message')).payload.text, 'hi');

  clock.advance(60000);
  alice2.sendMessage('text', { text: 'later' });
  assert.equal((await bob.next('message')).payload.text, 'later');
  clock.advance(60000);
  relay.sweep();
  assert.equal(relay.state.messageTimes.size, 0);
});

test('offline queues have byte limits: per device and for all together', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock, maxMessagesPerMinute: 1000, queueMaxBytes: 10000, queuesMaxBytes: 25000, maxCrewMembers: 20 });
  const alice = await connect(relay);
  const { crew_code: code } = await alice.createCrew('a1', 'Alice');
  for (const id of ['g1', 'g2', 'g3']) {
    const ghost = await connect(relay);
    await ghost.joinCrew(code, id, id);
    await ghost.close();
  }
  await waitFor(() => relay.state.devices.size === 1, 'ghosts offline');

  const text = 't'.repeat(1900);
  for (let i = 0; i < 12; i++) {
    clock.advance(1000);
    alice.sendMessage('text', { text: `${i} ${text}` });
  }
  await alice.sync();

  const entryBytes = (q) => q.reduce((sum, e) => sum + Buffer.byteLength(JSON.stringify(e.message)), 0);
  let total = 0;
  for (const id of ['g1', 'g2', 'g3']) {
    const queue = relay.state.queues.get(id);
    assert.ok(entryBytes(queue) <= 10000, `${id}: ${entryBytes(queue)} bytes`);
    assert.ok(queue.length >= 3 && queue.length <= 5, `${id}: ${queue.length} messages`);
    assert.match(queue[queue.length - 1].message.payload.text, /^11 /, 'the newest message is kept');
    total += entryBytes(queue);
  }
  assert.ok(total <= 25000, `all queues: ${total} bytes`);
  assert.equal(relay.state.queuedBytes, total, 'the counter is exact');

  // The whole state stays small, whatever one sender does
  relay.flush();
  assert.ok(fs.statSync(relay.dataFile).size < 40000);

  // Counter stays exact through delivery, expiry and a restart
  const g1 = await connect(relay);
  await g1.joinCrew(code, 'g1', 'g1');
  assert.ok((await g1.drain('message')).length >= 1);
  assert.equal(relay.state.queuedBytes, entryBytes(relay.state.queues.get('g2')) + entryBytes(relay.state.queues.get('g3')));
  const dataFile = relay.dataFile;
  await relay.close();
  const again = await relayUp({ dataFile, now: clock });
  assert.equal(again.state.queuedBytes, entryBytes(again.state.queues.get('g2')) + entryBytes(again.state.queues.get('g3')));
  clock.advance(25 * 3600000);
  again.sweep();
  assert.equal(again.state.queues.size, 0);
  assert.equal(again.state.queuedBytes, 0);
});

test('the queue flood of the review stays small with the default limits', async () => {
  const relay = await relayUp({ maxMessagesPerMinute: 1000 }); // only the size limits at work
  const boss = await connect(relay);
  const { crew_code: code } = await boss.createCrew('q0', 'Q');
  for (let i = 1; i <= 9; i++) {
    const ghost = await connect(relay, from(`198.51.100.${i}`));
    await ghost.joinCrew(code, `q${i}`, 'G');
    await ghost.close();
  }
  await waitFor(() => relay.state.devices.size === 1, 'ghosts offline');
  for (let i = 0; i < 50; i++) boss.sendMessage('text', { text: 'A'.repeat(63000) + i });
  for (let i = 0; i < 50; i++) boss.sendMessage('text', { text: 'B'.repeat(1900) + i });
  await boss.sync();
  relay.flush();
  // 9 queues of 64 KB at most (the review got 28 MB)
  assert.ok(fs.statSync(relay.dataFile).size < 700000, `${fs.statSync(relay.dataFile).size} bytes`);
  assert.ok(relay.state.queuedBytes <= 9 * 65536);
  const q1 = relay.state.queues.get('q1');
  assert.ok(q1.length >= 30 && q1.length <= 33, `${q1.length} messages of 1.9 KB fit in 64 KB`);
  assert.ok(q1.every((entry) => entry.message.payload.text.startsWith('B')), 'no 63 KB message got in');
  assert.equal((await boss.drain('error')).filter((e) => e.error === 'message_too_large').length, 50);
});

// --- 3. Fetches (attack a3 i) ---

test('the pack_active change limit is per device id: rejoins and reconnects do not reset it', async () => {
  const fake = fakeFetch();
  const clock = fakeClock();
  const relay = await relayUp({ fetch: fake, now: clock });
  const att = await connect(relay);
  const { crew_code: code } = await att.createCrew('att1', 'Att');

  let n = 0;
  for (let i = 0; i < 60; i++) att.send({ type: 'pack_active', pack: packWith(n++) });
  // The review: join again on the same socket, 20 more changes, and so on
  for (let round = 0; round < 25; round++) {
    att.send({ type: 'join_crew', crew_code: code });
    for (let i = 0; i < 20; i++) att.send({ type: 'pack_active', pack: packWith(n++) });
  }
  await att.sync();
  // ... and on new sockets
  for (let round = 0; round < 3; round++) {
    const again = await connect(relay);
    await again.joinCrew(code, 'att1', 'Att');
    for (let i = 0; i < 20; i++) again.send({ type: 'pack_active', pack: packWith(n++) });
    await again.sync();
  }
  await relay.pollSources();
  assert.equal(fake.calls.length, 20, '20 changed packs in 10 minutes, so 20 fetches');
  assert.equal(relay.state.devicePacks.get('att1').id, 'p19');
  assert.equal(relay.state.packChanges.get('att1').length, 20);

  clock.advance(10 * 60000);
  relay.sweep();
  assert.equal(relay.state.packChanges.size, 0);
});

test('the relay makes at most maxFetchesPerMinute requests: the other sources wait', async () => {
  const fake = fakeFetch();
  const clock = fakeClock();
  const lines = [];
  const log = { log: (...args) => lines.push(args.join(' ')), error() {} };
  const relay = await relayUp({ fetch: fake, now: clock, log, maxFetchesPerMinute: 5, maxCrewMembers: 20 });
  const first = await connect(relay);
  const { crew_code: code } = await first.createCrew('d0', 'D0');
  const clients = [first];
  for (let i = 1; i < 8; i++) {
    const client = await connect(relay, from(`198.51.100.${i}`));
    await client.joinCrew(code, `d${i}`, `D${i}`);
    clients.push(client);
  }
  // 8 devices, 6 different URLs (the crew limit), all new at the same time
  for (let i = 0; i < 8; i++) clients[i].send({ type: 'pack_active', pack: packWith(i % 6) });
  for (const client of clients) await client.sync();
  await relay.pollSources();
  assert.equal(fake.calls.length, 5);
  assert.equal(lines.filter((l) => l.includes('fetches in the last minute')).length, 1, 'logged once');
  // The device whose source waits has its pack_data, without the value
  assert.deepEqual((await clients[5].next('pack_data')).values, {});

  // Polling again does not help before the minute is over
  clock.advance(30000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 5);

  clock.advance(30000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 10, 'the next 5: the URL that waited comes with them');
  assert.ok(fake.calls.slice(5).includes(`${HOST}/x?n=5`));
  assert.deepEqual((await clients[5].next('pack_data')).values, { 's.v': 'ok' });
});

test('a URL is never fetched again within 10 s, also after a failure and after a rejoin', async () => {
  const fake = fakeFetch();
  fake.fail = true;
  const clock = fakeClock();
  const relay = await relayUp({ fetch: fake, now: clock });
  const dev = await connect(relay);
  const { crew_code: code } = await dev.createCrew('d1', 'D');
  dev.send({ type: 'pack_active', pack: packWith(1) });
  await dev.next('pack_data');
  assert.equal(fake.calls.length, 1);

  // Failed fetch: join again, send the pack again, change a field, poll
  for (let i = 0; i < 5; i++) {
    dev.send({ type: 'join_crew', crew_code: code });
    dev.send({ type: 'pack_active', pack: packWith(1) });
    const changed = packWith(1);
    changed.sources[0].fields = { v: 'value', [`f${i}`]: `other${i}` };
    dev.send({ type: 'pack_active', pack: changed });
    await dev.sync();
    await relay.pollSources();
    clock.advance(1900);
  }
  assert.equal(fake.calls.length, 1, '9.5 s later: still one fetch');
  clock.advance(60000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 2);
});

// --- 4. Rosters and crews (attack a3 ii, v) ---

test('a first join to a roster costs one unit of the address budget', async () => {
  const relay = await relayUp();
  const vic = await connect(relay, from('192.0.2.1'));
  const { crew_code: code } = await vic.createCrew('vic', 'Vic');

  let joined = 0;
  let refused = null;
  for (let i = 0; i < 12; i++) {
    const ghost = await connect(relay, from('192.0.2.66'));
    const reply = await ghost.joinCrew(code, `ghost${i}`, 'G');
    if (reply.type === 'crew_joined') joined++;
    else refused = refused || reply.error;
    await ghost.close();
  }
  assert.equal(joined, 10);
  assert.equal(refused, 'rate_limited');
  assert.equal(relay.state.crews.get(code).members.size, 11);
  // Ghosts that are on the roster can come back for free
  const back = await connect(relay, from('192.0.2.66'));
  assert.equal((await back.joinCrew(code, 'ghost0', 'G')).type, 'crew_joined');
});

test('a full roster: the member that was offline longest leaves, real devices get in', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock, maxCrewMembers: 4 });
  const vic = await connect(relay);
  const { crew_code: code } = await vic.createCrew('vic', 'Vic');

  // Three ghosts fill the roster and go offline, one after the other
  const ghosts = [];
  for (let i = 0; i < 3; i++) {
    clock.advance(1000);
    const ghost = await connect(relay, from(`198.51.100.${i}`));
    await ghost.joinCrew(code, `ghost${i}`, `G${i}`);
    ghosts.push(ghost);
  }
  // ghost1 has an agent link, a pack and a pending install
  ghosts[1].send({ type: 'agent_connect_request' });
  const { code: connectCode } = await ghosts[1].next('agent_connect');
  const page = await (await fetch(`${relay.httpUrl}/connect/${connectCode}`, { method: 'POST' })).text();
  const token = /\/mcp\/([0-9a-f]{64})/.exec(page)[1];
  ghosts[1].send({ type: 'pack_active', pack: { pack: 1, id: 'g', name: 'G' } });
  await ghosts[1].sync();
  for (const i of [1, 0, 2]) { // ghost1 goes first: it is the one seen longest ago
    clock.advance(1000);
    await ghosts[i].close();
    await waitFor(() => !relay.state.devices.has(`ghost${i}`), 'ghost offline');
  }
  vic.sendMessage('text', { text: 'for the offline ones' });
  await vic.sync();
  relay.state.pendingPackOps.set('ghost1', { message: { type: 'pack_reset' }, timestamp: clock() });
  assert.equal(relay.state.queues.has('ghost1'), true);
  assert.equal(await mcpStatus(relay, token), 200);

  // A friend joins the full crew
  const friend = await connect(relay, from('203.0.113.1'));
  const joined = await friend.joinCrew(code, 'friend', 'Friend');
  assert.equal(joined.type, 'crew_joined');
  assert.deepEqual(Array.from(relay.state.crews.get(code).members.keys()).sort(), ['friend', 'ghost0', 'ghost2', 'vic']);
  assert.equal(relay.state.memberCrew.has('ghost1'), false);
  assert.equal(relay.state.queues.has('ghost1'), false);
  assert.equal(relay.state.pendingPackOps.has('ghost1'), false);
  assert.equal(relay.state.devicePacks.has('ghost1'), false);
  assert.equal(await mcpStatus(relay, token), 401);
  assert.equal(relay.state.deviceKeys.has('ghost1'), true, 'its key stays');
  assert.equal(relay.state.queues.has('ghost0'), true, 'the others keep their queue');

  // The one that left can join again as itself (with its key); now ghost0 leaves
  const ghost1 = await connect(relay, from('198.51.100.1'));
  const back = await ghost1.joinCrew(code, 'ghost1', 'G1');
  assert.equal(back.type, 'crew_joined');
  assert.equal('device_key' in back, false);
  assert.deepEqual(Array.from(relay.state.crews.get(code).members.keys()).sort(), ['friend', 'ghost1', 'ghost2', 'vic']);

  // Without its key nobody takes the id of a device that left
  const thief = await connect(relay, from('203.0.113.9'));
  thief.send({ type: 'join_crew', crew_code: code, device_id: 'ghost0', display_name: 'G0' });
  assert.equal((await thief.next()).error, 'device_key_required');

  // last_seen is in the state file
  relay.flush();
  const saved = JSON.parse(fs.readFileSync(relay.dataFile, 'utf8'));
  assert.deepEqual(Object.keys(saved.crews[code].last_seen).sort(), ['friend', 'ghost1', 'ghost2', 'vic']);
});

test('crew_full only when every member is connected right now', async () => {
  const relay = await relayUp({ maxCrewMembers: 2 });
  const vic = await connect(relay);
  const { crew_code: code } = await vic.createCrew('vic', 'Vic');
  const bob = await connect(relay);
  await bob.joinCrew(code, 'bob', 'Bob');

  const third = await connect(relay);
  assert.equal((await third.joinCrew(code, 'third', 'T')).error, 'crew_full');
  assert.equal(relay.state.crews.get(code).members.size, 2);

  await bob.close();
  await waitFor(() => !relay.state.devices.has('bob'), 'bob offline');
  assert.equal((await third.joinCrew(code, 'third', 'T')).type, 'crew_joined');
  assert.deepEqual(Array.from(relay.state.crews.get(code).members.keys()), ['vic', 'third']);
});

test('the number of crews is limited: server_full', async () => {
  const relay = await relayUp({ maxCrews: 2, joinMaxFailures: 100 });
  const a = await connect(relay);
  const first = await a.createCrew('a1', 'A');
  const b = await connect(relay);
  assert.equal((await b.createCrew('b1', 'B', 'LEELOO-BBBB')).type, 'crew_created');

  const c = await connect(relay);
  const refused = await c.createCrew('c1', 'C');
  assert.equal(refused.type, 'error');
  assert.equal(refused.error, 'server_full');
  assert.equal((await c.createCrew('c1', 'C', 'LEELOO-CCCC')).error, 'server_full');
  assert.equal((await c.register('c1', 'C', 'LEELOO-DDDD')).error, 'server_full');
  const tg = await post(relay, '/api/telegram/crew/create', { telegram_user_id: 1, display_name: 'T' });
  assert.equal(tg.status, 503);
  assert.equal(tg.body.error, 'server_full');
  assert.equal(relay.state.crews.size, 2);
  assert.equal(relay.state.devices.has('c1'), false);
  assert.equal(relay.state.deviceKeys.has('c1'), false);

  // Crews that exist take devices as before
  assert.equal((await c.joinCrew(first.crew_code, 'c1', 'C')).type, 'crew_joined');
  assert.equal((await c.createCrew('c1', 'C', 'LEELOO-BBBB')).type, 'crew_created');
});

// --- 5. Limiter (attack a4) ---

test('IPv6 clients are limited by their /64', async () => {
  assert.equal(ipLimitKey('203.0.113.7'), 'ip:203.0.113.7');
  assert.equal(ipLimitKey('::ffff:203.0.113.7'), 'ip:203.0.113.7');
  assert.equal(ipLimitKey('2001:db8:1:2:3:4:5:6'), 'ip:2001:db8:1:2::/64');
  assert.equal(ipLimitKey('2001:DB8:1:2::9'), 'ip:2001:db8:1:2::/64');
  assert.equal(ipLimitKey('2001:db8:1::a:b'), 'ip:2001:db8:1:0::/64');
  assert.equal(ipLimitKey('2001:db8::1'), 'ip:2001:db8:0:0::/64');
  assert.equal(ipLimitKey('::1'), 'ip:0:0:0:0::/64');
  assert.equal(ipLimitKey('fe80::1%eth0'), 'ip:fe80:0:0:0::/64');
  assert.equal(ipLimitKey('unknown'), 'ip:unknown');

  const relay = await relayUp({ joinMaxFailures: 3 });
  for (let i = 0; i < 3; i++) {
    const guesser = await connect(relay, from(`2001:db8:1:2::${i + 1}`));
    assert.equal((await guesser.joinCrew('LEELOO-NOPE', `g${i}`, 'G')).error, 'invalid_crew_code');
  }
  const sameNet = await connect(relay, from('2001:db8:1:2:ffff:ffff:ffff:ffff'));
  assert.equal((await sameNet.joinCrew('LEELOO-NOPE', 'g9', 'G')).error, 'rate_limited');
  const otherNet = await connect(relay, from('2001:db8:1:3::1'));
  assert.equal((await otherNet.joinCrew('LEELOO-NOPE', 'g9', 'G')).error, 'invalid_crew_code');
  assert.equal(relay.state.joinFailures.size, 2);
});

test('a limiter flood sweeps at most one time per second', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const stuff = () => {
    // 30,000 keys whose failures are older than the window: a sweep removes them
    for (let i = 0; i < 30000; i++) relay.state.joinFailures.set(`ip:10.9.${i >> 8}.${i & 255}`, [clock() - 3600000]);
  };
  const guesser = await connect(relay, from('192.0.2.200'));
  const fail = async () => assert.equal((await guesser.joinCrew('LEELOO-NOPE', 'g1', 'G')).error, 'invalid_crew_code');

  clock.advance(5000);
  stuff();
  await fail();
  assert.equal(relay.state.joinFailures.size, 1, 'over the cap: swept');

  stuff();
  await fail();
  await fail();
  assert.equal(relay.state.joinFailures.size, 30001, 'same second: not swept again');

  clock.advance(1000);
  await fail();
  assert.equal(relay.state.joinFailures.size, 1);
});

test('the relay listens on 127.0.0.1 by default', async () => {
  const relay = track(createRelay({ dataFile: null, log: quietLog, sourceTickMs: 0 }));
  assert.equal(relay.host, process.env.HOST || '127.0.0.1');
  await relay.listen(0);
  assert.equal(relay.server.address().address, process.env.HOST || '127.0.0.1');

  const open = track(createRelay({ dataFile: null, log: quietLog, sourceTickMs: 0, host: '0.0.0.0' }));
  await open.listen(0);
  assert.equal(open.server.address().address, '0.0.0.0');
});
