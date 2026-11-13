// Packs and agent connect (docs/PACKS.md)
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {
  createRelay, validatePack, packWarnings, extractValue, PACK_GUIDE, EXAMPLE_PACK, QUEUEABLE_MSG_TYPES
} = require('../server.js');
const { Client, startRelay, tempDataFile, fakeClock, quietLog } = require('./helpers.js');

const ROOT = path.join(__dirname, '..', '..');
const examplePack = () => JSON.parse(fs.readFileSync(path.join(ROOT, 'packs', 'unc-hoops.json'), 'utf8'));

const cleanup = [];
function track(item) {
  cleanup.push(item);
  return item;
}
afterEach(async () => {
  while (cleanup.length) await cleanup.pop().close();
});

async function relayUp(options) {
  // sourceTickMs 0: no timer, the tests call pollSources()
  return track(await startRelay({ sourceTickMs: 0, ...options }));
}
async function connect(relay) {
  return track(await Client.open(relay.wsUrl));
}

// Crew with Alice (a1, creator) and Bob (b1) both online.
async function crewOfTwo(relay) {
  const alice = await connect(relay);
  const created = await alice.createCrew('a1', 'Alice');
  const bob = await connect(relay);
  await bob.joinCrew(created.crew_code, 'b1', 'Bob');
  await alice.next('member_joined');
  return { alice, bob, code: created.crew_code };
}

async function requestCode(client) {
  client.send({ type: 'agent_connect_request' });
  return client.next('agent_connect');
}

// The whole connect flow. Returns the token of the connector URL.
async function connectAgent(relay, client) {
  const { code } = await requestCode(client);
  const res = await fetch(`${relay.httpUrl}/connect/${code}`, { method: 'POST' });
  assert.equal(res.status, 200);
  const match = /\/mcp\/([0-9a-f]{64})/.exec(await res.text());
  assert.ok(match, 'the page shows the connector URL');
  return match[1];
}

async function mcpRaw(relay, token, body) {
  const res = await fetch(`${relay.httpUrl}/mcp/${token}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: typeof body === 'string' ? body : JSON.stringify(body)
  });
  const text = await res.text();
  return { status: res.status, type: res.headers.get('content-type') || '', text, body: text ? JSON.parse(text) : null };
}

function rpc(relay, token, method, params, id = 1) {
  const message = { jsonrpc: '2.0', id, method };
  if (params !== undefined) message.params = params;
  return mcpRaw(relay, token, message);
}

async function callTool(relay, token, name, args = {}) {
  const res = await rpc(relay, token, 'tools/call', { name, arguments: args });
  assert.equal(res.status, 200);
  assert.ok(res.body.result, `tools/call ${name} gives a result: ${res.text}`);
  const result = res.body.result;
  assert.equal(result.content[0].type, 'text');
  return { isError: result.isError === true, text: result.content[0].text };
}

function smallPack(overrides = {}) {
  return { pack: 1, id: 'small', name: 'Small', theme: { header: 'HELLO' }, ...overrides };
}

const SOURCE_URL = 'https://site.api.espn.com/apis/test/team';
function sourcePack(overrides = {}) {
  return {
    pack: 1,
    id: 'live',
    name: 'Live',
    sources: [{
      id: 'game',
      url: SOURCE_URL,
      every: 60,
      fields: {
        state: 'team.events.0.status.state',
        score: 'team.events.0.score',
        period: 'team.events.0.period',
        long: 'team.long',
        missing: 'team.events.5.status.state',
        whole: 'team.events'
      }
    }],
    panels: [{ slot: 'weather', rows: [{ left: '{game.state}', right: '{game.score}' }] }],
    ...overrides
  };
}

function sourceReply(state = 'in', score = '71') {
  return {
    team: {
      long: 'x'.repeat(50),
      events: [{ status: { state }, score: { value: 71.0, displayValue: score }, period: 2 }]
    }
  };
}

// fetch stand-in for the sources. `reply` can be changed by the test.
function fakeFetch() {
  const fake = async (url, init) => {
    fake.calls.push(String(url));
    fake.inits.push(init);
    if (fake.fail) throw new Error('network down');
    return new Response(JSON.stringify(fake.reply), { status: 200 });
  };
  fake.calls = [];
  fake.inits = [];
  fake.fail = false;
  fake.reply = sourceReply();
  return fake;
}

async function waitFor(check, what) {
  for (let i = 0; i < 200; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timeout waiting for ${what}`);
}

// --- Validator ---

test('validator accepts the example pack; the embedded guide and example match the docs', () => {
  assert.deepEqual(validatePack(examplePack()), []);
  assert.deepEqual(packWarnings(examplePack()), []);
  assert.deepEqual(EXAMPLE_PACK, examplePack(), 'EXAMPLE_PACK in server.js must be the same as packs/unc-hoops.json');

  const doc = fs.readFileSync(path.join(ROOT, 'docs', 'PACKS.md'), 'utf8');
  const section = doc.slice(doc.indexOf('## 1. '), doc.indexOf('## 2. ')).trimEnd();
  assert.equal(PACK_GUIDE, section, 'PACK_GUIDE in server.js must be the same as docs/PACKS.md section 1');

  assert.deepEqual(validatePack({ pack: 1, id: 'a', name: 'A' }), [], 'smallest pack');
  assert.deepEqual(validatePack(smallPack({
    panels: [
      { slot: 'weather', builtin: 'weather', when: { field: 'a.b', exists: false }, priority: -100 },
      { slot: 'weather', rows: [{ left: '', bar: { value: '{a.b}', max: 0.5 } }] }
    ],
    rotate_seconds: 5
  })), []);
});

test('validator rejects bad packs with the path of the bad value', () => {
  const edit = (change) => {
    const pack = examplePack();
    change(pack);
    return pack;
  };
  const row = (r) => smallPack({ panels: [{ slot: 'weather', rows: [r] }] });
  const source = (s) => smallPack({
    sources: [{ id: 'game', url: 'https://site.api.espn.com/x', every: 60, fields: { a: 'b' }, ...s }]
  });

  const table = [
    ['not an object', [1, 2], 'pack: must be a JSON object'],
    ['unknown top key', smallPack({ script: 'rm -rf /' }), /^script: unknown key/],
    ['unknown theme key', smallPack({ theme: { font: 'x' } }), /^theme\.font: unknown key/],
    ['unknown row key', row({ left: 'a', color: 'red' }), /^panels\[0\]\.rows\[0\]\.color: unknown key/],
    ['unknown source key', source({ headers: {} }), /^sources\[0\]\.headers: unknown key/],
    ['wrong version', smallPack({ pack: 2 }), 'pack: must be the number 1'],
    ['version as text', smallPack({ pack: '1' }), 'pack: must be the number 1'],
    ['bad id', smallPack({ id: 'UNC Hoops' }), /^id: must match/],
    ['no name', { pack: 1, id: 'a' }, /^name: required/],
    ['long name', smallPack({ name: 'x'.repeat(25) }), 'name: longer than 24 characters'],
    ['bad colour', smallPack({ theme: { colors: { bg: 'blue' } } }), 'theme.colors.bg: must be a colour like #1A2B3C'],
    ['short colour', smallPack({ theme: { colors: { tan: '#FFF' } } }), /^theme\.colors\.tan: must be a colour/],
    ['unknown colour key', smallPack({ theme: { colors: { pink: '#FFFFFF' } } }), /^theme\.colors\.pink: unknown key/],
    ['long header', smallPack({ theme: { header: 'x'.repeat(13) } }), 'theme.header: longer than 12 characters'],
    ['empty label', smallPack({ theme: { labels: { time: '' } } }), 'theme.labels.time: must not be empty'],
    ['unknown label', smallPack({ theme: { labels: { clock: 'x' } } }), /^theme\.labels\.clock: unknown key/],
    ['4 sources', smallPack({
      sources: ['a', 'b', 'c', 'd'].map((id) => ({ id, url: 'https://site.api.espn.com/x', every: 60, fields: { a: 'b' } }))
    }), 'sources: more than 3 sources'],
    ['bad host', source({ url: 'https://evil.example.com/data' }), /^sources\[0\]\.url: host evil\.example\.com is not allowed/],
    ['host look-alike', source({ url: 'https://site.api.espn.com.evil.com/x' }), /^sources\[0\]\.url: host /],
    ['user in url', source({ url: 'https://site.api.espn.com@evil.com/x' }), /^sources\[0\]\.url: /],
    ['http url', source({ url: 'http://site.api.espn.com/x' }), 'sources[0].url: must start with https://'],
    ['url with port', source({ url: 'https://site.api.espn.com:8443/x' }), 'sources[0].url: must not contain a port'],
    ['fast source', source({ every: 5 }), 'sources[0].every: must be from 30 to 3600'],
    ['bad source id', source({ id: 'Game' }), /^sources\[0\]\.id: must match/],
    ['same source id', smallPack({
      sources: [0, 1].map(() => ({ id: 'game', url: 'https://site.api.espn.com/x', every: 60, fields: { a: 'b' } }))
    }), /^sources\[1\]\.id: "game" is used by more than one source/],
    ['no fields', source({ fields: {} }), 'sources[0].fields: needs at least 1 entry'],
    ['13 fields', source({
      fields: Object.fromEntries(Array.from({ length: 13 }, (_, i) => [`f${i}`, 'a']))
    }), 'sources[0].fields: more than 12 entries'],
    ['bad field key', source({ fields: { 'Home Score': 'a' } }), /^sources\[0\]\.fields\.Home Score: key must match/],
    ['long path', source({ fields: { a: 'x'.repeat(201) } }), 'sources[0].fields.a: longer than 200 characters'],
    ['7 panels', smallPack({
      panels: Array.from({ length: 7 }, () => ({ slot: 'weather', builtin: 'weather' }))
    }), 'panels: more than 6 panels'],
    ['wrong slot', smallPack({ panels: [{ slot: 'time', builtin: 'weather' }] }), 'panels[0].slot: must be "weather"'],
    ['no rows', smallPack({ panels: [{ slot: 'weather', title: 'x' }] }), /^panels\[0\]\.rows: required/],
    ['4 rows', smallPack({
      panels: [{ slot: 'weather', rows: [1, 2, 3, 4].map(() => ({ left: 'a' })) }]
    }), 'panels[0].rows: more than 3 rows'],
    ['long title', smallPack({ panels: [{ slot: 'weather', title: 'x'.repeat(11), rows: [{ left: 'a' }] }] }),
      'panels[0].title: longer than 10 characters'],
    ['long text', edit((p) => { p.panels[1].rows[0].left = 'x'.repeat(29); }),
      'panels[1].rows[0].left: longer than 28 characters'],
    ['long right', row({ left: 'a', right: 'x'.repeat(15) }), 'panels[0].rows[0].right: longer than 14 characters'],
    ['long right with a value', row({ left: 'a', right: '{a.b} ' + 'x'.repeat(14) }), 'panels[0].rows[0].right: longer than 14 characters'],
    ['left is not text', row({ left: 5 }), 'panels[0].rows[0].left: must be a string'],
    ['no left', row({ right: 'a' }), 'panels[0].rows[0].left: required'],
    ['right and bar', row({ left: 'a', right: 'b', bar: { value: '{a.b}', max: 10 } }),
      'panels[0].rows[0]: has right and bar, use only one of them'],
    ['bar max 0', row({ left: 'a', bar: { value: '{a.b}', max: 0 } }), /^panels\[0\]\.rows\[0\]\.bar\.max: /],
    ['bad when: two ops', edit((p) => { p.panels[0].when.exists = true; }),
      'panels[0].when: needs exactly one of equals, not_equals, exists'],
    ['bad when: no op', edit((p) => { p.panels[0].when = { field: 'game.state' }; }),
      'panels[0].when: needs exactly one of equals, not_equals, exists'],
    ['bad when: field', edit((p) => { p.panels[0].when.field = ''; }), /^panels\[0\]\.when\.field: required/],
    ['bad when: no field', edit((p) => { delete p.panels[0].when.field; }), /^panels\[0\]\.when\.field: required/],
    ['upper case scheme', source({ url: 'HTTPS://site.api.espn.com/x' }), 'sources[0].url: must start with https://'],
    ['bad when: unknown op', edit((p) => { p.panels[0].when.contains = 'x'; }), /^panels\[0\]\.when\.contains: unknown key/],
    ['bad when: exists as text', edit((p) => { p.panels[0].when = { field: 'game.state', exists: 'yes' }; }),
      'panels[0].when.exists: must be true or false'],
    ['priority 101', edit((p) => { p.panels[0].priority = 101; }), 'panels[0].priority: must be from -100 to 100'],
    ['priority 1.5', edit((p) => { p.panels[0].priority = 1.5; }), 'panels[0].priority: must be an integer'],
    ['builtin with rows', smallPack({ panels: [{ slot: 'weather', builtin: 'weather', rows: [{ left: 'a' }] }] }),
      /^panels\[0\]\.rows: unknown key/],
    ['slow rotation', smallPack({ rotate_seconds: 601 }), 'rotate_seconds: must be from 5 to 600'],
    ['oversize', smallPack({
      panels: Array.from({ length: 6 }, () => ({ slot: 'weather', rows: [{ left: 'a', bar: { value: 'x'.repeat(1500), max: 1 } }] }))
    }), /^pack: JSON text is \d+ bytes, the limit is 7800$/]
  ];

  for (const [label, pack, expected] of table) {
    const errors = validatePack(pack);
    const hit = errors.some((e) => (expected instanceof RegExp ? expected.test(e) : e === expected));
    assert.ok(hit, `${label}: expected ${expected}, got ${JSON.stringify(errors)}`);
  }

  // Lone surrogates cannot be stored as UTF-8 on the device
  assert.deepEqual(validatePack(smallPack({ name: 'a\uD83Db' })), ['name: has an invalid character']);
  assert.deepEqual(validatePack(smallPack({ panels: [{ slot: 'weather', rows: [{ left: 'ok' }, { left: 'x\uDE00' }] }] })),
    ['panels[0].rows[1].left: has an invalid character']);
  assert.deepEqual(validatePack(JSON.parse('{"pack":1,"id":"a","name":"A","theme":{"labels":{"ti\\udc00me":"x"}}}')),
    ['theme.labels: has an invalid character']);
  assert.deepEqual(validatePack(smallPack({ name: 'ok \uD83D\uDE00' })), [], 'a full pair is a normal character');

  // Readable screen rule
  const black = Object.fromEntries(['bg', 'green', 'purple', 'lavender', 'tan', 'white'].map((k) => [k, '#000000']));
  assert.deepEqual(validatePack(smallPack({ theme: { colors: black } })),
    ['green', 'purple', 'lavender', 'tan', 'white'].map(
      (k) => `theme.colors.${k}: too close to the background colour (contrast 1.00, need 2.0)`));
  const lightBg = validatePack(smallPack({ theme: { colors: { bg: '#FFFFFF' } } }));
  assert.equal(lightBg.length, 2, 'a light bg alone hides the default green and white');
  assert.equal(lightBg[1], 'theme.colors.white: too close to the background colour (contrast 1.00, need 2.0)');
  assert.match(lightBg[0], /^theme\.colors\.green: too close to the background colour \(contrast 1\.\d\d, need 2\.0\)$/);
  assert.deepEqual(validatePack(smallPack({
    theme: { colors: { bg: '#FFFFFF', green: '#006633', purple: '#4B3F9E', lavender: '#7A1FA2', tan: '#6B4A12', white: '#111111' } }
  })), []);
  assert.deepEqual(validatePack(smallPack({ theme: { colors: { tan: '#22263A' } } })).map((e) => e.split(':')[0]),
    ['theme.colors.tan']);
  assert.deepEqual(validatePack(smallPack({ theme: { colors: { rose: '#1A1D2E', yellow: '#1A1D2E' } } })), [],
    'only the five text colours are checked');

  // The relay stops at 7,800 bytes (the device at 8,000)
  const sized = (bytes) => {
    const pack = smallPack({ panels: Array.from({ length: 6 }, () => ({ slot: 'weather', rows: [{ left: 'a', bar: { value: '', max: 1 } }] })) });
    const spare = bytes - Buffer.byteLength(JSON.stringify(pack));
    pack.panels.forEach((panel, i) => { panel.rows[0].bar.value = 'v'.repeat(Math.floor(spare / 6) + (i < spare % 6 ? 1 : 0)); });
    assert.equal(Buffer.byteLength(JSON.stringify(pack)), bytes);
    return pack;
  };
  assert.deepEqual(validatePack(sized(7800)), []);
  assert.deepEqual(validatePack(sized(7801)), ['pack: JSON text is 7801 bytes, the limit is 7800']);

  // The allowed hosts are an option
  const other = smallPack({ sources: [{ id: 'a', url: 'https://data.example.org/x', every: 60, fields: { a: 'b' } }] });
  assert.equal(validatePack(other).length, 1);
  assert.deepEqual(validatePack(other, { sourceHosts: 'data.example.org, api.open-meteo.com' }), []);

  // A {value} placeholder does not count for the length of a template
  assert.deepEqual(validatePack(smallPack({
    panels: [{ slot: 'weather', rows: [{ left: 'x'.repeat(28) + '{game.home_score}', right: '{game.home_score}' + 'x'.repeat(14) }] }]
  })), []);

  // A hint, not an error
  assert.deepEqual(validatePack(smallPack({ panels: [{ slot: 'weather', rows: [{ left: '{nope.x}' }] }] })), []);
  assert.match(packWarnings(smallPack({ panels: [{ slot: 'weather', rows: [{ left: '{nope.x}' }] }] }))[0],
    /^panels\[0\]\.rows\[0\]\.left: \{nope\.x\}/);
});

test('extractValue follows dot paths, array indexes and the displayValue rule', () => {
  const data = { a: { list: [{ n: 7, s: 'text', flag: true, obj: { displayValue: '12-3', value: 12 }, plain: { x: 1 } }], nil: null } };
  assert.equal(extractValue(data, 'a.list.0.n'), '7');
  assert.equal(extractValue(data, 'a.list.0.s'), 'text');
  assert.equal(extractValue(data, 'a.list.0.flag'), 'true');
  assert.equal(extractValue(data, 'a.list.0.obj'), '12-3');
  assert.equal(extractValue(data, 'a.list.0.plain'), undefined);
  assert.equal(extractValue(data, 'a.list.1.n'), undefined);
  assert.equal(extractValue(data, 'a.list.x'), undefined);
  assert.equal(extractValue(data, 'a.list'), undefined);
  assert.equal(extractValue(data, 'a.nil'), undefined);
  assert.equal(extractValue(data, 'a.list.0.s.length'), undefined);
  assert.equal(extractValue(data, 'a.constructor'), undefined);
  assert.equal(extractValue({ t: 'y'.repeat(41) }, 't'), 'y'.repeat(40));
});

// --- Agent connect ---

test('connect flow: code -> page -> connector link works once, wrong token is 401', async () => {
  const relay = await relayUp({ publicBaseUrl: 'https://relay.test/' });
  const { alice } = await crewOfTwo(relay);

  const offer = await requestCode(alice);
  assert.match(offer.code, /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/);
  assert.equal(offer.url, `https://relay.test/connect/${offer.code}`);
  assert.equal(offer.expires_in, 900);

  // The page does not use the code up (link previews fetch it)
  for (let i = 0; i < 2; i++) {
    const page = await fetch(`${relay.httpUrl}/connect/${offer.code}`);
    assert.equal(page.status, 200);
    assert.match(page.headers.get('content-type'), /text\/html/);
    const html = await page.text();
    assert.match(html, /Create my connector link/);
    assert.match(html, new RegExp(`method="post" action="/connect/${offer.code}"`));
    assert.match(html, /Alice/);
  }

  const redeemed = await fetch(`${relay.httpUrl}/connect/${offer.code}`, { method: 'POST' });
  assert.equal(redeemed.status, 200);
  assert.equal(redeemed.headers.get('cache-control'), 'no-store');
  const html = await redeemed.text();
  const match = /https:\/\/relay\.test\/mcp\/([0-9a-f]{64})/.exec(html);
  assert.ok(match, 'connector URL with the public base URL');
  assert.match(html, /Do not share it/);
  assert.match(html, /navigator\.clipboard\.writeText/);
  const token = match[1];

  // Only the hash is kept
  assert.equal(relay.state.agentTokens.size, 1);
  assert.ok(!JSON.stringify(Array.from(relay.state.agentTokens.entries())).includes(token));
  relay.flush();
  assert.ok(!fs.readFileSync(relay.dataFile, 'utf8').includes(token), 'the token is not in the state file');

  const ok = await rpc(relay, token, 'ping');
  assert.equal(ok.status, 200);
  assert.deepEqual(ok.body, { jsonrpc: '2.0', id: 1, result: {} });

  // One use
  assert.equal((await fetch(`${relay.httpUrl}/connect/${offer.code}`, { method: 'POST' })).status, 404);
  const gone = await fetch(`${relay.httpUrl}/connect/${offer.code}`);
  assert.equal(gone.status, 404);
  assert.match(await gone.text(), /connect my agent/);

  // Wrong tokens
  for (const bad of ['0'.repeat(64), 'nope', token.slice(0, 63) + (token[63] === 'a' ? 'b' : 'a')]) {
    const res = await rpc(relay, bad, 'ping');
    assert.equal(res.status, 401);
    assert.ok(res.body.error.message);
  }

  const get = await fetch(`${relay.httpUrl}/mcp/${token}`);
  assert.equal(get.status, 405);
  assert.equal(get.headers.get('allow'), 'POST');
});

test('connect codes: expire after 15 minutes, need a crew, a new one replaces the old one', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { alice } = await crewOfTwo(relay);

  const first = await requestCode(alice);
  const second = await requestCode(alice);
  assert.notEqual(first.code, second.code);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${first.code}`)).status, 404, 'replaced');

  clock.advance(15 * 60000 - 1);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${second.code}`)).status, 200);
  clock.advance(1);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${second.code}`)).status, 404);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${second.code}`, { method: 'POST' })).status, 404);
  assert.equal(relay.state.agentTokens.size, 0);

  // Lower case works (people type codes)
  const third = await requestCode(alice);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${third.code.toLowerCase()}`)).status, 200);

  // A device without a crew gets no code
  const loner = await connect(relay);
  loner.send({ type: 'register', device_id: 'x1', device_name: 'Loner' });
  await loner.next('registered');
  loner.send({ type: 'agent_connect_request' });
  assert.equal((await loner.next('error')).error, 'not_in_crew');
});

test('wrong connect codes are rate limited per IP', async () => {
  const relay = await relayUp({ joinMaxFailures: 3 });
  const { alice } = await crewOfTwo(relay);
  const { code } = await requestCode(alice);
  const headers = { 'X-Forwarded-For': '203.0.113.9' };

  for (let i = 0; i < 3; i++) {
    assert.equal((await fetch(`${relay.httpUrl}/connect/WRONG${i}XX`, { headers })).status, 404);
  }
  assert.equal((await fetch(`${relay.httpUrl}/connect/${code}`, { headers })).status, 429);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${code}`, { method: 'POST', headers })).status, 429);
  // Another address is not affected
  assert.equal((await fetch(`${relay.httpUrl}/connect/${code}`, { headers: { 'X-Forwarded-For': '203.0.113.10' } })).status, 200);
});

test('connect pages escape what they show', async () => {
  const relay = await relayUp();
  const alice = await connect(relay);
  await alice.createCrew('a1', '<img src=x onerror=alert(1)>');
  const { code } = await requestCode(alice);
  const html = await (await fetch(`${relay.httpUrl}/connect/${code}`)).text();
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
});

// --- MCP protocol ---

test('MCP: initialize, tools/list, notifications, errors', async () => {
  const relay = await relayUp();
  const { alice } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);

  const init = await rpc(relay, token, 'initialize', {
    protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'test', version: '0' }
  }, 'init-1');
  assert.equal(init.status, 200);
  assert.match(init.type, /^application\/json/);
  assert.equal(init.body.id, 'init-1');
  assert.equal(init.body.result.protocolVersion, '2025-03-26');
  assert.deepEqual(init.body.result.capabilities, { tools: {} });
  assert.equal(init.body.result.serverInfo.name, 'leeloo');
  assert.equal(init.body.result.serverInfo.version, require('../package.json').version);
  assert.match(init.body.result.instructions, /get_pack_guide/);

  const unknownVersion = await rpc(relay, token, 'initialize', { protocolVersion: '1999-01-01' });
  assert.equal(unknownVersion.body.result.protocolVersion, '2025-06-18');

  const list = await rpc(relay, token, 'tools/list', {});
  const tools = list.body.result.tools;
  assert.deepEqual(tools.map((t) => t.name).sort(), [
    'get_pack_guide', 'get_status', 'install_pack', 'reset_pack', 'send_message', 'share_pack', 'validate_pack'
  ]);
  for (const tool of tools) {
    assert.equal(tool.inputSchema.type, 'object', tool.name);
    assert.ok(tool.description.length > 20, tool.name);
  }
  const byName = Object.fromEntries(tools.map((t) => [t.name, t]));
  assert.deepEqual(byName.install_pack.inputSchema.required, ['pack']);
  assert.deepEqual(byName.send_message.inputSchema.required, ['text']);
  assert.equal(byName.send_message.inputSchema.properties.text.maxLength, 500);

  // Notifications: 202, no body
  const note = await mcpRaw(relay, token, { jsonrpc: '2.0', method: 'notifications/initialized' });
  assert.equal(note.status, 202);
  assert.equal(note.text, '');

  const unknown = await rpc(relay, token, 'resources/list', {}, 7);
  assert.equal(unknown.status, 200);
  assert.equal(unknown.body.id, 7);
  assert.equal(unknown.body.error.code, -32601);

  const badJson = await mcpRaw(relay, token, '{"jsonrpc": "2.0", "id": 1, "method": ');
  assert.equal(badJson.body.error.code, -32700);
  assert.equal(badJson.body.id, null);

  const notRpc = await mcpRaw(relay, token, { id: 1, method: 'ping' });
  assert.equal(notRpc.body.error.code, -32600);

  const unknownTool = await rpc(relay, token, 'tools/call', { name: 'rm_rf', arguments: {} });
  assert.equal(unknownTool.body.error.code, -32602);

  // Batch: one reply per request, none for the notification
  const batch = await mcpRaw(relay, token, [
    { jsonrpc: '2.0', id: 1, method: 'ping' },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    { jsonrpc: '2.0', id: 2, method: 'nope' }
  ]);
  assert.equal(batch.status, 200);
  assert.deepEqual(batch.body.map((r) => r.id), [1, 2]);
  assert.equal(batch.body[1].error.code, -32601);
  assert.equal((await mcpRaw(relay, token, [])).body.error.code, -32600);

  // 64 KB limit
  const big = await mcpRaw(relay, token, { jsonrpc: '2.0', id: 1, method: 'ping', params: { x: 'y'.repeat(70000) } });
  assert.equal(big.status, 413);
  assert.equal(big.body.error.code, -32600);
});

test('MCP tools: get_status, get_pack_guide, validate_pack', async () => {
  const relay = await relayUp();
  const { alice, bob } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);

  alice.send({ type: 'pack_active', pack: smallPack() });
  await alice.sync();

  const status = JSON.parse((await callTool(relay, token, 'get_status')).text);
  assert.equal(status.owner, 'Alice');
  assert.equal(status.owner_online, true);
  assert.deepEqual(status.crew_members.sort(), ['Alice', 'Bob']);
  assert.deepEqual(status.online_devices.sort(), ['Alice', 'Bob']);
  assert.deepEqual(status.active_pack, { id: 'small', name: 'Small' });

  await bob.close();
  await waitFor(() => !relay.state.devices.has('b1'), 'bob offline');
  alice.send({ type: 'pack_active', pack: null });
  await alice.sync();
  const later = JSON.parse((await callTool(relay, token, 'get_status')).text);
  assert.deepEqual(later.online_devices, ['Alice']);
  assert.deepEqual(later.crew_members.sort(), ['Alice', 'Bob']);
  assert.equal(later.active_pack, null);

  const guide = await callTool(relay, token, 'get_pack_guide');
  assert.equal(guide.isError, false);
  assert.ok(guide.text.includes(PACK_GUIDE));
  assert.ok(guide.text.includes('"id": "unc-hoops"'));
  assert.ok(guide.text.includes('site.api.espn.com, api.open-meteo.com'));

  const valid = await callTool(relay, token, 'validate_pack', { pack: examplePack() });
  assert.equal(valid.isError, false);
  assert.match(valid.text, /valid/);

  // Agents sometimes send the pack as JSON text
  assert.equal((await callTool(relay, token, 'validate_pack', { pack: JSON.stringify(examplePack()) })).isError, false);

  const bad = examplePack();
  bad.panels[1].rows[0].left = 'x'.repeat(29);
  bad.theme.colors.bg = 'navy';
  const invalid = await callTool(relay, token, 'validate_pack', { pack: bad });
  assert.equal(invalid.isError, true);
  assert.ok(invalid.text.includes('panels[1].rows[0].left: longer than 28 characters'));
  assert.ok(invalid.text.includes('theme.colors.bg: must be a colour like #1A2B3C'));

  assert.equal((await callTool(relay, token, 'validate_pack', {})).isError, true);
});

test('install_pack and reset_pack reach the owner device that is online', async () => {
  const relay = await relayUp();
  const { alice, bob } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);

  const result = await callTool(relay, token, 'install_pack', { pack: examplePack() });
  assert.equal(result.isError, false);
  assert.match(result.text, /online/);
  const install = await alice.next('pack_install');
  assert.deepEqual(install.pack, examplePack());
  assert.equal(install.from, 'agent');

  const bad = await callTool(relay, token, 'install_pack', { pack: smallPack({ name: '' }) });
  assert.equal(bad.isError, true);
  assert.ok(bad.text.includes('name: must not be empty'));

  assert.equal((await callTool(relay, token, 'reset_pack')).isError, false);
  assert.deepEqual(await alice.next('pack_reset'), { type: 'pack_reset' });

  assert.deepEqual(await alice.drain('pack_install'), [], 'the invalid pack was not sent');
  assert.deepEqual((await bob.drain()).filter((m) => m.type.startsWith('pack_')), [], 'only the owner device');
  assert.equal(relay.state.pendingPackOps.size, 0);
});

test('install_pack is queued while the owner device is offline and delivered on rejoin', async () => {
  const relay = await relayUp();
  const { alice, code } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);
  await alice.close();
  await waitFor(() => !relay.state.devices.has('a1'), 'alice offline');

  // Only the newest operation is kept
  assert.match((await callTool(relay, token, 'reset_pack')).text, /offline/);
  const result = await callTool(relay, token, 'install_pack', { pack: smallPack() });
  assert.equal(result.isError, false);
  assert.match(result.text, /offline/);
  assert.match(result.text, /queued/);
  assert.equal(relay.state.pendingPackOps.size, 1);

  const again = await connect(relay);
  const joined = await again.joinCrew(code, 'a1', 'Alice');
  assert.equal(joined.type, 'crew_joined');
  const install = await again.next('pack_install');
  assert.deepEqual(install.pack, smallPack());
  assert.deepEqual(await again.drain('pack_reset'), []);
  assert.equal(relay.state.pendingPackOps.size, 0);

  // Not delivered a second time
  await again.close();
  await waitFor(() => !relay.state.devices.has('a1'), 'alice offline');
  const third = await connect(relay);
  await third.joinCrew(code, 'a1', 'Alice');
  assert.deepEqual(await third.drain('pack_install'), []);
});

test('share_pack offers the pack to the crew, not to the owner; queued for offline members', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);
  const carol = await connect(relay);
  await carol.joinCrew(code, 'c1', 'Carol');
  await carol.close();
  await waitFor(() => !relay.state.devices.has('c1'), 'carol offline');
  const token = await connectAgent(relay, alice);
  await alice.drain();

  const result = await callTool(relay, token, 'share_pack', { pack: examplePack() });
  assert.equal(result.isError, false);
  assert.match(result.text, /1 Leeloo got it now, 1 will get it/);

  const offer = await bob.next('message');
  assert.equal(offer.msg_type, 'pack_offer');
  assert.equal(offer.from_name, 'Alice');
  assert.deepEqual(offer.payload, { pack: examplePack() });

  assert.deepEqual(await alice.drain(), [], 'nothing for the owner device');

  const carolBack = await connect(relay);
  await carolBack.joinCrew(code, 'c1', 'Carol');
  const queued = await carolBack.next('message');
  assert.equal(queued.msg_type, 'pack_offer');
  assert.equal(queued.queued, true);
  assert.deepEqual(queued.payload.pack, examplePack());

  const bad = await callTool(relay, token, 'share_pack', { pack: smallPack({ id: 'Bad Id' }) });
  assert.equal(bad.isError, true);
  assert.deepEqual(await bob.drain('message'), []);
});

test('pack_offer from a device: valid is relayed and queued, invalid is dropped', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);
  assert.ok(QUEUEABLE_MSG_TYPES.has('pack_offer'));

  alice.sendMessage('pack_offer', { pack: smallPack({ junk: true }) });
  alice.sendMessage('pack_offer', { nothing: 1 });
  alice.sendMessage('pack_offer', { pack: smallPack(), extra: 'x'.repeat(100) });
  await alice.sync();

  const offers = await bob.drain('message');
  assert.equal(offers.length, 1);
  assert.equal(offers[0].msg_type, 'pack_offer');
  assert.equal(offers[0].from_name, 'Alice');
  assert.deepEqual(offers[0].payload, { pack: smallPack() });

  await bob.close();
  await waitFor(() => !relay.state.devices.has('b1'), 'bob offline');
  alice.sendMessage('pack_offer', { pack: smallPack() });
  await alice.sync();
  const bobBack = await connect(relay);
  await bobBack.joinCrew(code, 'b1', 'Bob');
  const queued = await bobBack.next('message');
  assert.equal(queued.msg_type, 'pack_offer');
  assert.equal(queued.queued, true);
});

test('send_message arrives as a text message in the owner\'s name', async () => {
  const relay = await relayUp();
  const { alice, bob } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);
  await alice.drain();

  const result = await callTool(relay, token, 'send_message', { text: '  pizza at 8?  ' });
  assert.equal(result.isError, false);
  assert.match(result.text, /1 Leeloo got it now, 0 will get it/);

  const msg = await bob.next('message');
  assert.equal(msg.msg_type, 'text');
  assert.equal(msg.from_name, 'Alice');
  assert.equal(msg.from_device, 'a1');
  assert.deepEqual(msg.payload, { text: 'pizza at 8?' });
  assert.deepEqual(await alice.drain(), []);

  for (const text of ['', '   ', 'x'.repeat(501), 42, undefined]) {
    const bad = await callTool(relay, token, 'send_message', { text });
    assert.equal(bad.isError, true, `text ${JSON.stringify(text)}`);
  }
  assert.equal((await callTool(relay, token, 'send_message', { text: 'x'.repeat(500) })).isError, false);
  assert.equal((await bob.drain('message')).length, 1);
});

test('an agent cannot flood the crew', async () => {
  const clock = fakeClock();
  const relay = await relayUp({ now: clock });
  const { alice, bob } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);

  for (let i = 0; i < 20; i++) {
    assert.equal((await callTool(relay, token, 'send_message', { text: `m${i}` })).isError, false);
  }
  const blocked = await callTool(relay, token, 'send_message', { text: 'one more' });
  assert.equal(blocked.isError, true);
  assert.match(blocked.text, /Too many/);
  assert.equal((await callTool(relay, token, 'share_pack', { pack: smallPack() })).isError, true);
  assert.equal((await bob.drain('message')).length, 20);

  clock.advance(10 * 60000);
  assert.equal((await callTool(relay, token, 'send_message', { text: 'later' })).isError, false);
});

test('agent_disconnect deletes every token of the device (and only of that device)', async () => {
  const relay = await relayUp();
  const { alice, bob } = await crewOfTwo(relay);
  const tokenA1 = await connectAgent(relay, alice);
  const tokenA2 = await connectAgent(relay, alice);
  const tokenB = await connectAgent(relay, bob);
  const open = await requestCode(alice);
  assert.equal((await rpc(relay, tokenA1, 'ping')).status, 200);

  alice.send({ type: 'agent_disconnect' });
  assert.deepEqual(await alice.next('agent_disconnected'), { type: 'agent_disconnected', removed: 2 });

  assert.equal((await rpc(relay, tokenA1, 'ping')).status, 401);
  assert.equal((await rpc(relay, tokenA2, 'tools/list')).status, 401);
  assert.equal((await rpc(relay, tokenB, 'ping')).status, 200);
  assert.equal((await fetch(`${relay.httpUrl}/connect/${open.code}`)).status, 404, 'open codes die too');

  alice.send({ type: 'agent_disconnect' });
  assert.equal((await alice.next('agent_disconnected')).removed, 0);
});

test('tools say "connect again" when the device left the crew roster', async () => {
  const relay = await relayUp();
  const { alice, bob, code } = await crewOfTwo(relay);
  const token = await connectAgent(relay, alice);
  relay.state.crews.get(code).members.delete('a1');

  for (const [name, args] of [['get_status', {}], ['send_message', { text: 'hi' }], ['install_pack', { pack: smallPack() }]]) {
    const result = await callTool(relay, token, name, args);
    assert.equal(result.isError, true, name);
    assert.match(result.text, /connect my agent/);
  }
  assert.deepEqual(await bob.drain('message'), []);
  assert.deepEqual(await alice.drain('pack_install'), []);
});

// --- Sources ---

test('pack_active starts the sources: pack_data with the extracted values', async () => {
  const clock = fakeClock();
  const fake = fakeFetch();
  const relay = await relayUp({ now: clock, fetch: fake });
  const { alice, bob } = await crewOfTwo(relay);

  alice.send({ type: 'pack_active', pack: sourcePack() });
  const data = await alice.next('pack_data');
  assert.equal(data.pack_id, 'live');
  assert.deepEqual(data.values, {
    'game.state': 'in',
    'game.score': '71',          // object with displayValue
    'game.period': '2',          // number -> string
    'game.long': 'x'.repeat(40)  // 40 characters at most
    // game.missing, game.whole: no value
  });
  assert.deepEqual(fake.calls, [SOURCE_URL]);
  assert.equal(fake.inits[0].redirect, 'error');
  assert.ok(fake.inits[0].signal, 'has a timeout');

  // A second device with the same URL (other fields): from the cache, no new fetch
  const bobPack = sourcePack({ id: 'bobs' });
  bobPack.sources[0] = { id: 'g', url: SOURCE_URL, every: 300, fields: { st: 'team.events.0.status.state' } };
  bobPack.panels = [];
  bob.send({ type: 'pack_active', pack: bobPack });
  assert.deepEqual(await bob.next('pack_data'), { type: 'pack_data', pack_id: 'bobs', values: { 'g.st': 'in' } });
  assert.equal(fake.calls.length, 1);

  // Not due yet
  clock.advance(59000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 1);

  // Due (the smallest interval of the two users): ONE fetch, both devices get the new values
  clock.advance(1000);
  fake.reply = sourceReply('post', '88');
  await relay.pollSources();
  await relay.pollSources();
  assert.equal(fake.calls.length, 2);
  const aliceData = await alice.next('pack_data');
  assert.equal(aliceData.values['game.state'], 'post');
  assert.equal(aliceData.values['game.score'], '88');
  assert.deepEqual((await bob.next('pack_data')).values, { 'g.st': 'post' });
  assert.deepEqual(await alice.drain('pack_data'), []);
  assert.deepEqual(await bob.drain('pack_data'), []);

  // Default look: Alice's source stops, Bob's interval (300 s) is the one left
  alice.send({ type: 'pack_active', pack: null });
  await alice.sync();
  assert.equal(relay.state.devicePacks.has('a1'), false);
  clock.advance(60000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 2);
  clock.advance(240000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 3);
  await bob.next('pack_data');
  assert.deepEqual(await alice.drain('pack_data'), []);

  // Nobody connected with that URL: no more fetches
  await bob.close();
  await waitFor(() => !relay.state.devices.has('b1'), 'bob offline');
  clock.advance(3600000);
  await relay.pollSources();
  assert.equal(fake.calls.length, 3);
  assert.equal(relay.state.sourceCache.size, 0);
});

test('a failed fetch keeps the old values and is logged once', async () => {
  const clock = fakeClock();
  const fake = fakeFetch();
  const errors = [];
  const log = { log() {}, error: (...args) => errors.push(args.join(' ')) };
  const relay = await relayUp({ now: clock, fetch: fake, log });
  const { alice } = await crewOfTwo(relay);

  alice.send({ type: 'pack_active', pack: sourcePack() });
  const first = await alice.next('pack_data');

  fake.fail = true;
  for (let i = 0; i < 3; i++) {
    clock.advance(60000);
    await relay.pollSources();
    assert.deepEqual((await alice.next('pack_data')).values, first.values, 'old values');
  }
  assert.equal(fake.calls.length, 4);
  assert.equal(errors.filter((e) => e.includes('[SOURCE]')).length, 1);

  // Bad replies are failures too
  fake.fail = false;
  fake.reply = 'just text';
  clock.advance(60000);
  await relay.pollSources();
  assert.deepEqual((await alice.next('pack_data')).values, first.values);

  fake.reply = sourceReply('post', '90');
  clock.advance(60000);
  await relay.pollSources();
  assert.equal((await alice.next('pack_data')).values['game.state'], 'post');

  // Logged again after it worked
  fake.fail = true;
  clock.advance(60000);
  await relay.pollSources();
  assert.equal(errors.filter((e) => e.includes('[SOURCE]')).length, 2);
});

test('a source on a host that is not allowed is never fetched', async () => {
  const clock = fakeClock();
  const fake = fakeFetch();
  const relay = await relayUp({ now: clock, fetch: fake });
  const { alice } = await crewOfTwo(relay);

  const evil = sourcePack();
  evil.sources[0].url = 'https://evil.example.com/data';
  alice.send({ type: 'pack_active', pack: evil });
  const plain = sourcePack();
  plain.sources[0].url = 'http://site.api.espn.com/x';
  alice.send({ type: 'pack_active', pack: plain });
  await alice.sync();
  assert.equal(relay.state.devicePacks.size, 0, 'invalid packs are not stored');

  // Even if such a pack is in the state (accepted when the host list was different)
  relay.state.devicePacks.set('a1', evil);
  clock.advance(60000);
  await relay.pollSources();
  relay.state.devicePacks.set('a1', plain);
  clock.advance(60000);
  await relay.pollSources();
  assert.deepEqual(fake.calls, []);

  // The host list is an option
  const other = track(await startRelay({ sourceTickMs: 0, fetch: fake, sourceHosts: ['evil.example.com'] }));
  const dave = await connect(other);
  await dave.createCrew('d1', 'Dave');
  dave.send({ type: 'pack_active', pack: evil });
  await dave.next('pack_data');
  assert.deepEqual(fake.calls, ['https://evil.example.com/data']);
});

test('a reply over 2 MB is refused', async () => {
  const clock = fakeClock();
  const fake = fakeFetch();
  fake.reply = { team: { long: 'z'.repeat(2 * 1024 * 1024) } };
  const relay = await relayUp({ now: clock, fetch: fake });
  const { alice } = await crewOfTwo(relay);

  alice.send({ type: 'pack_active', pack: sourcePack() });
  assert.deepEqual((await alice.next('pack_data')).values, {});
  assert.equal(relay.state.sourceCache.get(SOURCE_URL).hasData, false);
});

test('a device that joins again gets pack_data for its stored pack', async () => {
  const clock = fakeClock();
  const fake = fakeFetch();
  const relay = await relayUp({ now: clock, fetch: fake });
  const { alice, code } = await crewOfTwo(relay);

  alice.send({ type: 'pack_active', pack: sourcePack() });
  await alice.next('pack_data');
  await alice.close();
  await waitFor(() => !relay.state.devices.has('a1'), 'alice offline');

  clock.advance(30000);
  const back = await connect(relay);
  await back.joinCrew(code, 'a1', 'Alice');
  assert.equal((await back.next('pack_data')).values['game.state'], 'in');
  assert.equal(fake.calls.length, 1, 'fresh cache');
});

// --- Persistence ---

test('tokens, device packs and pending installs survive close + recreate', async () => {
  const dataFile = tempDataFile();
  const fake = fakeFetch();
  const first = await relayUp({ dataFile, fetch: fake });
  const { alice, bob, code } = await crewOfTwo(first);
  const tokenA = await connectAgent(first, alice);
  const tokenB = await connectAgent(first, bob);
  alice.send({ type: 'pack_active', pack: sourcePack() });
  await alice.next('pack_data');
  await bob.close();
  await waitFor(() => !first.state.devices.has('b1'), 'bob offline');
  await callTool(first, tokenB, 'install_pack', { pack: smallPack() });
  await first.close();

  const saved = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
  assert.equal(Object.keys(saved.agentTokens).length, 2);
  assert.ok(!JSON.stringify(saved).includes(tokenA));

  const second = await relayUp({ dataFile, fetch: fake });
  assert.equal((await rpc(second, tokenA, 'ping')).status, 200);
  assert.deepEqual(second.state.devicePacks.get('a1'), sourcePack());
  const status = JSON.parse((await callTool(second, tokenA, 'get_status')).text);
  assert.equal(status.owner, 'Alice');
  assert.equal(status.owner_online, false);
  assert.deepEqual(status.active_pack, { id: 'live', name: 'Live' });

  const bobBack = await connect(second);
  await bobBack.joinCrew(code, 'b1', 'Bob');
  assert.deepEqual((await bobBack.next('pack_install')).pack, smallPack());
});

test('a state file from before packs loads; bad pack entries are dropped', async () => {
  const dataFile = tempDataFile();
  fs.writeFileSync(dataFile, JSON.stringify({
    version: 1,
    crews: { 'LEELOO-OLD1': { created_at: 1, telegram_users: [], members: { a1: 'Alice' } } },
    queues: {},
    pendingSpotifyTokens: {}
  }));
  const old = createRelay({ dataFile, log: quietLog, sourceTickMs: 0 });
  assert.equal(old.state.crews.size, 1);
  assert.equal(old.state.agentTokens.size, 0);
  assert.equal(old.state.devicePacks.size, 0);
  assert.equal(old.state.deviceKeys.size, 0);
  await old.close();

  fs.writeFileSync(dataFile, JSON.stringify({
    version: 1,
    crews: { 'LEELOO-NEW1': { created_at: 1, telegram_users: [], members: { a1: 'Alice', b1: 'Bob' } } },
    agentTokens: { nothex: { device_id: 'a1', crew_code: 'X' }, ['a'.repeat(64)]: { device_id: 'a1', crew_code: 'X', created_at: 5 }, ['b'.repeat(64)]: 'junk' },
    // b1: invalid pack. c1: junk. z9: valid pack, but the device is in no crew
    devicePacks: { a1: smallPack(), b1: smallPack({ evil: 1 }), c1: 'junk', z9: smallPack() },
    deviceKeys: { a1: 'c'.repeat(64), b1: 'short', c1: 7 },
    pendingPackOps: { a1: { message: { type: 'pack_reset' }, timestamp: 1 }, b1: { message: { type: 'run', cmd: 'x' } }, c1: null }
  }));
  const relay = createRelay({ dataFile, log: quietLog, sourceTickMs: 0 });
  assert.deepEqual(Array.from(relay.state.agentTokens.keys()), ['a'.repeat(64)]);
  assert.deepEqual(Array.from(relay.state.devicePacks.keys()), ['a1']);
  assert.deepEqual(Array.from(relay.state.pendingPackOps.keys()), ['a1']);
  assert.deepEqual(Array.from(relay.state.deviceKeys.entries()), [['a1', 'c'.repeat(64)]]);
  await relay.close();
});
