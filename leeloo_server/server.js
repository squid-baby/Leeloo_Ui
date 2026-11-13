const express = require('express');
const WebSocket = require('ws');
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
require('dotenv').config();

// Message types that are worth keeping for a device that is offline.
// Ephemeral ones (nudge, reaction) are only relayed live.
const QUEUEABLE_MSG_TYPES = new Set(['text', 'song_push', 'hang_propose', 'hang_confirm', 'pack_offer']);

const SPOTIFY_PENDING_TTL_MS = 3600000; // access tokens expire after 1 hour
const STATE_VERSION = 1; // new keys are optional when loading, so no bump was needed for packs

function generateCrewCode() {
  const chars = CODE_ALPHABET;
  let code = '';
  for (let i = 0; i < 4; i++) {
    code += chars[crypto.randomInt(chars.length)];
  }
  return `LEELOO-${code}`;
}

function generateDeviceId() {
  return 'leeloo_' + crypto.randomBytes(6).toString('hex');
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Returns a trimmed, length-limited string, or '' when the value is not a usable string.
function cleanString(value, maxLength) {
  if (typeof value !== 'string') return '';
  return value.trim().slice(0, maxLength);
}

function normalizeCrewCode(value) {
  return cleanString(value, 32).toUpperCase();
}

function clientIp(req) {
  const forwarded = req.headers['x-forwarded-for'];
  const first = (Array.isArray(forwarded) ? forwarded[0] : forwarded || '').split(',')[0].trim();
  const ip = first || (req.socket && req.socket.remoteAddress) || 'unknown';
  return ip.startsWith('::ffff:') ? ip.slice(7) : ip;
}

// ---------------------------------------------------------------------------
// Packs (docs/PACKS.md §1). A pack is data, not code. Everything is in this
// one file because deploy.sh copies only server.js.
// ---------------------------------------------------------------------------

// The device accepts 8,000 bytes. The relay stops a little earlier, so that a
// pack it accepted is never refused by a device that writes the same JSON
// in a slightly different way.
const PACK_MAX_BYTES = 7800;
const PACK_MAX_ERRORS = 20;
const DEFAULT_SOURCE_HOSTS = ['site.api.espn.com', 'api.open-meteo.com'];
const PACK_COLOR_KEYS = ['bg', 'green', 'purple', 'lavender', 'tan', 'rose', 'white', 'yellow', 'orange'];
const PACK_LABEL_KEYS = ['weather', 'time', 'messages', 'album'];
const PACK_ID_RE = /^[a-z0-9-]{1,32}$/;
const PACK_NAME_RE = /^[a-z][a-z0-9_]{0,15}$/;          // source id, field key
const PACK_TEMPLATE_RE = /\{([A-Za-z0-9_.]+)\}/g;        // {source_id.field}, same as leeloo_packs.py
const PACK_COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
const PACK_VALUE_MAX_CHARS = 40;
const PACK_LONE_SURROGATE_RE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
// Readable screen rule: these colours are drawn on `bg`
const PACK_DEFAULT_PALETTE = {
  bg: '#1A1D2E', green: '#7beec0', purple: '#9C93DD', lavender: '#d978f9', tan: '#C2995E', white: '#FFFFFF'
};
const PACK_TEXT_COLOR_KEYS = ['green', 'purple', 'lavender', 'tan', 'white'];
const PACK_MIN_CONTRAST = 2.0;

// WCAG relative luminance of #RRGGBB (the same formula as leeloo_packs.py)
function luminance(hex) {
  const channel = (i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

// WCAG contrast ratio of two #RRGGBB colours (1.0 to 21.0)
function contrastRatio(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// Path of the first string (key or value) with a lone surrogate, or null.
// Such text cannot be stored as UTF-8 on the device.
function loneSurrogatePath(value, where, depth = 0) {
  if (typeof value === 'string') return PACK_LONE_SURROGATE_RE.test(value) ? where : null;
  if (depth > 12 || value === null || typeof value !== 'object') return null;
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      const found = loneSurrogatePath(value[i], `${where}[${i}]`, depth + 1);
      if (found) return found;
    }
    return null;
  }
  for (const [key, item] of Object.entries(value)) {
    if (PACK_LONE_SURROGATE_RE.test(key)) return where || 'pack';
    const found = loneSurrogatePath(item, where ? `${where}.${key}` : key, depth + 1);
    if (found) return found;
  }
  return null;
}

// Characters as Python's len() counts them (code points), so that the relay
// and the device agree on every length limit.
function charCount(text) {
  return Array.from(text).length;
}

function parseSourceHosts(value) {
  const list = Array.isArray(value) ? value : String(value || '').split(',');
  const hosts = list.map((h) => String(h).trim().toLowerCase()).filter(Boolean);
  return new Set(hosts.length ? hosts : DEFAULT_SOURCE_HOSTS);
}

// Why a source URL may not be fetched, or null when it is fine.
function sourceUrlProblem(url, hosts) {
  if (typeof url !== 'string') return 'must be a string';
  if (!url.startsWith('https://')) return 'must start with https://';
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_) {
    return 'not a valid URL';
  }
  if (parsed.protocol !== 'https:') return 'must start with https://';
  if (parsed.username || parsed.password) return 'must not contain a user name or password';
  if (parsed.port) return 'must not contain a port';
  if (!hosts.has(parsed.hostname.toLowerCase())) {
    return `host ${parsed.hostname} is not allowed (allowed: ${Array.from(hosts).join(', ')})`;
  }
  return null;
}

/**
 * Check a pack against docs/PACKS.md §1.
 * Returns a list of short error texts ("panels[1].rows[0].left: longer than
 * 28 characters"). An empty list means the pack is valid.
 *
 * options.sourceHosts: allowed source hosts (array, Set or comma list).
 */
function validatePack(pack, options = {}) {
  const hosts = options.sourceHosts instanceof Set
    ? options.sourceHosts
    : parseSourceHosts(options.sourceHosts);
  const errors = [];
  const add = (where, problem) => {
    if (errors.length < PACK_MAX_ERRORS) errors.push(`${where}: ${problem}`);
  };

  if (!isPlainObject(pack)) return ['pack: must be a JSON object'];

  let bytes;
  try {
    bytes = Buffer.byteLength(JSON.stringify(pack), 'utf8');
  } catch (_) {
    return ['pack: cannot be written as JSON'];
  }
  if (bytes > PACK_MAX_BYTES) {
    return [`pack: JSON text is ${bytes} bytes, the limit is ${PACK_MAX_BYTES}`];
  }
  const badText = loneSurrogatePath(pack, '');
  if (badText) return [`${badText}: has an invalid character`];

  const unknownKeys = (obj, allowed, prefix) => {
    for (const key of Object.keys(obj)) {
      if (!allowed.includes(key)) {
        add(`${prefix}${key}`, `unknown key (allowed: ${allowed.join(', ')})`);
      }
    }
  };
  const checkText = (value, where, min, max) => {
    if (typeof value !== 'string') return add(where, 'must be a string');
    const count = charCount(value);
    if (count < min) return add(where, 'must not be empty');
    if (count > max) return add(where, `longer than ${max} characters`);
    return undefined;
  };
  // Text template of a row. The limit is for the text you write: a
  // {source_id.field} placeholder does not count, because its length is known
  // only when the value is there (the example pack has "{game.home_score}",
  // 17 characters, as a `right` text with a limit of 14). The device cuts
  // text that is too wide for the box.
  const checkTemplate = (value, where, max) => {
    if (typeof value !== 'string') return add(where, 'must be a string');
    if (charCount(value.replace(PACK_TEMPLATE_RE, '')) > max) return add(where, `longer than ${max} characters`);
    return undefined;
  };
  const checkNumber = (value, where, min, max, integer = false) => {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      return add(where, `must be a number from ${min} to ${max}`);
    }
    if (integer && !Number.isInteger(value)) return add(where, 'must be an integer');
    if (value < min || value > max) return add(where, `must be from ${min} to ${max}`);
    return undefined;
  };

  unknownKeys(pack, ['pack', 'id', 'name', 'theme', 'sources', 'panels', 'rotate_seconds'], '');

  if (pack.pack !== 1) add('pack', 'must be the number 1');

  if (typeof pack.id !== 'string') add('id', 'required, a string');
  else if (!PACK_ID_RE.test(pack.id)) add('id', 'must match ^[a-z0-9-]{1,32}$');

  if (pack.name === undefined) add('name', 'required, 1 to 24 characters');
  else checkText(pack.name, 'name', 1, 24);

  if (pack.rotate_seconds !== undefined) checkNumber(pack.rotate_seconds, 'rotate_seconds', 5, 600);

  // --- theme ---
  if (pack.theme !== undefined) {
    const theme = pack.theme;
    if (!isPlainObject(theme)) {
      add('theme', 'must be an object');
    } else {
      unknownKeys(theme, ['colors', 'header', 'labels'], 'theme.');
      if (theme.colors !== undefined) {
        if (!isPlainObject(theme.colors)) {
          add('theme.colors', 'must be an object');
        } else {
          for (const [key, value] of Object.entries(theme.colors)) {
            if (!PACK_COLOR_KEYS.includes(key)) {
              add(`theme.colors.${key}`, `unknown key (allowed: ${PACK_COLOR_KEYS.join(', ')})`);
            } else if (typeof value !== 'string' || !PACK_COLOR_RE.test(value)) {
              add(`theme.colors.${key}`, 'must be a colour like #1A2B3C');
            }
          }
          // Readable screen rule: a pack must not hide the text (all black, ...)
          const merged = { ...PACK_DEFAULT_PALETTE };
          for (const key of Object.keys(merged)) {
            const value = theme.colors[key];
            if (typeof value === 'string' && PACK_COLOR_RE.test(value)) merged[key] = value;
          }
          for (const key of PACK_TEXT_COLOR_KEYS) {
            const ratio = contrastRatio(merged[key], merged.bg);
            if (ratio < PACK_MIN_CONTRAST) {
              add(`theme.colors.${key}`,
                `too close to the background colour (contrast ${ratio.toFixed(2)}, need ${PACK_MIN_CONTRAST.toFixed(1)})`);
            }
          }
        }
      }
      if (theme.header !== undefined) checkText(theme.header, 'theme.header', 1, 12);
      if (theme.labels !== undefined) {
        if (!isPlainObject(theme.labels)) {
          add('theme.labels', 'must be an object');
        } else {
          for (const [key, value] of Object.entries(theme.labels)) {
            if (!PACK_LABEL_KEYS.includes(key)) {
              add(`theme.labels.${key}`, `unknown key (allowed: ${PACK_LABEL_KEYS.join(', ')})`);
            } else {
              checkText(value, `theme.labels.${key}`, 1, 10);
            }
          }
        }
      }
    }
  }

  // --- sources ---
  if (pack.sources !== undefined) {
    if (!Array.isArray(pack.sources)) {
      add('sources', 'must be an array');
    } else {
      if (pack.sources.length > 3) add('sources', 'more than 3 sources');
      const seen = new Set();
      pack.sources.slice(0, 3).forEach((source, i) => {
        const at = `sources[${i}]`;
        if (!isPlainObject(source)) return add(at, 'must be an object');
        unknownKeys(source, ['id', 'url', 'every', 'fields'], `${at}.`);

        if (typeof source.id !== 'string') add(`${at}.id`, 'required, a string');
        else if (!PACK_NAME_RE.test(source.id)) add(`${at}.id`, 'must match ^[a-z][a-z0-9_]{0,15}$');
        else if (seen.has(source.id)) add(`${at}.id`, `"${source.id}" is used by more than one source`);
        else seen.add(source.id);

        if (source.url === undefined) {
          add(`${at}.url`, 'required');
        } else {
          const problem = sourceUrlProblem(source.url, hosts);
          if (problem) add(`${at}.url`, problem);
        }

        if (source.every === undefined) add(`${at}.every`, 'required, seconds from 30 to 3600');
        else checkNumber(source.every, `${at}.every`, 30, 3600);

        if (!isPlainObject(source.fields)) {
          add(`${at}.fields`, 'required, an object with 1 to 12 entries');
        } else {
          const entries = Object.entries(source.fields);
          if (entries.length < 1) add(`${at}.fields`, 'needs at least 1 entry');
          if (entries.length > 12) add(`${at}.fields`, 'more than 12 entries');
          for (const [key, value] of entries.slice(0, 12)) {
            const where = `${at}.fields.${key}`;
            if (!PACK_NAME_RE.test(key)) {
              add(where, 'key must match ^[a-z][a-z0-9_]{0,15}$');
            } else if (typeof value !== 'string') {
              add(where, 'must be a dot path string like "team.nextEvent.0.shortName"');
            } else if (charCount(value) > 200) {
              add(where, 'longer than 200 characters');
            }
          }
        }
        return undefined;
      });
    }
  }

  // --- panels ---
  const checkWhen = (when, at) => {
    if (!isPlainObject(when)) return add(at, 'must be an object');
    unknownKeys(when, ['field', 'equals', 'not_equals', 'exists'], `${at}.`);
    if (typeof when.field !== 'string' || !when.field) {
      add(`${at}.field`, 'required, a value name like "game.state" (source_id.field)');
    }
    const ops = ['equals', 'not_equals', 'exists'].filter((op) => when[op] !== undefined);
    if (ops.length !== 1) return add(at, 'needs exactly one of equals, not_equals, exists');
    if (ops[0] === 'exists') {
      if (typeof when.exists !== 'boolean') add(`${at}.exists`, 'must be true or false');
    } else if (typeof when[ops[0]] !== 'string') {
      add(`${at}.${ops[0]}`, 'must be a string');
    }
    return undefined;
  };

  const checkRow = (row, at) => {
    if (!isPlainObject(row)) return add(at, 'must be an object');
    unknownKeys(row, ['left', 'right', 'bar'], `${at}.`);
    if (row.left === undefined) add(`${at}.left`, 'required');
    else checkTemplate(row.left, `${at}.left`, 28);
    if (row.right !== undefined) checkTemplate(row.right, `${at}.right`, 14);
    if (row.right !== undefined && row.bar !== undefined) {
      add(at, 'has right and bar, use only one of them');
    }
    if (row.bar !== undefined) {
      const bar = row.bar;
      if (!isPlainObject(bar)) return add(`${at}.bar`, 'must be an object {value, max}');
      unknownKeys(bar, ['value', 'max'], `${at}.bar.`);
      if (typeof bar.value !== 'string') add(`${at}.bar.value`, 'required, a template string like "{game.score}"');
      if (typeof bar.max !== 'number' || !Number.isFinite(bar.max) || bar.max <= 0) {
        add(`${at}.bar.max`, 'required, a number greater than 0');
      }
    }
    return undefined;
  };

  if (pack.panels !== undefined) {
    if (!Array.isArray(pack.panels)) {
      add('panels', 'must be an array');
    } else {
      if (pack.panels.length > 6) add('panels', 'more than 6 panels');
      pack.panels.slice(0, 6).forEach((panel, i) => {
        const at = `panels[${i}]`;
        if (!isPlainObject(panel)) return add(at, 'must be an object');

        if (panel.slot !== 'weather') add(`${at}.slot`, 'must be "weather"');
        if (panel.when !== undefined) checkWhen(panel.when, `${at}.when`);
        if (panel.priority !== undefined) checkNumber(panel.priority, `${at}.priority`, -100, 100, true);

        if (panel.builtin !== undefined) {
          unknownKeys(panel, ['slot', 'builtin', 'when', 'priority'], `${at}.`);
          if (panel.builtin !== 'weather') add(`${at}.builtin`, 'must be "weather"');
          return undefined;
        }

        unknownKeys(panel, ['slot', 'title', 'rows', 'when', 'priority'], `${at}.`);
        if (panel.title !== undefined) checkText(panel.title, `${at}.title`, 1, 10);
        if (!Array.isArray(panel.rows)) {
          add(`${at}.rows`, 'required, 1 to 3 rows (or use "builtin": "weather")');
        } else {
          if (panel.rows.length < 1) add(`${at}.rows`, 'needs at least 1 row');
          if (panel.rows.length > 3) add(`${at}.rows`, 'more than 3 rows');
          panel.rows.slice(0, 3).forEach((row, j) => checkRow(row, `${at}.rows[${j}]`));
        }
        return undefined;
      });
    }
  }

  return errors;
}

// Hints for a VALID pack: value names used in templates or conditions that no
// source provides (they would always show as "--"). Not errors: the format
// allows them.
function packWarnings(pack) {
  const known = new Set();
  for (const source of Array.isArray(pack.sources) ? pack.sources : []) {
    for (const field of Object.keys(source.fields)) known.add(`${source.id}.${field}`);
  }
  const warnings = [];
  const checkTemplate = (text, where) => {
    if (typeof text !== 'string') return;
    for (const match of text.matchAll(PACK_TEMPLATE_RE)) {
      if (!known.has(match[1])) warnings.push(`${where}: {${match[1]}} is not a field of any source, it will show as --`);
    }
  };
  (Array.isArray(pack.panels) ? pack.panels : []).forEach((panel, i) => {
    if (panel.when && !known.has(panel.when.field)) {
      warnings.push(`panels[${i}].when.field: ${panel.when.field} is not a field of any source`);
    }
    (Array.isArray(panel.rows) ? panel.rows : []).forEach((row, j) => {
      checkTemplate(row.left, `panels[${i}].rows[${j}].left`);
      checkTemplate(row.right, `panels[${i}].rows[${j}].right`);
      if (row.bar) checkTemplate(row.bar.value, `panels[${i}].rows[${j}].bar.value`);
    });
  });
  return warnings.slice(0, PACK_MAX_ERRORS);
}

// Follow a dot path into a JSON reply. Returns a string of 40 characters at
// most, or undefined when the path gives no value.
function extractValue(data, dotPath) {
  let current = data;
  for (const part of String(dotPath).split('.')) {
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(part)) return undefined;
      current = current[Number(part)];
    } else if (isPlainObject(current)) {
      if (!Object.prototype.hasOwnProperty.call(current, part)) return undefined;
      current = current[part];
    } else {
      return undefined;
    }
  }
  if (isPlainObject(current)) {
    if (!Object.prototype.hasOwnProperty.call(current, 'displayValue')) return undefined;
    current = current.displayValue;
  }
  let text;
  if (typeof current === 'string') text = current;
  else if (typeof current === 'number' && Number.isFinite(current)) text = String(current);
  else if (typeof current === 'boolean') text = String(current);
  else return undefined;
  return Array.from(text).slice(0, PACK_VALUE_MAX_CHARS).join('');
}

// docs/PACKS.md §1, word for word (test/packs.test.js checks that the two are
// the same). It is embedded because only server.js is copied to the server.
const PACK_GUIDE = [
  "## 1. Pack format (version 1)",
  "",
  "The JSON text of a pack is 8,000 bytes at most (the relay accepts 7,800, to",
  "keep a margin). Unknown keys are an error. Text must not contain lone",
  "surrogate code points (U+D800 to U+DFFF).",
  "",
  "| Key | Type | Rules |",
  "|-----|------|-------|",
  "| `pack` | number | Must be `1` |",
  "| `id` | string | `^[a-z0-9-]{1,32}$` |",
  "| `name` | string | 1 to 24 characters |",
  "| `theme` | object | Optional. See below |",
  "| `sources` | array | Optional. 3 at most |",
  "| `panels` | array | Optional. 6 at most |",
  "| `rotate_seconds` | number | Optional. 5 to 600. Default 20 |",
  "",
  "### theme",
  "",
  "- `colors`: object. Keys from `bg`, `green`, `purple`, `lavender`, `tan`,",
  "  `rose`, `white`, `yellow`, `orange`. Each value `#RRGGBB`.",
  "  (`tan` = weather box, `purple` = time box, `lavender` = outer frame and",
  "  messages box, `green` = album box.)",
  "  **Readable screen rule:** put the pack's colours on top of the default",
  "  palette (`bg #1A1D2E`, `green #7beec0`, `purple #9C93DD`,",
  "  `lavender #d978f9`, `tan #C2995E`, `white #FFFFFF`). Each of `green`,",
  "  `purple`, `lavender`, `tan` and `white` must then have a WCAG contrast",
  "  ratio of 2.0 or more against `bg`. (A pack that only sets a light `bg`",
  "  must set these five colours too.)",
  "- `header`: string, 1 to 12 characters. Replaces `LEELOO v1.0`.",
  "- `labels`: object. Keys from `weather`, `time`, `messages`, `album`. Each",
  "  value 1 to 10 characters.",
  "",
  "### sources",
  "",
  "Each source: `{ \"id\", \"url\", \"every\", \"fields\" }`.",
  "",
  "- `id`: `^[a-z][a-z0-9_]{0,15}$`, unique in the pack.",
  "- `url`: `https://` only. The host must be in the relay's allowed list",
  "  (default: `site.api.espn.com`, `api.open-meteo.com`).",
  "- `every`: seconds between fetches, 30 to 3600.",
  "- `fields`: object, 1 to 12 entries. Key `^[a-z][a-z0-9_]{0,15}$`. Value: a",
  "  dot path into the JSON reply. A number is an array index",
  "  (`team.nextEvent.0.shortName`). 200 characters at most.",
  "",
  "The **relay** fetches each URL (one time for all devices that use it) and",
  "sends the values to the devices. A value is made a string of 40 characters at",
  "most. If the path ends at an object that has `displayValue`, that is used.",
  "A missing path gives no value. The full name of a value is `source_id.field`,",
  "for example `game.state`.",
  "",
  "### panels",
  "",
  "All panels in version 1 fill the **weather box** (three rows). Two forms:",
  "",
  "1. Built-in: `{ \"slot\": \"weather\", \"builtin\": \"weather\" }` — the normal",
  "   weather panel takes part in the rotation.",
  "2. Custom: `{ \"slot\": \"weather\", \"title\", \"rows\", \"when\", \"priority\" }`",
  "   - `title`: optional, 1 to 10 characters. Label of the box for this panel.",
  "   - `rows`: 1 to 3 rows. Each row: `{ \"left\", \"right\", \"bar\" }`.",
  "     - `left`: required text template, 28 characters at most (the text outside `{...}` is counted).",
  "     - `right`: optional text template, 14 characters at most (counted the same way).",
  "     - `bar`: optional `{ \"value\": template, \"max\": number > 0 }`. Draws the",
  "       12-box slider at the right. A row has `right` or `bar`, not the two.",
  "   - A template puts values in with `{source_id.field}`. A value that is not",
  "     there shows as `--`.",
  "   - `when`: optional condition `{ \"field\": \"game.state\", <op> }` with one",
  "     of `\"equals\": string`, `\"not_equals\": string`, `\"exists\": true|false`.",
  "     Both forms can have `when` and `priority`.",
  "   - `priority`: optional integer, -100 to 100. Default 0.",
  "",
  "**Which panel shows:** take the panels whose `when` is true (no `when` =",
  "true). Keep those with the highest `priority`. If more than one stays, show",
  "them in turn, each for `rotate_seconds`:",
  "`index = floor(unix_time / rotate_seconds) % count`. If none stays, or the",
  "pack has no panels, show the built-in weather."
].join('\n');

// packs/unc-hoops.json (also checked by the tests)
const EXAMPLE_PACK = {
  "pack": 1,
  "id": "unc-hoops",
  "name": "UNC Hoops",
  "theme": {
    "colors": {
      "bg": "#13294B",
      "tan": "#7BAFD4",
      "lavender": "#FFFFFF",
      "purple": "#B9D9EB",
      "green": "#7BAFD4"
    },
    "header": "GO HEELS",
    "labels": {
      "messages": "crew"
    }
  },
  "sources": [
    {
      "id": "game",
      "url": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/153",
      "every": 60,
      "fields": {
        "state": "team.nextEvent.0.competitions.0.status.type.state",
        "when": "team.nextEvent.0.competitions.0.status.type.shortDetail",
        "match": "team.nextEvent.0.shortName",
        "clock": "team.nextEvent.0.competitions.0.status.displayClock",
        "period": "team.nextEvent.0.competitions.0.status.period",
        "home": "team.nextEvent.0.competitions.0.competitors.0.team.abbreviation",
        "home_score": "team.nextEvent.0.competitions.0.competitors.0.score",
        "away": "team.nextEvent.0.competitions.0.competitors.1.team.abbreviation",
        "away_score": "team.nextEvent.0.competitions.0.competitors.1.score",
        "record": "team.record.items.0.summary",
        "standing": "team.standingSummary"
      }
    }
  ],
  "panels": [
    {
      "slot": "weather",
      "title": "live",
      "priority": 10,
      "when": {
        "field": "game.state",
        "equals": "in"
      },
      "rows": [
        {
          "left": "{game.home}",
          "right": "{game.home_score}"
        },
        {
          "left": "{game.away}",
          "right": "{game.away_score}"
        },
        {
          "left": "period {game.period}",
          "right": "{game.clock}"
        }
      ]
    },
    {
      "slot": "weather",
      "title": "final",
      "priority": 5,
      "when": {
        "field": "game.state",
        "equals": "post"
      },
      "rows": [
        {
          "left": "{game.home}",
          "right": "{game.home_score}"
        },
        {
          "left": "{game.away}",
          "right": "{game.away_score}"
        },
        {
          "left": "final",
          "right": "{game.record}"
        }
      ]
    },
    {
      "slot": "weather",
      "builtin": "weather"
    },
    {
      "slot": "weather",
      "title": "next game",
      "when": {
        "field": "game.state",
        "equals": "pre"
      },
      "rows": [
        {
          "left": "{game.match}"
        },
        {
          "left": "{game.when}"
        },
        {
          "left": "{game.record}",
          "right": "{game.standing}"
        }
      ]
    }
  ],
  "rotate_seconds": 20
};

// ---------------------------------------------------------------------------
// Agent connect (docs/PACKS.md §3)
// ---------------------------------------------------------------------------

const CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // No I/O/0/1
const AGENT_CODE_TTL_MS = 15 * 60000;
const AGENT_TOKENS_PER_DEVICE = 5;
const AGENT_SEND_LIMIT = 20;            // send_message + share_pack per token ...
const AGENT_SEND_WINDOW_MS = 10 * 60000; // ... in this window
const AGENT_PACK_OP_LIMIT = 10;         // install_pack + reset_pack per token in the same window
const PACK_ACTIVE_LIMIT = 20;           // changed pack_active per connection in the same window
const SOURCE_MIN_GAP_MS = 10000;        // a new field of a cached URL waits this long for its fetch
const MCP_BODY_LIMIT = 64 * 1024;
const MCP_MAX_BATCH = 20;
const MCP_PROTOCOL_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];
const MCP_DEFAULT_PROTOCOL_VERSION = '2025-06-18';
const SOURCE_TIMEOUT_MS = 10000;
const SOURCE_MAX_BYTES = 2 * 1024 * 1024;
const SOURCE_KEEP_UNUSED_MS = 3600000;

let RELAY_VERSION = '1.0.0';
try {
  RELAY_VERSION = require('./package.json').version || RELAY_VERSION;
} catch (_) { /* keep the default */ }

function generateConnectCode() {
  let code = '';
  for (let i = 0; i < 8; i++) code += CODE_ALPHABET[crypto.randomInt(CODE_ALPHABET.length)];
  return code;
}

function sha256Hex(text) {
  return crypto.createHash('sha256').update(text).digest('hex');
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

// Small self-contained page in the style of the Spotify success page.
function htmlPage(title, bodyHtml) {
  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex">
    <title>LEELOO - ${escapeHtml(title)}</title>
    <style>
      body { font-family: Arial, sans-serif; text-align: center; padding: 40px 16px; margin: 0; background: #1A1D2E; color: #A7AFD4; }
      main { max-width: 560px; margin: 0 auto; }
      h1 { color: #7beec0; }
      h2 { color: #7beec0; font-size: 1.05em; margin-top: 28px; }
      p, li { line-height: 1.5; }
      ol { text-align: left; }
      button { font-size: 1.05em; padding: 12px 22px; border: 0; border-radius: 8px; background: #7beec0; color: #1A1D2E; font-weight: bold; cursor: pointer; }
      textarea { width: 100%; box-sizing: border-box; font-family: monospace; font-size: 0.95em; padding: 12px; border: 1px solid #A7AFD4; border-radius: 8px; background: #11131f; color: #ffffff; resize: none; }
      .warn { color: #D6697F; }
    </style>
  </head>
  <body>
    <main>
${bodyHtml}
    </main>
  </body>
</html>`;
}

class RpcError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function rpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id === undefined ? null : id, error: { code, message } };
}

function toolText(text, isError = false) {
  const result = { content: [{ type: 'text', text }] };
  if (isError) result.isError = true;
  return result;
}

const PACK_ARGUMENT_SCHEMA = {
  type: 'object',
  description: 'The complete pack as a JSON object (format: call get_pack_guide). 7,800 bytes at most.',
  additionalProperties: true
};
const NO_ARGUMENTS_SCHEMA = { type: 'object', properties: {}, additionalProperties: false };
const PACK_INPUT_SCHEMA = {
  type: 'object',
  properties: { pack: PACK_ARGUMENT_SCHEMA },
  required: ['pack'],
  additionalProperties: false
};

const MCP_TOOLS = [
  {
    name: 'get_status',
    title: 'Leeloo status',
    description: 'Shows the owner name of this Leeloo, if it is online, the crew members, the crew devices that are online now, and the pack that is active on the Leeloo.',
    inputSchema: NO_ARGUMENTS_SCHEMA,
    annotations: { readOnlyHint: true }
  },
  {
    name: 'get_pack_guide',
    title: 'Pack format guide',
    description: 'Returns the pack format (version 1) with all rules and limits, the allowed source hosts and an example pack. Call this before you write a pack.',
    inputSchema: NO_ARGUMENTS_SCHEMA,
    annotations: { readOnlyHint: true }
  },
  {
    name: 'validate_pack',
    title: 'Check a pack',
    description: 'Checks a pack and changes nothing. Returns "valid" or the list of errors, each with the path of the bad value. Correct the pack and check it again.',
    inputSchema: PACK_INPUT_SCHEMA,
    annotations: { readOnlyHint: true }
  },
  {
    name: 'install_pack',
    title: 'Install a pack',
    description: 'Checks a pack, then installs it on the owner\'s Leeloo. It replaces the pack that is active now. If the Leeloo is offline, the pack is installed when it connects again. An invalid pack is not installed: the result lists the errors.',
    inputSchema: PACK_INPUT_SCHEMA
  },
  {
    name: 'share_pack',
    title: 'Offer a pack to the crew',
    description: 'Checks a pack, then offers it to the other Leeloos of the crew in the owner\'s name. Each friend decides to use it or not. It does not install the pack on the owner\'s Leeloo (use install_pack for that). Do this only when the owner asks for it.',
    inputSchema: PACK_INPUT_SCHEMA
  },
  {
    name: 'reset_pack',
    title: 'Default look',
    description: 'Removes the active pack from the owner\'s Leeloo and brings back the default look. If the Leeloo is offline, this is done when it connects again.',
    inputSchema: NO_ARGUMENTS_SCHEMA
  },
  {
    name: 'send_message',
    title: 'Message the crew',
    description: 'Sends a short text message to the crew in the owner\'s name. It shows on the screens of the friends\' Leeloos. Do this only when the owner asks for it.',
    inputSchema: {
      type: 'object',
      properties: {
        text: { type: 'string', minLength: 1, maxLength: 500, description: 'The message, 1 to 500 characters. Plain text.' }
      },
      required: ['text'],
      additionalProperties: false
    }
  }
];

const MCP_INSTRUCTIONS =
  'A Leeloo is a small gadget with a 480x320 screen that shows weather, time, crew messages and ' +
  'the music of its owner, and shares messages and songs with a crew of friends. This connector ' +
  'controls ONE Leeloo. You can change how it looks with a "pack": one JSON file with colours, ' +
  'labels and up to 6 panels for the weather box, which can show live values from allowed web ' +
  'sources. A pack is data, not code. Call get_pack_guide before you write a pack, check it with ' +
  'validate_pack, then use install_pack. share_pack and send_message go to the owner\'s friends: ' +
  'use them only when the owner asks.';

/**
 * Build a relay instance. Nothing listens until `listen()` is called.
 *
 * Options (all optional):
 *   dataFile          path of the JSON state file, or null to disable persistence
 *   heartbeatMs       WebSocket ping interval (default 30000)
 *   saveDebounceMs    delay between a change and the write to disk (default 1000)
 *   sweepMs           interval of the expiry sweep (default 60000)
 *   queueLimit        max queued messages per device (default 50)
 *   queueTtlMs        max age of a queued message (default 24 h)
 *   joinMaxFailures   failed join attempts allowed per window (default 10)
 *   joinWindowMs      length of that window (default 10 min)
 *   maxPayload        max incoming WebSocket message size in bytes (default 64 KB)
 *   now               clock function returning ms (default Date.now)
 *   fetch             fetch implementation used for the Spotify token exchange
 *                     and for pack sources
 *   publicBaseUrl     base of the links shown to users (env PUBLIC_BASE_URL,
 *                     default https://leeloobot.xyz)
 *   sourceHosts       hosts that pack sources may use: array or comma list
 *                     (env LEELOO_SOURCE_HOSTS, default site.api.espn.com,api.open-meteo.com)
 *   maxSourceUrls     distinct source URLs polled at one time, all crews (default 50)
 *   maxCrewSourceUrls distinct source URLs polled for one crew (default 6)
 *   maxCrewMembers    devices on the roster of one crew (default 12)
 *   maxDevicePacks    stored active packs (default 2000, oldest dropped)
 *   maxDeviceKeys     stored device keys (default 20000; keys of devices that
 *                     are in no crew are dropped first)
 *   sourceTickMs      how often the source scheduler looks for due URLs
 *                     (default 5000; 0 = no timer, call pollSources() yourself)
 *   log               logger with log/error (default console)
 *   telegramApiSecret, spotifyClientId, spotifyClientSecret, spotifyRedirectUri
 *                     default to the matching environment variables
 */
function createRelay(options = {}) {
  const now = options.now || Date.now;
  const log = options.log || console;
  const heartbeatMs = options.heartbeatMs ?? 30000;
  const saveDebounceMs = options.saveDebounceMs ?? 1000;
  const sweepMs = options.sweepMs ?? 60000;
  const queueLimit = options.queueLimit ?? 50;
  const queueTtlMs = options.queueTtlMs ?? 24 * 3600000;
  const joinMaxFailures = options.joinMaxFailures ?? 10;
  const joinWindowMs = options.joinWindowMs ?? 10 * 60000;
  const maxPayload = options.maxPayload ?? 64 * 1024;
  const fetchImpl = options.fetch || ((...args) => fetch(...args));
  const dataFile = options.dataFile === undefined
    ? (process.env.LEELOO_DATA_FILE || path.join(__dirname, 'data', 'state.json'))
    : options.dataFile;

  const SPOTIFY_CLIENT_ID = options.spotifyClientId ?? process.env.SPOTIFY_CLIENT_ID;
  const SPOTIFY_CLIENT_SECRET = options.spotifyClientSecret ?? process.env.SPOTIFY_CLIENT_SECRET;
  const SPOTIFY_REDIRECT_URI = options.spotifyRedirectUri ?? process.env.SPOTIFY_REDIRECT_URI;
  const TELEGRAM_API_SECRET = options.telegramApiSecret ?? process.env.TELEGRAM_API_SECRET ?? '';
  const publicBaseUrl = String(options.publicBaseUrl ?? process.env.PUBLIC_BASE_URL ?? 'https://leeloobot.xyz')
    .replace(/\/+$/, '');
  const sourceHosts = parseSourceHosts(options.sourceHosts ?? process.env.LEELOO_SOURCE_HOSTS);
  const sourceTickMs = options.sourceTickMs ?? 5000;
  const maxSourceUrls = options.maxSourceUrls ?? 50;
  const maxCrewSourceUrls = options.maxCrewSourceUrls ?? 6;
  const maxCrewMembers = options.maxCrewMembers ?? 12;
  const maxDevicePacks = options.maxDevicePacks ?? 2000;
  const maxDeviceKeys = options.maxDeviceKeys ?? 20000;
  const checkPack = (pack) => validatePack(pack, { sourceHosts });

  const app = express();
  const server = http.createServer(app);
  const wss = new WebSocket.Server({ server, maxPayload });

  // Live connections only (never persisted)
  const devices = new Map(); // device_id -> { ws, crew_code, device_name, connected_at }

  // Persisted state
  const crews = new Map();   // crew_code -> { created_at, telegram_users: Map<id, name>, members: Map<device_id, name> }
  const queues = new Map();  // device_id -> [ { crew_code, message } ]
  const pendingSpotifyTokens = new Map(); // device_id -> { tokens, timestamp }
  const agentTokens = new Map();    // sha256(token) -> { device_id, crew_code, created_at }
  const devicePacks = new Map();    // device_id -> active pack (valid; only for crew members)
  const deviceKeys = new Map();     // device_id -> sha256(device_key): proof that a socket is that device
  const pendingPackOps = new Map(); // device_id -> { message: pack_install | pack_reset, timestamp }

  // Failed crew attempts: key -> [timestamps]
  const joinFailures = new Map();

  // Memory only
  const connectCodes = new Map(); // code -> { device_id, crew_code, expires_at }
  const memberCrew = new Map();   // device_id -> crew_code: index of the rosters (a device is in one crew)
  const agentCalls = new Map();   // "send:<token hash>" / "pack:<token hash>" -> [timestamps]
  const sourceCache = new Map();  // url -> { values: Map<dot path, text>, hasData, lastAttempt, failing, inflight }
  const capLogged = new Set();    // "device url" pairs already logged as over a source cap

  let closed = false;

  // --- Persistence ---

  let saveTimer = null;
  let dirty = false;

  function serializeState() {
    const crewsOut = {};
    crews.forEach((crew, code) => {
      crewsOut[code] = {
        created_at: crew.created_at,
        // Pairs keep the key type: Telegram user ids are numbers
        telegram_users: Array.from(crew.telegram_users.entries()),
        members: Object.fromEntries(crew.members)
      };
    });
    return {
      version: STATE_VERSION,
      saved_at: now(),
      crews: crewsOut,
      queues: Object.fromEntries(queues),
      pendingSpotifyTokens: Object.fromEntries(pendingSpotifyTokens),
      agentTokens: Object.fromEntries(agentTokens), // hashes only, never the tokens
      devicePacks: Object.fromEntries(devicePacks),
      deviceKeys: Object.fromEntries(deviceKeys), // hashes only
      pendingPackOps: Object.fromEntries(pendingPackOps)
    };
  }

  function loadState() {
    if (!dataFile) return;
    let raw;
    try {
      raw = fs.readFileSync(dataFile, 'utf8');
    } catch (error) {
      if (error.code !== 'ENOENT') log.error(`[STATE] Cannot read ${dataFile}:`, error.message);
      return;
    }

    let state;
    try {
      state = JSON.parse(raw);
      if (!isPlainObject(state)) throw new Error('state is not an object');
    } catch (error) {
      // Keep the bad file for inspection and start empty
      const backup = `${dataFile}.corrupt-${Date.now()}`;
      try { fs.renameSync(dataFile, backup); } catch (_) { /* ignore */ }
      log.error(`[STATE] ${dataFile} is corrupt (${error.message}); moved to ${backup}, starting empty`);
      return;
    }

    if (isPlainObject(state.crews)) {
      for (const [code, saved] of Object.entries(state.crews)) {
        if (!isPlainObject(saved)) continue;
        const crew = {
          created_at: Number(saved.created_at) || now(),
          telegram_users: new Map(),
          members: new Map()
        };
        if (Array.isArray(saved.telegram_users)) {
          for (const pair of saved.telegram_users) {
            if (Array.isArray(pair) && pair.length === 2) crew.telegram_users.set(pair[0], String(pair[1]));
          }
        }
        if (isPlainObject(saved.members)) {
          for (const [id, name] of Object.entries(saved.members)) {
            crew.members.set(id, String(name));
            memberCrew.set(id, code);
          }
        }
        crews.set(code, crew);
      }
    }

    if (isPlainObject(state.queues)) {
      for (const [id, entries] of Object.entries(state.queues)) {
        if (!Array.isArray(entries)) continue;
        const valid = entries.filter((e) => isPlainObject(e) && typeof e.crew_code === 'string' && isPlainObject(e.message));
        if (valid.length) queues.set(id, valid);
      }
    }

    if (isPlainObject(state.pendingSpotifyTokens)) {
      for (const [id, pending] of Object.entries(state.pendingSpotifyTokens)) {
        if (isPlainObject(pending) && isPlainObject(pending.tokens)) pendingSpotifyTokens.set(id, pending);
      }
    }

    // Agent connect and packs. Older files do not have these keys.
    if (isPlainObject(state.agentTokens)) {
      for (const [hash, saved] of Object.entries(state.agentTokens)) {
        if (!/^[0-9a-f]{64}$/.test(hash) || !isPlainObject(saved)) continue;
        if (typeof saved.device_id !== 'string' || typeof saved.crew_code !== 'string') continue;
        agentTokens.set(hash, {
          device_id: saved.device_id,
          crew_code: saved.crew_code,
          created_at: Number(saved.created_at) || now()
        });
      }
    }

    if (isPlainObject(state.devicePacks)) {
      for (const [id, pack] of Object.entries(state.devicePacks)) {
        // The rules or the allowed hosts can change between two runs. The
        // device sends pack_active again after its next join. Packs of
        // devices that are in no crew are not kept.
        if (devicePacks.size >= maxDevicePacks) break;
        if (memberCrew.has(id) && checkPack(pack).length === 0) devicePacks.set(id, pack);
      }
    }

    if (isPlainObject(state.deviceKeys)) {
      for (const [id, hash] of Object.entries(state.deviceKeys)) {
        if (typeof hash === 'string' && /^[0-9a-f]{64}$/.test(hash)) deviceKeys.set(id, hash);
      }
    }

    if (isPlainObject(state.pendingPackOps)) {
      for (const [id, op] of Object.entries(state.pendingPackOps)) {
        if (!isPlainObject(op) || !isPlainObject(op.message)) continue;
        const message = op.message;
        const ok = message.type === 'pack_reset' ||
          (message.type === 'pack_install' && checkPack(message.pack).length === 0);
        if (ok) pendingPackOps.set(id, { message, timestamp: Number(op.timestamp) || now() });
      }
    }

    log.log(`[STATE] Loaded ${crews.size} crews, ${queues.size} offline queues, ${agentTokens.size} agent links from ${dataFile}`);
  }

  // Synchronous atomic write: temp file in the same directory, then rename.
  function flush() {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    if (!dataFile || !dirty) return;
    try {
      fs.mkdirSync(path.dirname(dataFile), { recursive: true });
      const tmp = `${dataFile}.${process.pid}.tmp`;
      // 0600: the file can hold pending Spotify tokens
      fs.writeFileSync(tmp, JSON.stringify(serializeState()), { mode: 0o600 });
      fs.renameSync(tmp, dataFile);
      dirty = false;
    } catch (error) {
      log.error(`[STATE] Cannot save ${dataFile}:`, error.message);
    }
  }

  function scheduleSave() {
    dirty = true;
    if (!dataFile || saveTimer || closed) return;
    saveTimer = setTimeout(() => {
      saveTimer = null;
      flush();
    }, saveDebounceMs);
    saveTimer.unref();
  }

  // --- Helper functions ---

  function getOrCreateCrew(crewCode) {
    if (!crews.has(crewCode)) {
      crews.set(crewCode, {
        created_at: now(),
        telegram_users: new Map(), // user_id -> display_name
        members: new Map()         // device_id -> display name (remembered while offline)
      });
      scheduleSave();
    }
    return crews.get(crewCode);
  }

  function isOnline(deviceId, crewCode) {
    const device = devices.get(deviceId);
    return !!device && device.crew_code === crewCode && device.ws.readyState === WebSocket.OPEN;
  }

  function send(ws, message) {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }

  function broadcastToCrew(crewCode, message, excludeDeviceId = null) {
    let sentCount = 0;
    const msgJson = JSON.stringify(message);

    devices.forEach((device, id) => {
      if (id !== excludeDeviceId &&
          device.crew_code === crewCode &&
          device.ws.readyState === WebSocket.OPEN) {
        device.ws.send(msgJson);
        sentCount++;
      }
    });

    return sentCount;
  }

  function countOnline(crewCode) {
    let count = 0;
    devices.forEach((device) => {
      if (device.crew_code === crewCode) count++;
    });
    return count;
  }

  // --- Store-and-forward ---

  function isExpired(entry) {
    return now() - (entry.message.timestamp || 0) >= queueTtlMs;
  }

  // Queue a relayed message for every roster device of the crew that is offline.
  function queueForOffline(crewCode, message, excludeDeviceId = null) {
    if (!QUEUEABLE_MSG_TYPES.has(message.msg_type)) return 0;
    const crew = crews.get(crewCode);
    if (!crew) return 0;

    let queued = 0;
    crew.members.forEach((_name, id) => {
      if (id === excludeDeviceId || isOnline(id, crewCode)) return;
      const queue = (queues.get(id) || []).filter((entry) => !isExpired(entry));
      queue.push({ crew_code: crewCode, message });
      while (queue.length > queueLimit) queue.shift(); // drop oldest
      queues.set(id, queue);
      queued++;
    });
    if (queued) scheduleSave();
    return queued;
  }

  // Relay a crew message to online devices and queue it for offline ones.
  function relayCrewMessage(crewCode, message, excludeDeviceId = null) {
    const sent = broadcastToCrew(crewCode, message, excludeDeviceId);
    const queued = queueForOffline(crewCode, message, excludeDeviceId);
    return { sent, queued };
  }

  function deliverQueued(ws, deviceId, crewCode) {
    const queue = queues.get(deviceId);
    if (!queue) return;
    let delivered = 0;
    for (const entry of queue) {
      if (entry.crew_code !== crewCode || isExpired(entry)) continue;
      send(ws, { ...entry.message, queued: true });
      delivered++;
    }
    queues.delete(deviceId);
    scheduleSave();
    if (delivered) log.log(`[QUEUE] Delivered ${delivered} queued messages to ${deviceId}`);
  }

  function deliverPendingSpotifyTokens(ws, deviceId) {
    const pending = pendingSpotifyTokens.get(deviceId);
    if (!pending) return;
    // Only deliver if less than 1 hour old (token expiry)
    if (now() - pending.timestamp < SPOTIFY_PENDING_TTL_MS) {
      send(ws, { type: 'spotify_auth_complete', tokens: pending.tokens });
      log.log(`[SPOTIFY] Delivered pending tokens to ${deviceId}`);
    }
    pendingSpotifyTokens.delete(deviceId);
    scheduleSave();
  }

  // --- Rate limit of failed crew attempts ---

  function recentFailures(key) {
    const list = joinFailures.get(key);
    if (!list) return [];
    const cutoff = now() - joinWindowMs;
    const recent = list.filter((t) => t > cutoff);
    if (recent.length) joinFailures.set(key, recent);
    else joinFailures.delete(key);
    return recent;
  }

  function isRateLimited(key) {
    return recentFailures(key).length >= joinMaxFailures;
  }

  function recordFailure(key) {
    const recent = recentFailures(key);
    recent.push(now());
    joinFailures.set(key, recent);
    // Hard cap in case of a flood between two sweeps
    if (joinFailures.size > 20000) sweep();
  }

  // --- Periodic cleanup ---

  function sweep() {
    for (const key of Array.from(joinFailures.keys())) recentFailures(key);

    let changed = false;
    queues.forEach((queue, id) => {
      const kept = queue.filter((entry) => !isExpired(entry));
      if (kept.length === queue.length) return;
      if (kept.length) queues.set(id, kept);
      else queues.delete(id);
      changed = true;
    });
    pendingSpotifyTokens.forEach((pending, id) => {
      if (now() - pending.timestamp >= SPOTIFY_PENDING_TTL_MS) {
        pendingSpotifyTokens.delete(id);
        changed = true;
      }
    });
    if (changed) scheduleSave();

    connectCodes.forEach((entry, code) => {
      if (now() >= entry.expires_at) connectCodes.delete(code);
    });
    agentCalls.forEach((list, key) => {
      if (!list.some((t) => now() - t < AGENT_SEND_WINDOW_MS)) agentCalls.delete(key);
    });
  }

  // --- Pack sources: the relay fetches each URL once for all devices ---

  function packSources(pack) {
    return pack && Array.isArray(pack.sources) ? pack.sources : [];
  }

  function isConnected(deviceId) {
    const device = devices.get(deviceId);
    return !!device && device.ws.readyState === WebSocket.OPEN;
  }

  // Keep the active pack of a crew member (the newest `maxDevicePacks` ones).
  function setDevicePack(deviceId, pack) {
    devicePacks.delete(deviceId); // a changed pack moves to the end
    devicePacks.set(deviceId, pack);
    while (devicePacks.size > maxDevicePacks) devicePacks.delete(devicePacks.keys().next().value);
    scheduleSave();
  }

  // url -> { every (smallest among the users, seconds), deviceIds, paths }
  // for the active packs of the crew members that are connected now.
  // The number of URLs is limited for all crews and for each crew. Devices
  // that connected first come first. A source over a limit is not polled.
  function sourceUsers() {
    const users = new Map();
    const crewUrls = new Map(); // crew_code -> Set of urls
    const skipped = new Set();

    devices.forEach((_device, id) => {
      const crewCode = memberCrew.get(id);
      if (!crewCode || !isConnected(id)) return;
      for (const source of packSources(devicePacks.get(id))) {
        let ofCrew = crewUrls.get(crewCode);
        if (!ofCrew) crewUrls.set(crewCode, ofCrew = new Set());
        if (!ofCrew.has(source.url)) {
          const crewFull = ofCrew.size >= maxCrewSourceUrls;
          if (crewFull || (!users.has(source.url) && users.size >= maxSourceUrls)) {
            const key = `${id} ${source.url}`;
            skipped.add(key);
            if (!capLogged.has(key)) {
              capLogged.add(key);
              log.log(`[SOURCE] Not polled for ${id}: ${source.url} (more than ${
                crewFull ? `${maxCrewSourceUrls} URLs in crew ${crewCode}` : `${maxSourceUrls} URLs in all`})`);
            }
            continue;
          }
          ofCrew.add(source.url);
        }
        const user = users.get(source.url) || { every: source.every, deviceIds: new Set(), paths: new Set() };
        user.every = Math.min(user.every, source.every);
        user.deviceIds.add(id);
        for (const dotPath of Object.values(source.fields)) user.paths.add(dotPath);
        users.set(source.url, user);
      }
    });

    capLogged.forEach((key) => {
      if (!skipped.has(key)) capLogged.delete(key);
    });
    return users;
  }

  // All values of a device's pack from the cache: { "game.state": "in", ... }
  // Sources that are not polled for this device (over a limit) give nothing.
  function packValues(deviceId, pack, users) {
    const values = {};
    for (const source of packSources(pack)) {
      const user = users.get(source.url);
      const entry = sourceCache.get(source.url);
      if (!user || !user.deviceIds.has(deviceId) || !entry) continue;
      for (const [field, dotPath] of Object.entries(source.fields)) {
        const value = entry.values.get(dotPath);
        if (value !== undefined) values[`${source.id}.${field}`] = value;
      }
    }
    return values;
  }

  function sendPackData(deviceId, users = sourceUsers()) {
    const pack = devicePacks.get(deviceId);
    if (!isConnected(deviceId) || !packSources(pack).length) return;
    send(devices.get(deviceId).ws, { type: 'pack_data', pack_id: pack.id, values: packValues(deviceId, pack, users) });
  }

  async function readLimitedText(res) {
    const declared = Number(res.headers && typeof res.headers.get === 'function' && res.headers.get('content-length'));
    if (declared > SOURCE_MAX_BYTES) throw new Error('reply is larger than 2 MB');

    if (res.body && typeof res.body.getReader === 'function') {
      const reader = res.body.getReader();
      const chunks = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > SOURCE_MAX_BYTES) {
          try { await reader.cancel(); } catch (_) { /* ignore */ }
          throw new Error('reply is larger than 2 MB');
        }
        chunks.push(Buffer.from(value));
      }
      return Buffer.concat(chunks).toString('utf8');
    }

    const text = await res.text();
    if (Buffer.byteLength(text, 'utf8') > SOURCE_MAX_BYTES) throw new Error('reply is larger than 2 MB');
    return text;
  }

  async function fetchSourceJson(url) {
    // Checked again here: the allowed hosts can differ from when the pack was accepted
    const problem = sourceUrlProblem(url, sourceHosts);
    if (problem) throw new Error(problem);

    const res = await fetchImpl(url, {
      method: 'GET',
      redirect: 'error', // never follow a redirect (it could leave the allowed hosts)
      headers: { Accept: 'application/json', 'User-Agent': `leeloo-relay/${RELAY_VERSION}` },
      signal: AbortSignal.timeout(SOURCE_TIMEOUT_MS)
    });
    if (!res || !res.ok) throw new Error(`HTTP ${res ? res.status : 'error'}`);
    if (res.redirected || (res.url && sourceUrlProblem(res.url, sourceHosts))) {
      throw new Error('redirected');
    }

    const data = JSON.parse(await readLimitedText(res));
    if (data === null || typeof data !== 'object') throw new Error('reply is not a JSON object');
    return data;
  }

  // Fetch one URL (one request at a time per URL), then send the values to
  // every connected device that uses it. Never rejects. Only the values that
  // the users ask for are kept, not the reply.
  function fetchSource(url) {
    let entry = sourceCache.get(url);
    if (!entry) {
      entry = { values: new Map(), hasData: false, lastAttempt: 0, failing: false, inflight: null };
      sourceCache.set(url, entry);
    }
    if (entry.inflight) return entry.inflight;

    entry.lastAttempt = now();
    entry.inflight = (async () => {
      let data = null;
      let failure = null;
      try {
        data = await fetchSourceJson(url);
      } catch (error) {
        failure = error;
      }
      entry.inflight = null;
      if (closed) return;

      const users = sourceUsers();
      const user = users.get(url);
      if (failure) {
        // The old values stay. Logged one time until the URL recovers.
        if (!entry.failing) log.error(`[SOURCE] ${url} failed: ${failure.message}`);
        entry.failing = true;
      } else {
        // A path with no value is kept as undefined: "asked, not there"
        entry.values = new Map();
        if (user) user.paths.forEach((dotPath) => entry.values.set(dotPath, extractValue(data, dotPath)));
        entry.hasData = true;
        if (entry.failing) log.log(`[SOURCE] ${url} works again`);
        entry.failing = false;
      }
      if (user) user.deviceIds.forEach((id) => sendPackData(id, users));
    })();
    return entry.inflight;
  }

  function isSourceDue(entry, user) {
    if (!entry) return true;
    const age = now() - entry.lastAttempt;
    if (age >= user.every * 1000) return true;
    if (entry.failing || age < SOURCE_MIN_GAP_MS) return false;
    // A user asks for a field that the last fetch did not take
    for (const dotPath of user.paths) {
      if (!entry.values.has(dotPath)) return true;
    }
    return false;
  }

  // Fetch every URL whose interval has passed. Called by a timer; tests call
  // it directly. Resolves when the fetches it started (or found running) are done.
  function pollSources() {
    const users = sourceUsers();

    sourceCache.forEach((entry, url) => {
      if (users.has(url) || entry.inflight) return;
      const old = now() - entry.lastAttempt >= SOURCE_KEEP_UNUSED_MS;
      if (old || sourceCache.size > maxSourceUrls * 2) sourceCache.delete(url);
    });

    const running = [];
    users.forEach((user, url) => {
      const entry = sourceCache.get(url);
      if (entry && entry.inflight) running.push(entry.inflight);
      else if (isSourceDue(entry, user)) running.push(fetchSource(url));
    });
    return Promise.all(running).then(() => undefined);
  }

  // Values for one device right now: from the cache if it is fresh, else fetch.
  function startDeviceSources(deviceId) {
    const sources = packSources(devicePacks.get(deviceId));
    if (!sources.length || !isConnected(deviceId)) return;

    const users = sourceUsers();
    let fetching = false;
    for (const url of new Set(sources.map((source) => source.url))) {
      const entry = sourceCache.get(url);
      const user = users.get(url);
      if (!user || !user.deviceIds.has(deviceId)) continue; // over a limit: not polled
      if (entry && entry.inflight) {
        fetching = true;
      } else if (isSourceDue(entry, user)) {
        fetchSource(url);
        fetching = true;
      }
    }
    // A fetch sends pack_data when it ends. With nothing to fetch, send now.
    if (!fetching) sendPackData(deviceId, users);
  }

  // --- Agent connect ---

  function tokensOfDevice(deviceId) {
    const hashes = [];
    agentTokens.forEach((entry, hash) => {
      if (entry.device_id === deviceId) hashes.push(hash);
    });
    return hashes;
  }

  function createConnectCode(deviceId, crewCode) {
    // One open code per device
    connectCodes.forEach((entry, code) => {
      if (entry.device_id === deviceId) connectCodes.delete(code);
    });
    let code = generateConnectCode();
    while (connectCodes.has(code)) code = generateConnectCode();
    connectCodes.set(code, { device_id: deviceId, crew_code: crewCode, expires_at: now() + AGENT_CODE_TTL_MS });
    return code;
  }

  // The entry of a code that is still good, or null.
  function lookupConnectCode(raw) {
    const code = cleanString(raw, 16).toUpperCase();
    const entry = connectCodes.get(code);
    if (!entry) return null;
    if (now() >= entry.expires_at) {
      connectCodes.delete(code);
      return null;
    }
    return { code, ...entry };
  }

  function createAgentToken(deviceId, crewCode) {
    const mine = tokensOfDevice(deviceId)
      .sort((a, b) => agentTokens.get(a).created_at - agentTokens.get(b).created_at);
    while (mine.length >= AGENT_TOKENS_PER_DEVICE) agentTokens.delete(mine.shift()); // drop oldest

    const token = crypto.randomBytes(32).toString('hex');
    agentTokens.set(sha256Hex(token), { device_id: deviceId, crew_code: crewCode, created_at: now() });
    scheduleSave();
    return token;
  }

  function removeAgentTokens(deviceId) {
    const hashes = tokensOfDevice(deviceId);
    for (const hash of hashes) {
      agentTokens.delete(hash);
      agentCalls.delete(`send:${hash}`);
      agentCalls.delete(`pack:${hash}`);
    }
    connectCodes.forEach((entry, code) => {
      if (entry.device_id === deviceId) connectCodes.delete(code);
    });
    if (hashes.length) scheduleSave();
    return hashes.length;
  }

  // Send a pack_install / pack_reset to a device, or keep it (only the newest
  // one) for its next connection. Returns true when it was sent now.
  function sendOrKeepPackOp(deviceId, message) {
    if (isConnected(deviceId)) {
      send(devices.get(deviceId).ws, message);
      if (pendingPackOps.delete(deviceId)) scheduleSave();
      return true;
    }
    pendingPackOps.set(deviceId, { message, timestamp: now() });
    scheduleSave();
    return false;
  }

  function deliverPendingPackOp(ws, deviceId) {
    const op = pendingPackOps.get(deviceId);
    if (!op) return;
    send(ws, op.message);
    pendingPackOps.delete(deviceId);
    scheduleSave();
    log.log(`[PACK] Delivered pending ${op.message.type} to ${deviceId}`);
  }

  // After register / create_crew / join_crew
  function afterBind(ws, deviceId) {
    deliverPendingPackOp(ws, deviceId);
    startDeviceSources(deviceId);
  }

  // --- MCP tools (docs/PACKS.md §3) ---

  function packArgument(args) {
    let pack = args.pack;
    if (typeof pack === 'string') {
      // Some agents send the pack as JSON text
      try { pack = JSON.parse(pack); } catch (_) { return { errors: ['pack: is not valid JSON'] }; }
    }
    if (pack === undefined) return { errors: ['pack: required'] };
    return { pack, errors: checkPack(pack) };
  }

  function packErrorResult(errors) {
    return toolText(
      `The pack is not valid. Nothing was changed. Correct these ${errors.length === 1 ? 'error' : 'errors'} and try again:\n` +
      errors.map((e) => `- ${e}`).join('\n'), true);
  }

  function warningText(pack) {
    const warnings = packWarnings(pack);
    return warnings.length ? `\nWarnings:\n${warnings.map((w) => `- ${w}`).join('\n')}` : '';
  }

  // Count one call of a kind ("send": to the crew, "pack": install / reset)
  // for a token. False when the token used up its budget of the window.
  function agentCallAllowed(kind, tokenHash, limit) {
    const key = `${kind}:${tokenHash}`;
    const recent = (agentCalls.get(key) || []).filter((t) => now() - t < AGENT_SEND_WINDOW_MS);
    const allowed = recent.length < limit;
    if (allowed) recent.push(now());
    agentCalls.set(key, recent);
    return allowed;
  }

  function reachText(sent, queued) {
    if (!sent && !queued) return 'There is no other Leeloo in the crew, so nobody got it.';
    return `${sent} ${sent === 1 ? 'Leeloo' : 'Leeloos'} got it now, ${queued} will get it when they come online.`;
  }

  function callTool(name, args, auth) {
    if (!MCP_TOOLS.some((tool) => tool.name === name)) throw new RpcError(-32602, `Unknown tool: ${name}`);

    // The link works only while the device is in the crew it was made for
    const crew = crews.get(auth.crew_code);
    if (!crew || !crew.members.has(auth.device_id) || memberCrew.get(auth.device_id) !== auth.crew_code) {
      return toolText('This Leeloo is not in its crew any more, so this connector link does not work. ' +
        'Ask the owner to say "connect my agent" to the Leeloo and to add the new connector link.', true);
    }
    const deviceId = auth.device_id;
    const ownerName = crew.members.get(deviceId);
    const tooMany = () => toolText(
      `Too many messages to the crew (limit: ${AGENT_SEND_LIMIT} in 10 minutes). Try again later.`, true);
    const tooManyPackOps = () => toolText(
      `Too many pack changes (limit: ${AGENT_PACK_OP_LIMIT} installs and resets in 10 minutes). Nothing was changed. ` +
      'Check packs with validate_pack, and try again later.', true);

    switch (name) {
      case 'get_status': {
        const online = [];
        devices.forEach((device, id) => {
          if (device.crew_code === auth.crew_code && isConnected(id)) online.push(device.device_name);
        });
        const active = devicePacks.get(deviceId);
        return toolText(JSON.stringify({
          owner: ownerName,
          owner_online: isConnected(deviceId),
          crew_members: allMemberNames(crew, null),
          online_devices: online,
          active_pack: active ? { id: active.id, name: active.name } : null
        }, null, 2));
      }

      case 'get_pack_guide':
        return toolText(
          `${PACK_GUIDE}\n\n` +
          `Source hosts that this relay allows: ${Array.from(sourceHosts).join(', ')}\n\n` +
          `## Example pack\n\n${JSON.stringify(EXAMPLE_PACK, null, 2)}\n`);

      case 'validate_pack': {
        const { pack, errors } = packArgument(args);
        if (errors.length) return packErrorResult(errors);
        return toolText(`The pack is valid.${warningText(pack)}`);
      }

      case 'install_pack': {
        const { pack, errors } = packArgument(args);
        if (errors.length) return packErrorResult(errors);
        if (!agentCallAllowed('pack', auth.hash, AGENT_PACK_OP_LIMIT)) return tooManyPackOps();
        const sentNow = sendOrKeepPackOp(deviceId, { type: 'pack_install', pack, from: 'agent' });
        log.log(`[AGENT] install_pack ${pack.id} for ${deviceId} (${sentNow ? 'sent' : 'queued'})`);
        return toolText((sentNow
          ? `Sent "${pack.name}" to ${ownerName}'s Leeloo. It is online and installs it now.`
          : `${ownerName}'s Leeloo is offline. "${pack.name}" is queued and will be installed when it connects again.`) +
          warningText(pack));
      }

      case 'share_pack': {
        const { pack, errors } = packArgument(args);
        if (errors.length) return packErrorResult(errors);
        if (!agentCallAllowed('send', auth.hash, AGENT_SEND_LIMIT)) return tooMany();
        const { sent, queued } = relayCrewMessage(auth.crew_code, {
          type: 'message',
          from_device: deviceId,
          from_name: ownerName,
          msg_type: 'pack_offer',
          payload: { pack },
          timestamp: now()
        }, deviceId);
        log.log(`[AGENT] share_pack ${pack.id} from ${deviceId} (${sent} devices, ${queued} queued)`);
        return toolText(`Offered "${pack.name}" to the crew in ${ownerName}'s name. ${reachText(sent, queued)} ` +
          `Each friend decides to use it or not.${warningText(pack)}`);
      }

      case 'reset_pack': {
        if (!agentCallAllowed('pack', auth.hash, AGENT_PACK_OP_LIMIT)) return tooManyPackOps();
        const sentNow = sendOrKeepPackOp(deviceId, { type: 'pack_reset' });
        log.log(`[AGENT] reset_pack for ${deviceId} (${sentNow ? 'sent' : 'queued'})`);
        return toolText(sentNow
          ? `${ownerName}'s Leeloo is online and goes back to the default look now.`
          : `${ownerName}'s Leeloo is offline. It will go back to the default look when it connects again.`);
      }

      case 'send_message': {
        const text = typeof args.text === 'string' ? args.text.trim() : '';
        const count = charCount(text);
        if (count < 1) return toolText('text: required, 1 to 500 characters. Nothing was sent.', true);
        if (count > 500) return toolText(`text: longer than 500 characters (it has ${count}). Nothing was sent.`, true);
        if (!agentCallAllowed('send', auth.hash, AGENT_SEND_LIMIT)) return tooMany();
        const { sent, queued } = relayCrewMessage(auth.crew_code, {
          type: 'message',
          from_device: deviceId,
          from_name: ownerName,
          msg_type: 'text',
          payload: { text },
          timestamp: now()
        }, deviceId);
        log.log(`[AGENT] send_message from ${deviceId} (${sent} devices, ${queued} queued)`);
        return toolText(`Message sent in ${ownerName}'s name. ${reachText(sent, queued)}`);
      }

      default:
        throw new RpcError(-32602, `Unknown tool: ${name}`);
    }
  }

  // One JSON-RPC message. Returns the response, or null when none is due
  // (notifications, and responses that a client sends to us).
  function handleRpc(message, auth) {
    if (!isPlainObject(message) || message.jsonrpc !== '2.0') {
      return rpcError(null, -32600, 'Invalid request: not a JSON-RPC 2.0 message');
    }
    const hasId = typeof message.id === 'string' || typeof message.id === 'number';
    if (typeof message.method !== 'string') {
      if (hasId && (message.result !== undefined || message.error !== undefined)) return null;
      return rpcError(hasId ? message.id : null, -32600, 'Invalid request: no method');
    }
    if (!hasId) return null; // notification

    const params = isPlainObject(message.params) ? message.params : {};
    try {
      let result;
      switch (message.method) {
        case 'initialize':
          result = {
            protocolVersion: MCP_PROTOCOL_VERSIONS.includes(params.protocolVersion)
              ? params.protocolVersion
              : MCP_DEFAULT_PROTOCOL_VERSION,
            capabilities: { tools: {} },
            serverInfo: { name: 'leeloo', version: RELAY_VERSION },
            instructions: MCP_INSTRUCTIONS
          };
          break;
        case 'ping':
          result = {};
          break;
        case 'tools/list':
          result = { tools: MCP_TOOLS };
          break;
        case 'tools/call': {
          if (typeof params.name !== 'string') throw new RpcError(-32602, 'Invalid params: name is required');
          const args = params.arguments === undefined ? {} : params.arguments;
          if (!isPlainObject(args)) throw new RpcError(-32602, 'Invalid params: arguments must be an object');
          result = callTool(params.name, args, auth);
          break;
        }
        default:
          throw new RpcError(-32601, `Method not found: ${message.method}`);
      }
      return { jsonrpc: '2.0', id: message.id, result };
    } catch (error) {
      if (error instanceof RpcError) return rpcError(message.id, error.code, error.message);
      log.error('[MCP] Internal error:', error.message);
      return rpcError(message.id, -32603, 'Internal error');
    }
  }

  // --- Crew membership helpers ---

  // Names of every known crew member except the given device:
  // roster devices (online or not) plus Telegram users, without duplicates.
  function allMemberNames(crew, selfDeviceId) {
    const names = new Set();
    crew.members.forEach((name, id) => {
      if (id !== selfDeviceId) names.add(name);
    });
    crew.telegram_users.forEach((name) => names.add(name));
    return Array.from(names);
  }

  // Attach a device id to this socket (replacing any older socket with the same id).
  function bindDevice(ws, ctx, deviceId, crewCode, deviceName) {
    const previous = devices.get(deviceId);
    if (previous && previous.ws !== ws) {
      // Same device on a new socket: the old one is stale. Its close handler
      // will see that it no longer owns the id and will not announce anything.
      try { previous.ws.close(4000, 'replaced by a new connection'); } catch (_) { /* ignore */ }
    }

    // This socket changed its device id: release the old one
    if (ctx.deviceId && ctx.deviceId !== deviceId) {
      const old = devices.get(ctx.deviceId);
      if (old && old.ws === ws) devices.delete(ctx.deviceId);
    }

    ctx.deviceId = deviceId;
    devices.set(deviceId, {
      ws,
      crew_code: crewCode,
      device_name: deviceName,
      connected_at: now()
    });

    if (crewCode) {
      if (memberCrew.get(deviceId) !== crewCode) {
        // The device moved to this crew: it leaves the other rosters, and
        // the agent links made for the old crew stop working.
        crews.forEach((other, code) => {
          if (code !== crewCode) other.members.delete(deviceId);
        });
        removeAgentTokens(deviceId);
        memberCrew.set(deviceId, crewCode);
        scheduleSave();
      }
      const crew = getOrCreateCrew(crewCode);
      if (crew.members.get(deviceId) !== deviceName) {
        crew.members.set(deviceId, deviceName);
        scheduleSave();
      }
    }
  }

  // --- Device identity ---
  // A device id is public (the crew sees it), so it does not prove anything.
  // The first bind of an id gives the device a secret key. Every later bind
  // of that id needs the key. The relay keeps only the hash.

  // True when this socket may bind deviceId. If not, the error was sent.
  function deviceKeyAccepted(ws, ctx, deviceId, data, ip) {
    const stored = deviceKeys.get(deviceId);
    if (!stored) return true; // first bind of this id (or a device from before the keys)

    // This socket already proved that it is the device
    const bound = devices.get(deviceId);
    if (ctx.deviceId === deviceId && bound && bound.ws === ws) return true;

    const given = typeof data.device_key === 'string' ? data.device_key : '';
    if (given && crypto.timingSafeEqual(Buffer.from(sha256Hex(given), 'hex'), Buffer.from(stored, 'hex'))) {
      return true;
    }

    const limitKey = `ip:${ip}`;
    if (isRateLimited(limitKey)) {
      sendRateLimited(ws);
      return false;
    }
    recordFailure(limitKey);
    send(ws, {
      type: 'error',
      error: 'device_key_required',
      message: 'This device id belongs to another device'
    });
    log.log(`[DEVICE] Refused a bind of ${deviceId} from ${ip}: no or wrong device key`);
    return false;
  }

  // The new key of a device id that has none (call after the bind), else undefined.
  function issueDeviceKey(deviceId) {
    if (deviceKeys.has(deviceId)) return undefined;
    const key = crypto.randomBytes(32).toString('hex');
    deviceKeys.set(deviceId, sha256Hex(key));
    if (deviceKeys.size > maxDeviceKeys) {
      // Oldest first; never the key of a crew member or of a connected device
      for (const id of deviceKeys.keys()) {
        if (deviceKeys.size <= maxDeviceKeys) break;
        if (!memberCrew.has(id) && !devices.has(id)) deviceKeys.delete(id);
      }
    }
    scheduleSave();
    return key;
  }

  // Reply to a bind. `device_key` is in it only when the key was made now.
  function sendBound(ws, reply) {
    const key = issueDeviceKey(reply.device_id);
    send(ws, key ? { ...reply, device_key: key } : reply);
  }

  // False (and the error was sent) when the roster of the crew is full.
  function crewHasRoom(ws, crewCode, deviceId) {
    const crew = crews.get(crewCode);
    if (!crew || crew.members.has(deviceId) || crew.members.size < maxCrewMembers) return true;
    send(ws, { type: 'error', error: 'crew_full', message: `Crew ${crewCode} is full (${maxCrewMembers} devices)` });
    return false;
  }

  // create_crew / register with a client-supplied crew code never fail on an
  // unknown code, so they could be used to get around the join limit. Devices
  // that are not yet on the crew roster spend from the same per-IP budget.
  function crewClaimAllowed(crewCode, deviceId, ip) {
    const crew = crews.get(crewCode);
    if (crew && deviceId && crew.members.has(deviceId)) return true;
    const key = `ip:${ip}`;
    if (isRateLimited(key)) return false;
    recordFailure(key);
    return true;
  }

  function sendRateLimited(ws) {
    send(ws, {
      type: 'error',
      error: 'rate_limited',
      message: 'Too many attempts, try again later'
    });
  }

  // --- HTTP ---

  app.use(express.static(path.join(__dirname, 'public')));

  // --- MCP connector of one device: POST /mcp/<token> (Streamable HTTP,
  // stateless, JSON replies). Registered before express.json() because it
  // needs the raw text: bad JSON must give a JSON-RPC error. ---

  const mcpBody = express.text({ type: () => true, limit: MCP_BODY_LIMIT });

  app.post('/mcp/:token', (req, res) => {
    res.set('Cache-Control', 'no-store');

    // The relay keeps only the hash of a token, so the lookup is by hash
    const token = String(req.params.token || '');
    const hash = /^[0-9a-f]{64}$/.test(token) ? sha256Hex(token) : null;
    const saved = hash ? agentTokens.get(hash) : null;
    if (!saved) {
      return res.status(401).json(rpcError(null, -32001,
        'Unknown or deleted connector link. Say "connect my agent" to the Leeloo to make a new one.'));
    }
    const auth = { ...saved, hash };

    return mcpBody(req, res, (bodyError) => {
      if (bodyError) {
        const tooLarge = bodyError.type === 'entity.too.large';
        return res.status(tooLarge ? 413 : 400).json(rpcError(null, -32600,
          tooLarge ? 'Invalid request: body is larger than 64 KB' : 'Invalid request: cannot read the body'));
      }

      let parsed;
      try {
        if (typeof req.body !== 'string') throw new Error('empty body');
        parsed = JSON.parse(req.body);
      } catch (_) {
        return res.status(400).json(rpcError(null, -32700, 'Parse error: the body is not valid JSON'));
      }

      if (Array.isArray(parsed)) {
        if (parsed.length === 0 || parsed.length > MCP_MAX_BATCH) {
          return res.status(400).json(rpcError(null, -32600,
            `Invalid request: a batch needs 1 to ${MCP_MAX_BATCH} messages`));
        }
        const replies = parsed.map((message) => handleRpc(message, auth)).filter(Boolean);
        return replies.length ? res.json(replies) : res.status(202).end();
      }

      const reply = handleRpc(parsed, auth);
      return reply ? res.json(reply) : res.status(202).end();
    });
  });

  // No SSE stream and no sessions: only POST
  app.all('/mcp/:token', (req, res) => {
    res.set('Allow', 'POST').status(405).json(rpcError(null, -32000, 'Method not allowed: use POST'));
  });

  app.use(express.json());

  // --- Agent connect pages: the QR code on the device opens /connect/<code> ---

  function connectHeaders(res) {
    res.set({
      'Cache-Control': 'no-store',
      'Referrer-Policy': 'no-referrer',
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
      'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
    });
    return res.type('html');
  }

  // The entry of the code in the URL. Sends the error page itself when there is none.
  function connectEntry(req, res) {
    const limitKey = `ip:${clientIp(req)}`;
    connectHeaders(res);
    if (isRateLimited(limitKey)) {
      res.status(429).send(htmlPage('Too many attempts', `
      <h1>Too many attempts</h1>
      <p>Wait ten minutes, then say "connect my agent" to your Leeloo again.</p>`));
      return null;
    }
    let entry = lookupConnectCode(req.params.code);
    // The device left the crew after it asked for the code
    if (entry && memberCrew.get(entry.device_id) !== entry.crew_code) entry = null;
    if (!entry) {
      recordFailure(limitKey); // wrong codes count, like wrong crew codes
      res.status(404).send(htmlPage('Link not found', `
      <h1>This link does not work</h1>
      <p>The code is wrong, was used before, or is older than 15 minutes.</p>
      <p>Say "connect my agent" to your Leeloo again and scan the new code.</p>`));
      return null;
    }
    return entry;
  }

  app.get('/connect/:code', (req, res) => {
    const entry = connectEntry(req, res);
    if (!entry) return;
    const crew = crews.get(entry.crew_code);
    const ownerName = (crew && crew.members.get(entry.device_id)) || 'your';
    res.send(htmlPage('Connect your agent', `
      <h1>Connect your AI agent</h1>
      <p>This makes a personal connector link for the Leeloo of <b>${escapeHtml(ownerName)}</b>.
         With it, an AI agent (Claude, ChatGPT) can change how the Leeloo looks and send messages to the crew in your name.</p>
      <form method="post" action="/connect/${escapeHtml(encodeURIComponent(entry.code))}">
        <button type="submit">Create my connector link</button>
      </form>
      <p>The link can be made one time. To take the access away, say "disconnect my agent" to your Leeloo.</p>`));
  });

  app.post('/connect/:code', (req, res) => {
    const entry = connectEntry(req, res);
    if (!entry) return;
    connectCodes.delete(entry.code); // one use

    const token = createAgentToken(entry.device_id, entry.crew_code);
    const connectorUrl = `${publicBaseUrl}/mcp/${token}`;
    log.log(`[AGENT] Connector link created for device ${entry.device_id}`);

    res.send(htmlPage('Your connector link', `
      <h1>Your connector link</h1>
      <textarea id="link" rows="4" readonly>${escapeHtml(connectorUrl)}</textarea>
      <p><button type="button" id="copy">Copy the link</button></p>
      <p class="warn"><b>This link is a key to your Leeloo. Do not share it.</b>
         It is shown only now. If you lose it, say "connect my agent" again.</p>
      <h2>Claude</h2>
      <ol>
        <li>Open Settings, then Connectors.</li>
        <li>Choose "Add custom connector".</li>
        <li>Give it the name Leeloo, paste the link, and add it.</li>
      </ol>
      <h2>ChatGPT</h2>
      <ol>
        <li>Open Settings and enable Developer Mode (in the connector settings).</li>
        <li>Add a connector.</li>
        <li>Paste the link as the server URL, with no authentication, and create it.</li>
      </ol>
      <p>Then ask your agent: "Make my Leeloo look like ..."</p>
      <p>To take the access away, say "disconnect my agent" to your Leeloo.</p>
      <script>
        (function () {
          var box = document.getElementById('link');
          var button = document.getElementById('copy');
          function selectLink() { box.focus(); box.select(); box.setSelectionRange(0, box.value.length); }
          function done() { button.textContent = 'Copied'; }
          function fallback() {
            selectLink();
            try { if (document.execCommand('copy')) { done(); return; } } catch (e) {}
            button.textContent = 'Link selected - copy it';
          }
          box.addEventListener('focus', selectLink);
          button.addEventListener('click', function () {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(box.value).then(done, fallback);
            } else {
              fallback();
            }
          });
        })();
      </script>`));
  });

  function telegramAuth(req, res, next) {
    if (TELEGRAM_API_SECRET) {
      const provided = req.headers['x-api-secret'];
      if (provided !== TELEGRAM_API_SECRET) {
        return res.status(401).json({ error: 'unauthorized' });
      }
    }
    if (!isPlainObject(req.body)) req.body = {};
    next();
  }

  app.get('/health', (req, res) => {
    res.json({
      status: 'ok',
      devices: devices.size,
      crews: crews.size,
      uptime: process.uptime()
    });
  });

  // --- Telegram API endpoints ---

  app.post('/api/telegram/crew/create', telegramAuth, (req, res) => {
    const { telegram_user_id, display_name } = req.body;

    if (!telegram_user_id) {
      return res.status(400).json({ error: 'telegram_user_id required' });
    }

    // Generate unique crew code
    let crewCode = generateCrewCode();
    while (crews.has(crewCode)) {
      crewCode = generateCrewCode();
    }

    const crew = getOrCreateCrew(crewCode);
    crew.telegram_users.set(telegram_user_id, cleanString(display_name, 64) || 'Unknown');
    scheduleSave();

    log.log(`[TELEGRAM] User ${telegram_user_id} created crew ${crewCode}`);
    res.json({ crew_code: crewCode });
  });

  app.post('/api/telegram/crew/join', telegramAuth, (req, res) => {
    const { telegram_user_id, display_name } = req.body;
    const code = normalizeCrewCode(req.body.crew_code);

    if (!telegram_user_id || !code) {
      return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
    }

    // With an API secret the caller is our own bot (all users share its IP), so
    // the Telegram user is the unit to limit. Without one, limit by client IP.
    const limitKey = TELEGRAM_API_SECRET ? `tg:${telegram_user_id}` : `ip:${clientIp(req)}`;
    const crew = crews.get(code);
    const alreadyMember = !!crew && crew.telegram_users.has(telegram_user_id);

    if (!alreadyMember && isRateLimited(limitKey)) {
      return res.status(429).json({ error: 'rate_limited', message: 'Too many attempts, try again later' });
    }

    if (!crew) {
      recordFailure(limitKey);
      return res.status(404).json({ error: 'crew_not_found', message: `Crew ${code} not found` });
    }

    crew.telegram_users.set(telegram_user_id, cleanString(display_name, 64) || 'Unknown');
    scheduleSave();

    const deviceCount = countOnline(code);
    log.log(`[TELEGRAM] User ${telegram_user_id} joined crew ${code} (${deviceCount} devices online)`);
    res.json({ success: true, crew_code: code, devices_online: deviceCount });
  });

  app.post('/api/telegram/message', telegramAuth, (req, res) => {
    const { telegram_user_id, payload } = req.body;
    const code = normalizeCrewCode(req.body.crew_code);
    const senderName = cleanString(req.body.sender_name, 64);
    const msgType = cleanString(req.body.msg_type, 32) || 'text';

    if (!telegram_user_id || !code) {
      return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
    }

    const crew = crews.get(code);

    if (!crew || !crew.telegram_users.has(telegram_user_id)) {
      return res.status(403).json({ error: 'not_in_crew', message: 'You are not a member of this crew' });
    }

    if (msgType === 'pack_offer') {
      const offerErrors = checkPack(isPlainObject(payload) ? payload.pack : undefined);
      if (offerErrors.length) {
        return res.status(400).json({ error: 'invalid_pack', errors: offerErrors });
      }
    }

    // Keep display name fresh (user may have changed their Telegram name)
    if (senderName && crew.telegram_users.get(telegram_user_id) !== senderName) {
      crew.telegram_users.set(telegram_user_id, senderName);
      scheduleSave();
    }

    // Build the message in the format devices expect (matches leeloo_client.py)
    const message = {
      type: 'message',
      from_device: 'telegram',
      from_name: senderName || 'Phone',
      msg_type: msgType,
      payload: isPlainObject(payload) ? payload : {},
      timestamp: now()
    };

    const { sent, queued } = relayCrewMessage(code, message);

    log.log(`[TELEGRAM] ${message.from_name} sent ${msgType} to crew ${code} (${sent} devices, ${queued} queued)`);
    res.json({ success: true, devices_reached: sent, devices_queued: queued });
  });

  app.get('/api/telegram/crew/status', telegramAuth, (req, res) => {
    const code = normalizeCrewCode(req.query.crew_code);

    if (!code) {
      return res.status(400).json({ error: 'crew_code required' });
    }

    let deviceCount = 0;
    const deviceNames = [];
    devices.forEach((device) => {
      if (device.crew_code === code) {
        deviceCount++;
        deviceNames.push(device.device_name);
      }
    });

    const crew = crews.get(code);
    const telegramUserNames = crew
      ? Array.from(crew.telegram_users.values())
      : [];
    res.json({
      crew_code: code,
      exists: !!(crew || deviceCount > 0),
      devices_online: deviceCount,
      device_names: deviceNames,
      telegram_users: telegramUserNames.length,
      telegram_user_names: telegramUserNames
    });
  });

  // --- Spotify OAuth callback ---

  app.get('/spotify/callback', async (req, res) => {
    const { code, state } = req.query;

    if (!code || typeof code !== 'string') {
      return res.status(400).send('No authorization code provided');
    }

    const deviceId = cleanString(state, 128);
    if (!deviceId) {
      return res.status(400).send('No device id (state) provided');
    }

    try {
      const tokenResponse = await fetchImpl('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Authorization': 'Basic ' + Buffer.from(SPOTIFY_CLIENT_ID + ':' + SPOTIFY_CLIENT_SECRET).toString('base64')
        },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          code: code,
          redirect_uri: SPOTIFY_REDIRECT_URI
        })
      });

      let tokens = null;
      try {
        tokens = await tokenResponse.json();
      } catch (_) {
        tokens = null;
      }

      if (!tokenResponse.ok || !isPlainObject(tokens) || tokens.error || !tokens.access_token) {
        const reason = isPlainObject(tokens) && tokens.error ? tokens.error : `HTTP ${tokenResponse.status}`;
        log.error(`[SPOTIFY] Token exchange failed for device ${deviceId}: ${reason}`);
        return res.status(502).type('text/plain')
          .send('Spotify did not accept the authorization. Please scan the code and try again.');
      }

      const tokenPayload = {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        expires_in: tokens.expires_in
      };

      const device = devices.get(deviceId);

      if (device && device.ws.readyState === WebSocket.OPEN) {
        // Device is online — send tokens immediately
        send(device.ws, { type: 'spotify_auth_complete', tokens: tokenPayload });
        log.log(`[SPOTIFY] Tokens sent to device ${deviceId}`);
      } else {
        // Device not connected yet (e.g. still in portal AP mode)
        // Store tokens for delivery when device connects
        pendingSpotifyTokens.set(deviceId, { tokens: tokenPayload, timestamp: now() });
        scheduleSave();
        log.log(`[SPOTIFY] Tokens stored for device ${deviceId} (pending delivery)`);
      }

      res.send(`
      <html>
        <head><title>LEELOO - Spotify Connected</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px; background: #1A1D2E; color: #7beec0;">
          <h1>Spotify Connected!</h1>
          <p style="color: #A7AFD4;">Your LEELOO will start showing your music once it boots up.</p>
          <p style="color: #A7AFD4;">You can close this window.</p>
        </body>
      </html>
    `);
    } catch (error) {
      log.error('Spotify OAuth error:', error);
      res.status(500).send('Failed to complete Spotify authorization');
    }
  });

  // --- WebSocket connection handling ---

  wss.on('connection', (ws, req) => {
    const ip = clientIp(req);
    log.log('New WebSocket connection from:', ip);

    const ctx = { deviceId: null };
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });

    // The device entry of this socket, or null if it has none (or was replaced)
    function currentDevice() {
      if (!ctx.deviceId) return null;
      const device = devices.get(ctx.deviceId);
      return device && device.ws === ws ? device : null;
    }

    function requestedDeviceId(data) {
      return cleanString(data.device_id, 128) || ctx.deviceId || generateDeviceId();
    }

    ws.on('message', (message) => {
      try {
        const data = JSON.parse(message);
        if (!isPlainObject(data)) return;

        switch (data.type) {
          case 'register': {
            const deviceId = cleanString(data.device_id, 128) || generateDeviceId();
            const crewCode = normalizeCrewCode(data.crew_code) || null;
            const deviceName = cleanString(data.device_name, 64) || 'LEELOO';

            if (!deviceKeyAccepted(ws, ctx, deviceId, data, ip)) break;

            if (crewCode && !crewClaimAllowed(crewCode, deviceId, ip)) {
              sendRateLimited(ws);
              break;
            }
            if (!crewCode && !deviceKeys.has(deviceId)) {
              // A new device id without a crew makes state: same per-IP budget
              if (isRateLimited(`ip:${ip}`)) {
                sendRateLimited(ws);
                break;
              }
              recordFailure(`ip:${ip}`);
            }
            if (crewCode && !crewHasRoom(ws, crewCode, deviceId)) break;

            bindDevice(ws, ctx, deviceId, crewCode, deviceName);

            sendBound(ws, { type: 'registered', device_id: deviceId });

            if (crewCode) deliverQueued(ws, deviceId, crewCode);
            deliverPendingSpotifyTokens(ws, deviceId);
            afterBind(ws, deviceId);

            log.log(`Device registered: ${deviceId} (${deviceName}) crew=${crewCode}`);
            break;
          }

          case 'create_crew': {
            // Device creates a new crew (optionally with a pre-assigned code from portal setup)
            const requestedCode = normalizeCrewCode(data.crew_code);
            const deviceId = requestedDeviceId(data);
            const deviceName = cleanString(data.display_name, 64) || cleanString(data.device_name, 64) || 'LEELOO';

            if (!deviceKeyAccepted(ws, ctx, deviceId, data, ip)) break;

            let crewCode = requestedCode || generateCrewCode();
            while (!requestedCode && crews.has(crewCode)) {
              crewCode = generateCrewCode();
            }

            if (!crewClaimAllowed(crewCode, deviceId, ip)) {
              sendRateLimited(ws);
              break;
            }
            if (!crewHasRoom(ws, crewCode, deviceId)) break;

            bindDevice(ws, ctx, deviceId, crewCode, deviceName);

            sendBound(ws, { type: 'crew_created', device_id: deviceId, crew_code: crewCode });

            deliverQueued(ws, deviceId, crewCode);
            deliverPendingSpotifyTokens(ws, deviceId);
            afterBind(ws, deviceId);

            log.log(`Device ${deviceId} created crew ${crewCode}`);
            break;
          }

          case 'join_crew': {
            const joinCode = normalizeCrewCode(data.crew_code);
            const deviceId = requestedDeviceId(data);
            const deviceName = cleanString(data.display_name, 64) || cleanString(data.device_name, 64) || 'LEELOO';
            const crew = crews.get(joinCode);

            if (!deviceKeyAccepted(ws, ctx, deviceId, data, ip)) break;

            // A device already on the roster is not guessing: never limit its rejoin
            const knownMember = !!crew && crew.members.has(deviceId);
            const limitKey = `ip:${ip}`;

            if (!knownMember && isRateLimited(limitKey)) {
              sendRateLimited(ws);
              break;
            }

            if (!crew) {
              recordFailure(limitKey);
              send(ws, {
                type: 'error',
                error: 'invalid_crew_code',
                message: `Crew ${joinCode} not found`
              });
              break;
            }
            if (!crewHasRoom(ws, joinCode, deviceId)) break;

            bindDevice(ws, ctx, deviceId, joinCode, deviceName);

            // Count crew members and collect existing member names (excluding self)
            let memberCount = 0;
            const memberNames = [];
            devices.forEach((d, id) => {
              if (d.crew_code === joinCode) {
                memberCount++;
                if (id !== deviceId) memberNames.push(d.device_name);
              }
            });

            sendBound(ws, {
              type: 'crew_joined',
              device_id: deviceId,
              crew_code: joinCode,
              crew_members: memberCount,
              member_names: memberNames,
              all_member_names: allMemberNames(crew, deviceId)
            });

            // Messages missed while offline come right after crew_joined
            deliverQueued(ws, deviceId, joinCode);
            deliverPendingSpotifyTokens(ws, deviceId);
            afterBind(ws, deviceId);

            // Notify others
            broadcastToCrew(joinCode, {
              type: 'member_joined',
              device_id: deviceId,
              display_name: deviceName
            }, deviceId);

            log.log(`Device ${deviceId} joined crew ${joinCode} (${memberCount} members)`);
            break;
          }

          case 'message': {
            // Generic message relay (text, song_push, nudge, hang_propose, etc.)
            const msgSender = currentDevice();
            if (!msgSender || !msgSender.crew_code) break;

            const relayMsg = {
              type: 'message',
              from_device: ctx.deviceId,
              from_name: msgSender.device_name,
              msg_type: cleanString(data.msg_type, 32) || 'text',
              payload: isPlainObject(data.payload) ? data.payload : {},
              timestamp: now()
            };

            if (relayMsg.msg_type === 'pack_offer') {
              // Friends only ever get packs that passed the check
              const offerErrors = checkPack(relayMsg.payload.pack);
              if (offerErrors.length) {
                log.log(`[PACK] Dropped invalid pack_offer from ${ctx.deviceId}: ${offerErrors[0]}`);
                break;
              }
              relayMsg.payload = { pack: relayMsg.payload.pack };
            }

            const { sent, queued } = relayCrewMessage(msgSender.crew_code, relayMsg, ctx.deviceId);
            log.log(`[MSG] ${msgSender.device_name} sent ${relayMsg.msg_type} to ${sent} devices (${queued} queued)`);
            break;
          }

          case 'share_music': {
            // Legacy music sharing
            const sender = currentDevice();
            if (!sender || !sender.crew_code) break;

            const sentCount = broadcastToCrew(sender.crew_code, {
              type: 'music_shared',
              spotify_uri: data.spotify_uri,
              artist: data.artist,
              track: data.track,
              album: data.album,
              pushed_by: sender.device_name,
              timestamp: now()
            }, ctx.deviceId);

            log.log(`Music shared from ${sender.device_name} to ${sentCount} devices`);
            break;
          }

          case 'reaction': {
            // Legacy reaction
            const reactor = currentDevice();
            if (!reactor || !reactor.crew_code) break;

            broadcastToCrew(reactor.crew_code, {
              type: 'reaction',
              reaction_type: data.reaction_type,
              from: reactor.device_name,
              timestamp: now()
            }, ctx.deviceId);
            break;
          }

          case 'agent_connect_request': {
            // "connect my agent": a one-use code for the QR code on the screen
            const owner = currentDevice();
            if (!owner || !owner.crew_code) {
              send(ws, { type: 'error', error: 'not_in_crew', message: 'Join a crew before you connect an agent' });
              break;
            }
            const code = createConnectCode(ctx.deviceId, owner.crew_code);
            send(ws, {
              type: 'agent_connect',
              code,
              url: `${publicBaseUrl}/connect/${code}`,
              expires_in: Math.round(AGENT_CODE_TTL_MS / 1000)
            });
            log.log(`[AGENT] Connect code created for device ${ctx.deviceId}`);
            break;
          }

          case 'agent_disconnect': {
            // "disconnect my agent": every connector link of this device stops working
            if (!currentDevice()) break;
            const removed = removeAgentTokens(ctx.deviceId);
            send(ws, { type: 'agent_disconnected', removed });
            log.log(`[AGENT] Removed ${removed} connector links of device ${ctx.deviceId}`);
            break;
          }

          case 'pack_active': {
            // The pack the device shows now (null = default look). The relay
            // fetches its sources while the device is connected. Only for a
            // device that is in a crew: strangers cannot make the relay poll.
            const member = currentDevice();
            const memberOf = member && member.crew_code ? crews.get(member.crew_code) : null;
            if (!memberOf || !memberOf.members.has(ctx.deviceId)) break;

            const pack = data.pack === undefined ? null : data.pack;
            const packErrors = pack === null ? [] : checkPack(pack);
            if (packErrors.length) {
              log.log(`[PACK] Ignored invalid pack_active from ${ctx.deviceId}: ${packErrors[0]}`);
            }
            if (pack === null || packErrors.length) {
              // No pack, or one the relay cannot serve: stop its sources
              if (devicePacks.delete(ctx.deviceId)) scheduleSave();
              break;
            }
            if (JSON.stringify(devicePacks.get(ctx.deviceId)) !== JSON.stringify(pack)) {
              // A changed pack can start fetches: only so many per connection
              const recent = (member.packChanges || []).filter((t) => now() - t < AGENT_SEND_WINDOW_MS);
              if (recent.length >= PACK_ACTIVE_LIMIT) {
                log.log(`[PACK] Ignored pack_active from ${ctx.deviceId}: more than ${PACK_ACTIVE_LIMIT} changes in 10 minutes`);
                break;
              }
              recent.push(now());
              member.packChanges = recent;
              setDevicePack(ctx.deviceId, pack);
            }
            startDeviceSources(ctx.deviceId);
            break;
          }

          case 'ping':
            send(ws, { type: 'pong' });
            break;

          default:
            log.log('Unknown message type:', data.type);
        }
      } catch (error) {
        log.error('Error handling message:', error.message);
      }
    });

    ws.on('close', () => {
      // Only clean up if this socket still owns the device id. After a
      // reconnect the id belongs to the new socket and must stay registered.
      const device = currentDevice();
      if (!device) return;

      devices.delete(ctx.deviceId);
      if (device.crew_code) {
        broadcastToCrew(device.crew_code, {
          type: 'member_offline',
          device_id: ctx.deviceId,
          display_name: device.device_name
        }, ctx.deviceId);
      }
      log.log(`Device disconnected: ${ctx.deviceId}`);
    });

    ws.on('error', (error) => {
      log.error('WebSocket error:', error.message);
    });
  });

  // --- Heartbeat: drop sockets that did not answer the previous ping ---

  const heartbeatTimer = setInterval(() => {
    wss.clients.forEach((ws) => {
      if (ws.isAlive === false) {
        ws.terminate();
        return;
      }
      ws.isAlive = false;
      try { ws.ping(); } catch (_) { /* socket is closing */ }
    });
  }, heartbeatMs);
  heartbeatTimer.unref();

  const sweepTimer = setInterval(sweep, sweepMs);
  sweepTimer.unref();

  const sourceTimer = sourceTickMs > 0 ? setInterval(pollSources, sourceTickMs) : null;
  if (sourceTimer) sourceTimer.unref();

  loadState();

  // --- Lifecycle ---

  function listen(port = 0, host) {
    return new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, host, () => {
        server.removeListener('error', reject);
        resolve(server.address().port);
      });
    });
  }

  // Stop timers, write the state to disk and close every connection.
  function close() {
    if (closed) return Promise.resolve();
    closed = true;
    clearInterval(heartbeatTimer);
    clearInterval(sweepTimer);
    if (sourceTimer) clearInterval(sourceTimer);
    flush();

    return new Promise((resolve) => {
      wss.clients.forEach((client) => client.terminate());
      wss.close(() => {
        if (!server.listening) return resolve();
        server.close(() => resolve());
        if (server.closeAllConnections) server.closeAllConnections();
      });
    });
  }

  return {
    app,
    server,
    wss,
    listen,
    close,
    flush,
    sweep,
    pollSources,
    validatePack: checkPack,
    dataFile,
    // Exposed for tests and debugging
    state: {
      devices, crews, queues, pendingSpotifyTokens, joinFailures,
      agentTokens, devicePacks, pendingPackOps, connectCodes, sourceCache,
      deviceKeys, memberCrew
    }
  };
}

module.exports = {
  createRelay,
  generateCrewCode,
  QUEUEABLE_MSG_TYPES,
  validatePack,
  packWarnings,
  extractValue,
  PACK_GUIDE,
  EXAMPLE_PACK,
  DEFAULT_SOURCE_HOSTS,
  MCP_TOOLS
};

if (require.main === module) {
  const PORT = process.env.PORT || 3000;
  const relay = createRelay();

  relay.server.listen(PORT, () => {
    console.log(`LEELOO Relay Server running on port ${PORT}`);
    console.log(`WebSocket endpoint: ws://localhost:${PORT}`);
    console.log(`Telegram API: http://localhost:${PORT}/api/telegram/...`);
    console.log(`State file: ${relay.dataFile || '(persistence disabled)'}`);
  });

  // Graceful shutdown (PM2 sends SIGINT, systemd/docker send SIGTERM)
  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log('Shutting down gracefully...');
    relay.close().then(() => process.exit(0));
    setTimeout(() => process.exit(0), 1500).unref();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
