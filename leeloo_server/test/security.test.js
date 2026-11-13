// Device identity (device_key), limits for strangers, agent links and crews
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const WebSocket = require('ws');
const { Client, startRelay, tempDataFile, fakeClock, deviceKeys } = require('./helpers.js');

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

// Crew with Vic (v1, creator) and Mallory (m1), both online.
async function crewOfTwo(relay) {
  const vic = await connect(relay);
  const created = await vic.createCrew('v1', 'Vic');
  const mallory = await connect(relay);
  const joined = await mallory.joinCrew(created.crew_code, 'm1', 'Mallory');
  await vic.next('member_joined');
  return { vic, mallory, code: created.crew_code, created, joined };
}

async function waitFor(check, what) {
  for (let i = 0; i < 200; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timeout waiting for ${what}`);
}

async function connectAgent(relay, client) {
  client.send({ type: 'agent_connect_request' });
  const { code } = await client.next('agent_connect');
  const res = await fetch(`${relay.httpUrl}/connect/${code}`, { method: 'POST' });
  assert.equal(res.status, 200);
  return /\/mcp\/([0-9a-f]{64})/.exec(await res.text())[1];
}

async function callTool(relay, token, name, args = {}) {
  const res = await fetch(`${relay.httpUrl}/mcp/${token}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } })
  });
  if (res.status !== 200) return { status: res.status };
  const result = (await res.json()).result;
  return { status: 200, isError: result.isError === true, text: result.content[0].text };
}

const HOST = 'https://site.api.espn.com';
function packWith(urls, id = 'p') {
  return {
    pack: 1,
    id,
    name: 'P',
    sources: urls.map((url, i) => ({ id: `s${i}`, url, every: 60, fields: { v: 'value' } }))
  };
}

// fetch stand-in: every URL replies { value: <its path> }
function fakeFetch() {
  const fake = async (url) => {
    fake.calls.push(String(url));
    return new Response(JSON.stringify({ value: new URL(url).pathname, other: 'more', big: 'x'.repeat(5000) }), { status: 200 });
  };
  fake.calls = [];
  return fake;
}

// The last pack_data after everything that is running has ended
async function lastPackData(relay, client) {
  await relay.pollSources();
  const all = await client.drain('pack_data');
  return all[all.length - 1];
}

// --- 1. Device identity ---

test('the first bind of a device id gives a device_key, one time', async () => {
  const relay = await relayUp();
  const vic = await connect(relay);
  vic.send({ type: 'create_crew', device_id: 'v1', display_name: 'Vic' });
  const created = await vic.next();
  assert.equal(created.type, 'crew_created');
  assert.match(created.device_key, /^[0-9a-f]{64}$/);
  const code = created.crew_code;

  // Only the hash is kept, in memory and in the file
  assert.equal(relay.state.deviceKeys.size, 1);
  assert.notEqual(relay.state.deviceKeys.get('v1'), created.device_key);
  relay.flush();
  const file = fs.readFileSync(relay.dataFile, 'utf8');
  assert.ok(!file.includes(created.device_key));
  assert.ok(file.includes(relay.state.deviceKeys.get('v1')));

  // The socket that is bound needs no key again, and gets no new one
  vic.send({ type: 'join_crew', crew_code: code, device_id: 'v1', display_name: 'Vic' });
  const again = await vic.next();
  assert.equal(again.type, 'crew_joined');
  assert.equal('device_key' in again, false);

  // A new socket with the key: accepted, the key is not repeated
  const vic2 = await connect(relay);
  vic2.send({ type: 'join_crew', crew_code: code, device_id: 'v1', device_key: created.device_key, display_name: 'Vic' });
  const rejoined = await vic2.next();
  assert.equal(rejoined.type, 'crew_joined');
  assert.equal('device_key' in rejoined, false);
  assert.equal(await vic.closed, 4000, 'the old socket of the same device is replaced');

  const vic3 = await connect(relay);
  vic3.send({ type: 'register', device_id: 'v1', device_key: created.device_key, crew_code: code });
  assert.deepEqual(await vic3.next(), { type: 'registered', device_id: 'v1' });

  // An id made by the relay gets a key too. `null` fields are like no fields
  const fresh = await connect(relay);
  fresh.send({ type: 'register', device_id: null, device_key: null, device_name: 'New' });
  const registered = await fresh.next();
  assert.match(registered.device_id, /^leeloo_[0-9a-f]{12}$/);
  assert.match(registered.device_key, /^[0-9a-f]{64}$/);
  assert.notEqual(registered.device_key, created.device_key);
});

test('a crew member cannot take the device id of another member', async () => {
  const relay = await relayUp();
  const { vic, mallory, code } = await crewOfTwo(relay);
  const vicSocket = relay.state.devices.get('v1').ws;

  const attempts = [
    { type: 'register', device_id: 'v1', crew_code: code, device_name: 'Vic' },
    { type: 'join_crew', device_id: 'v1', crew_code: code, display_name: 'Vic' },
    { type: 'create_crew', device_id: 'v1', crew_code: code, display_name: 'Vic' },
    { type: 'register', device_id: 'v1', device_key: 'f'.repeat(64) },
    { type: 'join_crew', device_id: 'v1', crew_code: code, device_key: deviceKeys.get('m1') },
    { type: 'register', device_id: 'v1', device_key: relay.state.deviceKeys.get('v1') } // the stored hash is not the key
  ];
  for (const attempt of attempts) {
    mallory.send(attempt);
    assert.deepEqual(await mallory.next(), {
      type: 'error', error: 'device_key_required', message: 'This device id belongs to another device'
    });
  }

  // Vic was not evicted
  assert.equal(relay.state.devices.get('v1').ws, vicSocket);
  assert.equal(vicSocket.readyState, WebSocket.OPEN);
  await vic.sync();
  assert.deepEqual(await vic.drain(), [], 'no member_joined / member_offline noise');

  // Mallory is still herself: a code she asks for is for HER device
  mallory.send({ type: 'agent_connect_request' });
  const offer = await mallory.next('agent_connect');
  assert.equal(relay.state.connectCodes.get(offer.code).device_id, 'm1');
  assert.equal(relay.state.connectCodes.size, 1);
  mallory.sendMessage('text', { text: 'hi' });
  assert.equal((await vic.next('message')).from_name, 'Mallory');

  // A socket that is not bound gets nothing at all
  const eve = await connect(relay);
  eve.send({ type: 'join_crew', device_id: 'v1', crew_code: code, display_name: 'Vic' });
  assert.equal((await eve.next()).error, 'device_key_required');
  eve.send({ type: 'agent_connect_request' });
  assert.equal((await eve.next()).error, 'not_in_crew');
  eve.send({ type: 'agent_disconnect' });
  eve.sendMessage('text', { text: 'from eve' });
  eve.send({ type: 'pack_active', pack: { pack: 1, id: 'x', name: 'X' } });
  await eve.sync();
  assert.deepEqual(await vic.drain(), []);
  assert.equal(relay.state.connectCodes.size, 1);
  assert.equal(relay.state.devicePacks.size, 0);
  assert.deepEqual(Array.from(relay.state.devices.keys()).sort(), ['m1', 'v1']);
});

test('pending Spotify tokens go only to the device that has the key', async () => {
  const relay = await relayUp();
  const { vic, mallory, code } = await crewOfTwo(relay);
  await vic.close();
  await waitFor(() => !relay.state.devices.has('v1'), 'vic offline');
  relay.state.pendingSpotifyTokens.set('v1', { tokens: { access_token: 'SECRET' }, timestamp: Date.now() });

  mallory.send({ type: 'register', device_id: 'v1', crew_code: code });
  assert.equal((await mallory.next('error')).error, 'device_key_required');
  assert.deepEqual(await mallory.drain('spotify_auth_complete'), []);
  assert.equal(relay.state.pendingSpotifyTokens.has('v1'), true);

  const vicBack = await connect(relay);
  assert.equal((await vicBack.joinCrew(code, 'v1', 'Vic')).type, 'crew_joined');
  assert.equal((await vicBack.next('spotify_auth_complete')).tokens.access_token, 'SECRET');
});

test('a device from before the keys gets one on its first bind; keys survive a restart', async () => {
  const dataFile = tempDataFile();
  fs.writeFileSync(dataFile, JSON.stringify({
    version: 1,
    crews: { 'LEELOO-OLD1': { created_at: 1, telegram_users: [], members: { old1: 'Olga' } } },
    queues: {}
  }));
  const first = await relayUp({ dataFile });
  assert.equal(first.state.deviceKeys.size, 0);

  const olga = await connect(first);
  olga.send({ type: 'join_crew', crew_code: 'LEELOO-OLD1', device_id: 'old1', display_name: 'Olga' });
  const joined = await olga.next();
  assert.equal(joined.type, 'crew_joined');
  assert.match(joined.device_key, /^[0-9a-f]{64}$/);

  // From now on the id is taken
  const thief = await connect(first);
  thief.send({ type: 'join_crew', crew_code: 'LEELOO-OLD1', device_id: 'old1', display_name: 'Olga' });
  assert.equal((await thief.next()).error, 'device_key_required');
  await first.close();

  const second = await relayUp({ dataFile });
  assert.equal(second.state.deviceKeys.size, 1);
  const thief2 = await connect(second);
  thief2.send({ type: 'register', device_id: 'old1', crew_code: 'LEELOO-OLD1' });
  assert.equal((await thief2.next()).error, 'device_key_required');

  const olga2 = await connect(second);
  olga2.send({ type: 'join_crew', crew_code: 'LEELOO-OLD1', device_id: 'old1', device_key: joined.device_key, display_name: 'Olga' });
  const rejoined = await olga2.next();
  assert.equal(rejoined.type, 'crew_joined');
  assert.equal('device_key' in rejoined, false);
});

test('wrong device keys count for the rate limit; the right key always works', async () => {
  const relay = await relayUp({ joinMaxFailures: 3 });
  const { vic, code } = await crewOfTwo(relay);
  const guesser = await connect(relay, { headers: { 'X-Forwarded-For': '203.0.113.50' } });

  for (let i = 0; i < 3; i++) {
    guesser.send({ type: 'register', device_id: 'v1', device_key: String(i).repeat(64) });
    assert.equal((await guesser.next()).error, 'device_key_required');
  }
  guesser.send({ type: 'register', device_id: 'v1', device_key: '9'.repeat(64) });
  assert.equal((await guesser.next()).error, 'rate_limited');
  guesser.send({ type: 'join_crew', crew_code: code, device_id: 'g1', display_name: 'G' });
  assert.equal((await guesser.next()).error, 'rate_limited', 'the address is blocked for joins too');

  // Vic behind the same address is not locked out of his own device
  await vic.close();
  const vicBack = await connect(relay, { headers: { 'X-Forwarded-For': '203.0.113.50' } });
  assert.equal((await vicBack.joinCrew(code, 'v1', 'Vic')).type, 'crew_joined');
});

// --- 2. Limits for strangers ---

test('pack_active is ignored from devices that are not in a crew', async () => {
  const fake = fakeFetch();
  const relay = await relayUp({ fetch: fake });
  const loner = await connect(relay);
  assert.equal((await loner.register('x1', 'Loner')).type, 'registered');
  loner.send({ type: 'pack_active', pack: packWith([`${HOST}/a`]) });
  await loner.sync();

  // On the roster of no crew, but with a crew code on the socket: still no
  const { vic, code } = await crewOfTwo(relay);
  relay.state.crews.get(code).members.delete('v1');
  vic.send({ type: 'pack_active', pack: packWith([`${HOST}/b`]) });
  await vic.sync();

  await relay.pollSources();
  assert.equal(relay.state.devicePacks.size, 0);
  assert.equal(relay.state.sourceCache.size, 0);
  assert.deepEqual(fake.calls, []);
  assert.deepEqual(await loner.drain('pack_data'), []);
});

test('new device ids without a crew are limited per IP', async () => {
  const relay = await relayUp({ joinMaxFailures: 3 });
  const headers = { 'X-Forwarded-For': '203.0.113.60' };
  const flood = await connect(relay, { headers });
  for (let i = 0; i < 3; i++) {
    assert.equal((await flood.register(`n${i}`, 'N')).type, 'registered');
  }
  flood.send({ type: 'register', device_id: 'n3', device_name: 'N' });
  assert.equal((await flood.next()).error, 'rate_limited');
  flood.send({ type: 'register', device_name: 'N' });
  assert.equal((await flood.next()).error, 'rate_limited');
  assert.equal(relay.state.deviceKeys.size, 3);

  // A device that has its key is not a new id: it can always come back
  const known = await connect(relay, { headers });
  assert.equal((await known.register('n1', 'N')).type, 'registered');

  // Another address has its own budget
  const other = await connect(relay, { headers: { 'X-Forwarded-For': '203.0.113.61' } });
  assert.equal((await other.register('o1', 'O')).type, 'registered');
});

test('a crew roster is limited: crew_full', async () => {
  const relay = await relayUp({ maxCrewMembers: 2 });
  const { vic, code } = await crewOfTwo(relay);

  const third = await connect(relay);
  const refused = await third.joinCrew(code, 't1', 'Third');
  assert.equal(refused.type, 'error');
  assert.equal(refused.error, 'crew_full');
  third.send({ type: 'register', device_id: 't1', crew_code: code });
  assert.equal((await third.next()).error, 'crew_full');
  third.send({ type: 'create_crew', device_id: 't1', crew_code: code, display_name: 'Third' });
  assert.equal((await third.next()).error, 'crew_full');
  assert.deepEqual(Array.from(relay.state.crews.get(code).members.keys()), ['v1', 'm1']);
  assert.equal(relay.state.devices.has('t1'), false);
  assert.equal(relay.state.deviceKeys.has('t1'), false, 'no bind, no key');
  assert.deepEqual(await vic.drain('member_joined'), []);

  // Members come back as before
  await vic.close();
  const vicBack = await connect(relay);
  assert.equal((await vicBack.joinCrew(code, 'v1', 'Vic')).type, 'crew_joined');
});

test('stored packs are limited: the oldest is dropped', async () => {
  const relay = await relayUp({ maxDevicePacks: 2 });
  const { vic, mallory, code } = await crewOfTwo(relay);
  const carol = await connect(relay);
  await carol.joinCrew(code, 'c1', 'Carol');

  vic.send({ type: 'pack_active', pack: { pack: 1, id: 'one', name: 'One' } });
  await vic.sync();
  mallory.send({ type: 'pack_active', pack: { pack: 1, id: 'two', name: 'Two' } });
  await mallory.sync();
  carol.send({ type: 'pack_active', pack: { pack: 1, id: 'three', name: 'Three' } });
  await carol.sync();
  assert.deepEqual(Array.from(relay.state.devicePacks.keys()), ['m1', 'c1']);

  // A changed pack counts as new
  mallory.send({ type: 'pack_active', pack: { pack: 1, id: 'four', name: 'Four' } });
  await mallory.sync();
  vic.send({ type: 'pack_active', pack: { pack: 1, id: 'five', name: 'Five' } });
  await vic.sync();
  assert.deepEqual(Array.from(relay.state.devicePacks.keys()), ['m1', 'v1']);
});

test('source URLs are limited per crew: the extra ones are not polled', async () => {
  const fake = fakeFetch();
  const lines = [];
  const log = { log: (...args) => lines.push(args.join(' ')), error() {} };
  const clock = fakeClock();
  const relay = await relayUp({ fetch: fake, log, now: clock, maxCrewSourceUrls: 2 });
  const { vic, mallory } = await crewOfTwo(relay);

  vic.send({ type: 'pack_active', pack: packWith([`${HOST}/a`, `${HOST}/b`, `${HOST}/c`]) });
  await vic.sync();
  const data = await lastPackData(relay, vic);
  assert.equal(data.pack_id, 'p');
  assert.deepEqual(data.values, { 's0.v': '/a', 's1.v': '/b' });
  assert.deepEqual(fake.calls.sort(), [`${HOST}/a`, `${HOST}/b`]);
  assert.equal(relay.state.devicePacks.get('v1').sources.length, 3, 'the pack itself is kept');

  // Another member of the same crew: a URL the crew has is fine, a new one is not
  mallory.send({ type: 'pack_active', pack: packWith([`${HOST}/b`, `${HOST}/d`], 'q') });
  await mallory.sync();
  assert.deepEqual((await lastPackData(relay, mallory)).values, { 's0.v': '/b' });

  clock.advance(60000);
  await relay.pollSources();
  assert.deepEqual(fake.calls.sort(), [`${HOST}/a`, `${HOST}/a`, `${HOST}/b`, `${HOST}/b`]);
  assert.equal(lines.filter((l) => l.includes('Not polled') && l.includes('/c')).length, 1, 'logged once');
  assert.equal(lines.filter((l) => l.includes('Not polled') && l.includes('/d')).length, 1);

  // Vic goes back to the default look: there is room for Mallory's URL now
  vic.send({ type: 'pack_active', pack: null });
  await vic.sync();
  clock.advance(60000);
  await relay.pollSources();
  assert.deepEqual((await lastPackData(relay, mallory)).values, { 's0.v': '/b', 's1.v': '/d' });
});

test('source URLs are limited for all crews together', async () => {
  const fake = fakeFetch();
  const relay = await relayUp({ fetch: fake, maxSourceUrls: 2 });
  const packs = [[`${HOST}/a`], [`${HOST}/b`], [`${HOST}/c`, `${HOST}/a`]];
  const clients = [];
  for (let i = 0; i < packs.length; i++) {
    const client = await connect(relay);
    await client.createCrew(`d${i}`, `D${i}`);
    client.send({ type: 'pack_active', pack: packWith(packs[i]) });
    await client.sync();
    clients.push(client);
  }
  // The third crew: /c is one URL too many, /a is polled anyway
  assert.deepEqual((await lastPackData(relay, clients[2])).values, { 's1.v': '/a' });
  assert.deepEqual(fake.calls.sort(), [`${HOST}/a`, `${HOST}/b`]);
  assert.equal(relay.state.sourceCache.size, 2);
});

test('only the values that packs ask for are kept, not the replies', async () => {
  const fake = fakeFetch();
  const clock = fakeClock();
  const relay = await relayUp({ fetch: fake, now: clock });
  const { vic, mallory } = await crewOfTwo(relay);

  vic.send({ type: 'pack_active', pack: packWith([`${HOST}/a`]) });
  assert.deepEqual((await vic.next('pack_data')).values, { 's0.v': '/a' });
  const entry = relay.state.sourceCache.get(`${HOST}/a`);
  assert.deepEqual(Array.from(entry.values.entries()), [['value', '/a']]);
  assert.ok(JSON.stringify(Array.from(entry.values)).length < 100);
  assert.equal('data' in entry, false);

  // A new field of the same URL: not in the cache. It is fetched after a
  // short wait (a device cannot make the relay fetch at once, again and again)
  const more = packWith([`${HOST}/a`], 'more');
  more.sources[0].fields = { v: 'value', o: 'other', none: 'nothing.here' };
  mallory.send({ type: 'pack_active', pack: more });
  assert.deepEqual((await mallory.next('pack_data')).values, { 's0.v': '/a' });
  await relay.pollSources();
  assert.equal(fake.calls.length, 1);

  clock.advance(10000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 2);
  assert.deepEqual((await mallory.next('pack_data')).values, { 's0.v': '/a', 's0.o': 'more' });
  // "nothing.here" was asked and is not there: no fetch for it again
  clock.advance(10000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 2);
});

test('a device cannot change its pack without end', async () => {
  const fake = fakeFetch();
  const clock = fakeClock();
  const relay = await relayUp({ fetch: fake, now: clock });
  const { vic } = await crewOfTwo(relay);

  for (let i = 0; i < 25; i++) {
    vic.send({ type: 'pack_active', pack: packWith([`${HOST}/n${i}`], `p${i}`) });
  }
  await vic.sync();
  await relay.pollSources();
  assert.equal(relay.state.devicePacks.get('v1').id, 'p19');
  assert.equal(fake.calls.length, 20);

  clock.advance(10 * 60000);
  vic.send({ type: 'pack_active', pack: packWith([`${HOST}/later`], 'later') });
  await vic.sync();
  assert.equal(relay.state.devicePacks.get('v1').id, 'later');
});

// --- 3. Agent links and crew membership ---

test('a device that moves to another crew loses its agent links', async () => {
  const relay = await relayUp();
  const { vic, mallory, code } = await crewOfTwo(relay);
  const token = await connectAgent(relay, vic);
  vic.send({ type: 'agent_connect_request' });
  const open = await vic.next('agent_connect');
  assert.equal((await callTool(relay, token, 'send_message', { text: 'before' })).isError, false);
  assert.equal((await mallory.next('message')).payload.text, 'before');

  // Vic makes a new crew with the same device
  const moved = await vic.createCrew('v1', 'Vic', 'LEELOO-NEW2');
  assert.equal(moved.type, 'crew_created');
  assert.equal(relay.state.crews.get(code).members.has('v1'), false, 'left the old roster');
  assert.equal(relay.state.memberCrew.get('v1'), 'LEELOO-NEW2');
  assert.equal(relay.state.agentTokens.size, 0);

  const after = await callTool(relay, token, 'send_message', { text: 'after' });
  assert.equal(after.status, 401);
  assert.equal((await callTool(relay, token, 'get_status')).status, 401);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${open.code}`, { method: 'POST' })).status, 404);
  assert.deepEqual(await mallory.drain('message'), []);

  // Messages for the old crew are not kept for the device any more
  mallory.sendMessage('text', { text: 'old crew only' });
  await mallory.sync();
  assert.equal(relay.state.queues.has('v1'), false);

  // A link made in the new crew works
  const fresh = await connectAgent(relay, vic);
  assert.equal(JSON.parse((await callTool(relay, fresh, 'get_status')).text).owner, 'Vic');
});

test('a link made for another crew than the device is in gives "connect again"', async () => {
  const relay = await relayUp();
  const { vic, mallory, code } = await crewOfTwo(relay);
  const token = await connectAgent(relay, vic);
  // As after a state file from before this rule: on the old roster, but in a new crew
  relay.state.memberCrew.set('v1', 'LEELOO-ELSE');

  for (const [name, args] of [['get_status', {}], ['send_message', { text: 'hi' }], ['share_pack', { pack: { pack: 1, id: 'a', name: 'A' } }]]) {
    const result = await callTool(relay, token, name, args);
    assert.equal(result.isError, true, name);
    assert.match(result.text, /connect my agent/);
  }
  assert.deepEqual(await mallory.drain('message'), []);
  assert.equal(relay.state.crews.get(code).members.has('v1'), true);
});

// --- 5. install_pack / reset_pack limit ---

test('install_pack and reset_pack are limited per link', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { vic, mallory } = await crewOfTwo(relay);
  const token = await connectAgent(relay, vic);
  const other = await connectAgent(relay, mallory);
  const pack = { pack: 1, id: 'a', name: 'A' };

  for (let i = 0; i < 5; i++) {
    assert.equal((await callTool(relay, token, 'install_pack', { pack })).isError, false);
    assert.equal((await callTool(relay, token, 'reset_pack')).isError, false);
  }
  // Invalid packs do not use the budget, and are not what the limit reports
  assert.match((await callTool(relay, token, 'install_pack', { pack: { pack: 1 } })).text, /not valid/);

  for (const [name, args] of [['install_pack', { pack }], ['reset_pack', {}]]) {
    const blocked = await callTool(relay, token, name, args);
    assert.equal(blocked.isError, true);
    assert.match(blocked.text, /Too many pack changes \(limit: 10 installs and resets in 10 minutes\)/);
  }
  assert.equal((await vic.drain('pack_install')).length, 5);
  assert.equal((await vic.drain('pack_reset')).length, 5);

  // Other tools and other links go on
  assert.equal((await callTool(relay, token, 'validate_pack', { pack })).isError, false);
  assert.equal((await callTool(relay, token, 'send_message', { text: 'hi' })).isError, false);
  assert.equal((await callTool(relay, other, 'install_pack', { pack })).isError, false);

  clock.advance(10 * 60000);
  assert.equal((await callTool(relay, token, 'install_pack', { pack })).isError, false);
});
