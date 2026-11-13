const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const WebSocket = require('ws');
const { createRelay } = require('../server.js');

const quietLog = { log() {}, error() {} };

function tempDataFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'leeloo-relay-test-'));
  return path.join(dir, 'state.json');
}

// A clock the tests can move forward.
function fakeClock(start = 1700000000000) {
  let t = start;
  const now = () => t;
  now.advance = (ms) => { t += ms; };
  return now;
}

async function startRelay(options = {}) {
  const relay = createRelay({
    dataFile: tempDataFile(),
    log: quietLog,
    saveDebounceMs: 10,
    telegramApiSecret: '',
    ...options
  });
  const port = await relay.listen(0, '127.0.0.1');
  relay.port = port;
  relay.wsUrl = `ws://127.0.0.1:${port}`;
  relay.httpUrl = `http://127.0.0.1:${port}`;
  return relay;
}

// Device keys that relays gave to the test devices, by device id. The Client
// helpers send them like a real device does (it keeps its key in a file).
// A test that wants to bind WITHOUT the key uses client.send() directly.
const deviceKeys = new Map();

// WebSocket client that buffers incoming JSON messages.
class Client {
  constructor(url, wsOptions = {}) {
    this.ws = new WebSocket(url, wsOptions);
    this.inbox = [];
    this.waiters = [];
    this.closed = new Promise((resolve) => this.ws.once('close', (code) => resolve(code)));
    this.ws.on('error', () => {});
    this.ws.on('message', (raw) => {
      const msg = JSON.parse(raw);
      if (typeof msg.device_key === 'string' && msg.device_id) deviceKeys.set(msg.device_id, msg.device_key);
      const index = this.waiters.findIndex((w) => w.match(msg));
      if (index >= 0) {
        const [waiter] = this.waiters.splice(index, 1);
        clearTimeout(waiter.timer);
        waiter.resolve(msg);
      } else {
        this.inbox.push(msg);
      }
    });
  }

  static async open(url, wsOptions) {
    const client = new Client(url, wsOptions);
    await new Promise((resolve, reject) => {
      client.ws.once('open', resolve);
      client.ws.once('error', reject);
    });
    return client;
  }

  send(obj) {
    this.ws.send(typeof obj === 'string' ? obj : JSON.stringify(obj));
  }

  // Next message (optionally of a given type), in arrival order.
  next(type = null, timeoutMs = 2000) {
    const match = (msg) => !type || msg.type === type;
    const index = this.inbox.findIndex(match);
    if (index >= 0) return Promise.resolve(this.inbox.splice(index, 1)[0]);
    return new Promise((resolve, reject) => {
      const waiter = { match, resolve };
      waiter.timer = setTimeout(() => {
        this.waiters.splice(this.waiters.indexOf(waiter), 1);
        reject(new Error(`timeout waiting for ${type || 'any message'}`));
      }, timeoutMs);
      this.waiters.push(waiter);
    });
  }

  // Round trip through the server: everything sent to us before this is in the inbox.
  async sync() {
    this.send({ type: 'ping' });
    await this.next('pong');
  }

  // All buffered messages of a type (after a sync), removed from the inbox.
  async drain(type = null) {
    await this.sync();
    const taken = this.inbox.filter((m) => !type || m.type === type);
    this.inbox = this.inbox.filter((m) => type && m.type !== type);
    return taken;
  }

  async createCrew(deviceId, name, crewCode) {
    const msg = { type: 'create_crew', device_id: deviceId, display_name: name };
    if (crewCode) msg.crew_code = crewCode;
    if (deviceKeys.has(deviceId)) msg.device_key = deviceKeys.get(deviceId);
    this.send(msg);
    return this.next();
  }

  async joinCrew(crewCode, deviceId, name) {
    const msg = { type: 'join_crew', crew_code: crewCode, device_id: deviceId, display_name: name };
    if (deviceKeys.has(deviceId)) msg.device_key = deviceKeys.get(deviceId);
    this.send(msg);
    return this.next();
  }

  async register(deviceId, name, crewCode) {
    const msg = { type: 'register', device_id: deviceId, device_name: name };
    if (crewCode) msg.crew_code = crewCode;
    if (deviceKeys.has(deviceId)) msg.device_key = deviceKeys.get(deviceId);
    this.send(msg);
    return this.next();
  }

  sendMessage(msgType, payload) {
    this.send({ type: 'message', msg_type: msgType, payload });
  }

  async close() {
    if (this.ws.readyState !== WebSocket.CLOSED) this.ws.terminate();
    await this.closed;
  }
}

async function post(relay, route, body, headers = {}) {
  const res = await fetch(relay.httpUrl + route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body)
  });
  return { status: res.status, body: await res.json() };
}

module.exports = { Client, startRelay, tempDataFile, fakeClock, post, quietLog, deviceKeys };
