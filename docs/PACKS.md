# LEELOO Packs and Agent Connect

A **pack** changes how a Leeloo looks and what one panel shows. A pack is
**data, not code**: one JSON file. The device draws it with a fixed set of
parts, so a pack from an agent or a friend cannot run commands on the Pi.

An **agent** (Claude, ChatGPT, ...) makes packs through the **agent connect**
link of the relay server.

Example: `packs/unc-hoops.json`.

## 1. Pack format (version 1)

The JSON text of a pack is 8,000 bytes at most. Unknown keys are an error.

| Key | Type | Rules |
|-----|------|-------|
| `pack` | number | Must be `1` |
| `id` | string | `^[a-z0-9-]{1,32}$` |
| `name` | string | 1 to 24 characters |
| `theme` | object | Optional. See below |
| `sources` | array | Optional. 3 at most |
| `panels` | array | Optional. 6 at most |
| `rotate_seconds` | number | Optional. 5 to 600. Default 20 |

### theme

- `colors`: object. Keys from `bg`, `green`, `purple`, `lavender`, `tan`,
  `rose`, `white`, `yellow`, `orange`. Each value `#RRGGBB`.
  (`tan` = weather box, `purple` = time box, `lavender` = outer frame and
  messages box, `green` = album box.)
- `header`: string, 1 to 12 characters. Replaces `LEELOO v1.0`.
- `labels`: object. Keys from `weather`, `time`, `messages`, `album`. Each
  value 1 to 10 characters.

### sources

Each source: `{ "id", "url", "every", "fields" }`.

- `id`: `^[a-z][a-z0-9_]{0,15}$`, unique in the pack.
- `url`: `https://` only. The host must be in the relay's allowed list
  (default: `site.api.espn.com`, `api.open-meteo.com`).
- `every`: seconds between fetches, 30 to 3600.
- `fields`: object, 1 to 12 entries. Key `^[a-z][a-z0-9_]{0,15}$`. Value: a
  dot path into the JSON reply. A number is an array index
  (`team.nextEvent.0.shortName`). 200 characters at most.

The **relay** fetches each URL (one time for all devices that use it) and
sends the values to the devices. A value is made a string of 40 characters at
most. If the path ends at an object that has `displayValue`, that is used.
A missing path gives no value. The full name of a value is `source_id.field`,
for example `game.state`.

### panels

All panels in version 1 fill the **weather box** (three rows). Two forms:

1. Built-in: `{ "slot": "weather", "builtin": "weather" }` — the normal
   weather panel takes part in the rotation.
2. Custom: `{ "slot": "weather", "title", "rows", "when", "priority" }`
   - `title`: optional, 1 to 10 characters. Label of the box for this panel.
   - `rows`: 1 to 3 rows. Each row: `{ "left", "right", "bar" }`.
     - `left`: required text template, 28 characters at most (the text outside `{...}` is counted).
     - `right`: optional text template, 14 characters at most (counted the same way).
     - `bar`: optional `{ "value": template, "max": number > 0 }`. Draws the
       12-box slider at the right. A row has `right` or `bar`, not the two.
   - A template puts values in with `{source_id.field}`. A value that is not
     there shows as `--`.
   - `when`: optional condition `{ "field": "game.state", <op> }` with one
     of `"equals": string`, `"not_equals": string`, `"exists": true|false`.
     Both forms can have `when` and `priority`.
   - `priority`: optional integer, -100 to 100. Default 0.

**Which panel shows:** take the panels whose `when` is true (no `when` =
true). Keep those with the highest `priority`. If more than one stays, show
them in turn, each for `rotate_seconds`:
`index = floor(unix_time / rotate_seconds) % count`. If none stays, or the
pack has no panels, show the built-in weather.

## 2. Messages between device and relay (WebSocket)

| Direction | Message | Purpose |
|-----------|---------|---------|
| device → relay | `{type:"agent_connect_request"}` | Ask for a connect code |
| relay → device | `{type:"agent_connect", code, url, expires_in}` | `url` goes in the QR code on the screen |
| relay → device | `{type:"pack_install", pack, from}` | Install on this device (from the owner's agent) |
| relay → device | `{type:"pack_reset"}` | Go back to the default look |
| device → relay | `{type:"pack_active", pack}` | The pack that is active now (`null` = default). Sent after each change and after each crew join. The relay starts or stops the sources |
| relay → device | `{type:"pack_data", pack_id, values}` | Source values, e.g. `{"game.state":"in"}` |
| relay → crew | `{type:"message", msg_type:"pack_offer", payload:{pack}, from_name}` | A friend (or the friend's agent) offers a pack. Queued for offline devices |

A device **never** installs an offered pack automatically. The user says
"use it". The owner's own agent installs directly.

## 3. Agent connect

1. The user says **"connect my agent"**. The device sends
   `agent_connect_request` and shows a QR code for
   `https://leeloobot.xyz/connect/<code>` (code valid 15 minutes, one use).
2. The page has one button. It makes the personal **connector URL**
   `https://leeloobot.xyz/mcp/<token>` and shows how to add it to Claude or
   ChatGPT as a custom connector. The token is the key to this one device.
   The relay keeps only a hash of it.
3. The connector is an MCP server (JSON-RPC over HTTP POST). Tools:

| Tool | Does |
|------|------|
| `get_status` | Owner name, crew members, online devices, active pack |
| `get_pack_guide` | This pack format, with the example |
| `validate_pack` | Checks a pack. Returns the list of errors |
| `install_pack` | Validates, then installs on the owner's Leeloo |
| `share_pack` | Offers a pack to the crew (each friend decides) |
| `reset_pack` | Default look on the owner's Leeloo |
| `send_message` | Sends a text message to the crew in the owner's name |

"Disconnect my agent" (voice) deletes all tokens of the device.

## 4. Voice commands

| Say | Action |
|-----|--------|
| "connect my agent" | Shows the connect QR code |
| "disconnect my agent" | Deletes the agent's access |
| "use it" (after an offer) | Installs the offered pack |
| "default look" | Removes the pack |
