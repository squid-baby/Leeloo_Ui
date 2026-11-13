#!/usr/bin/env node
// Operator tool: let ONE device register again after it lost its device key
// (for example a replaced SD card).
//
//   pm2 stop leeloo-relay
//   node reset_device_key.js data/state.json leeloo_1a2b3c4d5e6f
//   pm2 start leeloo-relay
//
// It removes the stored key hash and the agent links of the device from the
// state file. Stop the relay first: a running relay writes its own copy of
// the state over the file. After the reset the device binds with the crew
// code of its roster (join_crew) and gets a new key. On that bind the relay
// discards what it kept for the device (queued messages, pending Spotify
// tokens, pending pack installs). This tool never prints keys or hashes.

const fs = require('fs');
const path = require('path');

function fail(message, code = 1) {
  console.error(`reset_device_key: ${message}`);
  process.exit(code);
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function main(argv) {
  if (argv.length !== 2 || !argv[0] || !argv[1]) {
    fail('usage: node reset_device_key.js <state file> <device_id>   (stop the relay first)', 2);
  }
  const [file, deviceId] = argv;

  let raw;
  try {
    raw = fs.readFileSync(file, 'utf8');
  } catch (error) {
    fail(`cannot read ${file} (${error.code || error.message}). Nothing changed.`);
  }

  let state;
  try {
    state = JSON.parse(raw);
  } catch (_) {
    fail(`${file} is not valid JSON. Nothing changed.`);
  }
  if (!isPlainObject(state) || !isPlainObject(state.crews)) {
    fail(`${file} is not a relay state file (no "crews"). Nothing changed.`);
  }

  const crewsOfDevice = Object.entries(state.crews)
    .filter(([, crew]) => isPlainObject(crew) && isPlainObject(crew.members) &&
      Object.prototype.hasOwnProperty.call(crew.members, deviceId))
    .map(([code]) => code);

  let keyRemoved = false;
  if (isPlainObject(state.deviceKeys) && Object.prototype.hasOwnProperty.call(state.deviceKeys, deviceId)) {
    delete state.deviceKeys[deviceId];
    keyRemoved = true;
  }

  let linksRemoved = 0;
  if (isPlainObject(state.agentTokens)) {
    for (const [hash, entry] of Object.entries(state.agentTokens)) {
      if (isPlainObject(entry) && entry.device_id === deviceId) {
        delete state.agentTokens[hash];
        linksRemoved++;
      }
    }
  }

  console.log(`Device ${deviceId}:`);
  console.log(`  crew roster: ${crewsOfDevice.length ? crewsOfDevice.join(', ') : '(on no roster)'}`);
  console.log(`  device key: ${keyRemoved ? 'removed' : 'none stored'}`);
  console.log(`  agent links: ${linksRemoved} removed`);

  if (!keyRemoved && !linksRemoved) {
    console.log('Nothing to change. The file was not written.');
    return;
  }

  // Same way as the relay: temp file in the same directory, then rename
  const tmp = path.join(path.dirname(file), `${path.basename(file)}.${process.pid}.reset.tmp`);
  try {
    fs.writeFileSync(tmp, JSON.stringify(state), { mode: 0o600 });
    fs.renameSync(tmp, file);
  } catch (error) {
    try { fs.unlinkSync(tmp); } catch (_) { /* ignore */ }
    fail(`cannot write ${file} (${error.code || error.message}). Nothing changed.`);
  }

  console.log(`Saved ${file}. Start the relay now.`);
  console.log(crewsOfDevice.length
    ? 'The device gets a new key when it joins its crew again (it needs the crew code).'
    : 'The device gets a new key at its next registration.');
}

main(process.argv.slice(2));
