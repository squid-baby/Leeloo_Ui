#!/usr/bin/env python3
"""
LEELOO Telegram Bot
Handles device pairing and messaging from phone to LEELOO devices.
Communicates with the relay server (server.js) via HTTP API.
"""

import os
import re
import json
import time
import asyncio
import logging
import tempfile

try:
    import aiohttp
except ImportError:
    print("Installing aiohttp...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'aiohttp'])
    import aiohttp

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
except ImportError:
    print("Installing python-telegram-bot...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'python-telegram-bot'])
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from telegram.error import BadRequest, TelegramError

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
log = logging.getLogger('leeloo-bot')

# Config from environment
BOT_TOKEN = os.environ.get('LEELOO_BOT_TOKEN', '')
RELAY_API_URL = os.environ.get('RELAY_API_URL', 'http://localhost:3000')
TELEGRAM_API_SECRET = os.environ.get('TELEGRAM_API_SECRET', '')
STATE_FILE = os.environ.get('LEELOO_BOT_STATE_FILE') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'bot_state.json'
)
RELAY_TIMEOUT_S = 10

CREW_CODE_RE = re.compile(r'^LEELOO-[A-Z0-9]{4}$')

# Which crew each Telegram user belongs to. Persisted to STATE_FILE so a bot
# restart does not make everyone "join a crew first" again.
user_state = {}  # telegram_user_id (int) -> {crew_code, display_name}


# --- Persistent user state ---

def load_state():
    """Load user_state from STATE_FILE. A missing or corrupt file means empty state."""
    user_state.clear()
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.error(f"State file {STATE_FILE} unreadable ({e}); starting with empty state")
        try:
            # Keep the broken file for inspection instead of overwriting it
            os.replace(STATE_FILE, STATE_FILE + '.corrupt')
        except OSError:
            pass
        return

    users = raw.get('users') if isinstance(raw, dict) else None
    if not isinstance(users, dict):
        log.error(f"State file {STATE_FILE} has unexpected content; starting with empty state")
        return

    for key, entry in users.items():
        try:
            user_id = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        crew_code = entry.get('crew_code')
        if not isinstance(crew_code, str) or not crew_code:
            continue
        display_name = entry.get('display_name')
        user_state[user_id] = {
            'crew_code': crew_code,
            'display_name': display_name if isinstance(display_name, str) and display_name else 'Phone',
        }
    log.info(f"Loaded {len(user_state)} user(s) from {STATE_FILE}")


def save_state():
    """Write user_state to STATE_FILE atomically. Never raises."""
    tmp_path = None
    try:
        directory = os.path.dirname(os.path.abspath(STATE_FILE))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix='.bot_state.', suffix='.tmp', dir=directory)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'users': {str(uid): entry for uid, entry in user_state.items()}}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STATE_FILE)
        tmp_path = None
    except Exception as e:
        log.error(f"Could not save state to {STATE_FILE}: {e}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def set_user_crew(user_id: int, crew_code: str, display_name: str):
    """Remember (and persist) which crew a user belongs to."""
    user_state[user_id] = {'crew_code': crew_code, 'display_name': display_name or 'Phone'}
    save_state()


def forget_user_crew(user_id: int):
    """Drop (and persist) a user's crew membership."""
    if user_state.pop(user_id, None) is not None:
        save_state()


# --- Relay API ---

def api_headers():
    """Build headers for relay API requests."""
    headers = {'Content-Type': 'application/json'}
    if TELEGRAM_API_SECRET:
        headers['X-Api-Secret'] = TELEGRAM_API_SECRET
    return headers


async def _relay_request(method: str, path: str, **kwargs) -> dict:
    """Call the relay API. Always returns a dict and never raises.

    On any failure the dict has an 'error' key:
      network_error  - relay unreachable / timed out
      bad_response   - reply was not a JSON object
      <relay error>  - the relay's own error code (crew_not_found, rate_limited, ...)
    and, when an HTTP reply was received, 'status' with the HTTP status code.
    """
    url = f'{RELAY_API_URL}{path}'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=RELAY_TIMEOUT_S)) as session:
            async with session.request(method, url, headers=api_headers(), **kwargs) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ClientPayloadError):
                    data = None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error(f"API {path} failed: {type(e).__name__}: {e}")
        return {'error': 'network_error'}

    if not isinstance(data, dict):
        log.warning(f"API {path} returned {status} with a non-JSON body")
        return {'error': 'bad_response', 'status': status}

    if status >= 400:
        log.warning(f"API {path} returned {status}: {data}")
        data = dict(data)
        if not data.get('error'):
            data['error'] = f'http_{status}'
        data['status'] = status
    return data


async def relay_post(path: str, body: dict) -> dict:
    """POST to relay API and return JSON response (see _relay_request)."""
    return await _relay_request('POST', path, json=body)


async def relay_get(path: str, params: dict = None) -> dict:
    """GET from relay API and return JSON response (see _relay_request)."""
    return await _relay_request('GET', path, params=params)


def relay_error_text(data: dict) -> str:
    """Short, user-facing explanation of a relay error (plain text)."""
    error = data.get('error')
    status = data.get('status')
    if error == 'rate_limited' or status == 429:
        return "Too many attempts. Please wait a few minutes and try again."
    if error == 'network_error':
        return "Can't reach the LEELOO server right now. Please try again in a minute."
    if error == 'not_in_crew':
        return "You're not a member of this crew anymore."
    if error == 'crew_not_found':
        return "That crew was not found."
    if error == 'unauthorized' or status == 401:
        return "The LEELOO bot is not set up correctly. Please try again later."
    return "The LEELOO server had a problem. Please try again in a minute."


# --- Telegram reply helpers ---

def md_escape(value) -> str:
    """Escape user-supplied text for Telegram's legacy 'Markdown' parse mode."""
    return re.sub(r'([_*`\[])', r'\\\1', str(value if value is not None else ''))


async def _send(send_func, text: str, markdown: bool = False, **kwargs) -> bool:
    """Send or edit a Telegram message without ever raising.

    A reply that Telegram rejects must not crash the handler: by the time we
    confirm, the action (e.g. sending to the crew) has already happened.
    If the Markdown version is rejected, the same text is retried as plain text.
    """
    try:
        if markdown:
            try:
                await send_func(text, parse_mode='Markdown', **kwargs)
                return True
            except BadRequest as e:
                if 'not modified' in str(e).lower():
                    return True  # e.g. "Refresh" with nothing changed
                log.warning(f"Markdown reply rejected ({e}); retrying as plain text")
        await send_func(text, **kwargs)
        return True
    except BadRequest as e:
        if 'not modified' in str(e).lower():
            return True
        log.error(f"Telegram rejected reply: {e}")
    except TelegramError as e:
        log.error(f"Could not send Telegram reply: {e}")
    return False


def _first_name(user) -> str:
    return (getattr(user, 'first_name', None) or 'Phone') if user else 'Phone'


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _devices(n: int) -> str:
    return f"{n} device" if n == 1 else f"{n} devices"


# --- Bot handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command, including deep links like /start LEELOO-XXXX"""
    user = update.effective_user
    if user is None or update.message is None:
        return  # edited message, channel post, etc.
    user_id = user.id

    # A fresh /start cancels any pending "enter your crew code" prompt
    context.user_data['awaiting_crew_code'] = False

    # Check for deep link payload (from QR code scan)
    # When user scans t.me/Leeloo2259_bot?start=LEELOO-XXXX,
    # Telegram sends /start LEELOO-XXXX
    args = context.args
    if args and len(args) > 0:
        deep_link = args[0].upper()
        # Handle crew code deep link (LEELOO-XXXX or LEELOOXXXX)
        if deep_link.startswith('LEELOO'):
            # Normalize: LEELOOXXXX -> LEELOO-XXXX
            if '-' not in deep_link and len(deep_link) == 10:
                deep_link = deep_link[:6] + '-' + deep_link[6:]
            if deep_link.startswith('LEELOO-') and len(deep_link) == 11:
                log.info(f"Deep link crew join: user {user_id} -> {deep_link}")
                await join_crew(update, user_id, deep_link)
                return

    welcome_text = (
        f"Welcome to LEELOO, {md_escape(_first_name(user))}!\n\n"
        "LEELOO is a music sharing device that lets you and your "
        "friends push songs to each other.\n\n"
        "**Got a crew code? Tap below to join.**\n"
        "_(If you just set up a device, scan the QR code on its screen instead.)_"
    )

    keyboard = [
        [InlineKeyboardButton("Have a crew code? Join here →", callback_data='join_crew')],
        [InlineKeyboardButton("Start a new crew", callback_data='create_crew')],
    ]

    if user_id in user_state and user_state[user_id].get('crew_code'):
        crew_code = user_state[user_id]['crew_code']
        keyboard.append([InlineKeyboardButton(f"My Crew: {crew_code}", callback_data='my_crew')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await _send(update.message.reply_text, welcome_text, markdown=True, reply_markup=reply_markup)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    user_id = query.from_user.id
    try:
        await query.answer()
    except TelegramError as e:
        # e.g. a button tapped long ago ("query is too old") - still handle it
        log.warning(f"Could not answer callback query: {e}")

    if query.data == 'create_crew':
        await create_crew(query, user_id)
    elif query.data == 'join_crew':
        await prompt_join_crew(query, user_id, context)
    elif query.data == 'my_crew':
        await show_my_crew(query, user_id)
    elif query.data == 'pair_device':
        await show_pairing_code(query, user_id)
    elif query.data == 'send_message':
        await prompt_send_message(query, user_id, context)
    elif query.data == 'crew_status':
        await show_crew_status(query, user_id)


async def create_crew(query, user_id: int):
    """Create a new crew via relay API"""
    data = await relay_post('/api/telegram/crew/create', {
        'telegram_user_id': user_id,
        'display_name': _first_name(query.from_user)
    })

    crew_code = data.get('crew_code')
    if 'error' in data or not isinstance(crew_code, str) or not crew_code:
        if 'error' not in data:
            data = {'error': 'bad_response'}
        await _send(
            query.edit_message_text,
            f"Could not create a crew. {relay_error_text(data)}\n\nTry again with /start"
        )
        return

    set_user_crew(user_id, crew_code, _first_name(query.from_user))

    text = (
        "**Your crew has been created!**\n\n"
        f"Crew Code: `{crew_code}`\n\n"
        "**Next steps:**\n"
        "1. Power on your LEELOO device\n"
        "2. When it shows \"Join Crew\", enter this code\n"
        "3. Share this code with friends so they can join too!\n\n"
        "Once paired, just type any message here and it'll show on all devices.\n"
        "You can also share music — type a song or artist name and LEELOO will push it to the crew."
    )

    keyboard = [
        [InlineKeyboardButton("Pair My Device", callback_data='pair_device')],
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(query.edit_message_text, text, markdown=True, reply_markup=reply_markup)
    log.info(f"User {user_id} created crew {crew_code}")


async def prompt_join_crew(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for crew code to join"""
    context.user_data['awaiting_crew_code'] = True

    text = (
        "**Join an Existing Crew**\n\n"
        "Please enter the crew code your friend shared with you.\n\n"
        "It looks like: `LEELOO-XXXX`"
    )
    await _send(query.edit_message_text, text, markdown=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages from user.
    If the user is in a crew, any plain text is sent directly to the crew —
    no button tap or prompt required.
    """
    # update.message is None for edited messages / channel posts: ignore those
    # (an edit must not be sent to the crew a second time).
    if update.effective_user is None or update.message is None or not update.message.text:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return

    if context.user_data.get('awaiting_crew_code'):
        context.user_data['awaiting_crew_code'] = False
        await join_crew(update, user_id, text.upper())
        return

    # Drop the awaiting_message gate — send directly if in a crew
    state = user_state.get(user_id, {})
    if state.get('crew_code'):
        context.user_data['awaiting_message'] = False
        await send_crew_message(update, user_id, text)
        return

    await _send(
        update.message.reply_text,
        "Join a crew first so your messages have somewhere to go!\n\nUse /start to get set up."
    )


async def join_crew(update: Update, user_id: int, crew_code: str):
    """Join user to an existing crew via relay API"""
    crew_code = crew_code.strip()
    if not CREW_CODE_RE.match(crew_code):
        await _send(
            update.message.reply_text,
            "Invalid crew code format. It should look like: LEELOO-XXXX\n\nTry again or use /start"
        )
        return

    display_name = _first_name(update.effective_user)
    data = await relay_post('/api/telegram/crew/join', {
        'telegram_user_id': user_id,
        'crew_code': crew_code,
        'display_name': display_name
    })

    if 'error' in data:
        if data.get('error') == 'rate_limited' or data.get('status') == 429:
            await _send(
                update.message.reply_text,
                "Too many attempts. Please wait a few minutes, then try again with /start"
            )
        elif data.get('error') == 'crew_not_found':
            await _send(
                update.message.reply_text,
                f"Crew `{crew_code}` not found.\n\n"
                "Make sure a LEELOO device has connected with this code first, "
                "or create a new crew with /start",
                markdown=True
            )
        else:
            await _send(
                update.message.reply_text,
                f"Could not join the crew. {relay_error_text(data)}\n\nTry /start"
            )
        return

    set_user_crew(user_id, crew_code, display_name)

    devices_online = _count(data.get('devices_online', 0))
    text = (
        "**You've joined the crew!**\n\n"
        f"Crew Code: `{crew_code}`\n"
        f"Devices online: {devices_online}\n\n"
        "Just type anything here and it'll show up on all LEELOO devices in your crew.\n\n"
        "You can also share music — just type a song or artist name (e.g. _Tame Impala - Feels Like We Only Go Backwards_) "
        "and LEELOO will push it to the crew.\n\n"
        "Use the buttons below to check crew status or pair a device."
    )

    keyboard = [
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
        [InlineKeyboardButton("Pair My Device", callback_data='pair_device')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(update.message.reply_text, text, markdown=True, reply_markup=reply_markup)
    log.info(f"User {user_id} joined crew {crew_code} ({devices_online} devices online)")


async def show_my_crew(query, user_id: int):
    """Show user's current crew info"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code', 'Not joined')

    text = (
        "**Your Crew**\n\n"
        f"Crew Code: `{crew_code}`\n\n"
        "Share this code with friends to add them to your crew!\n\n"
        "**Options:**"
    )

    keyboard = [
        [InlineKeyboardButton("Pair New Device", callback_data='pair_device')],
        [InlineKeyboardButton("Send Message", callback_data='send_message')],
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(query.edit_message_text, text, markdown=True, reply_markup=reply_markup)


async def show_crew_status(query, user_id: int):
    """Show live crew status from relay server"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code', '')

    if not crew_code:
        await _send(query.edit_message_text, "You need to join a crew first! Use /start")
        return

    data = await relay_get('/api/telegram/crew/status', {'crew_code': crew_code})

    if 'error' in data:
        await _send(query.edit_message_text, f"Could not get crew status. {relay_error_text(data)}")
        return

    devices_online = _count(data.get('devices_online', 0))
    device_names = data.get('device_names') or []
    telegram_user_names = data.get('telegram_user_names') or []
    if not isinstance(device_names, list):
        device_names = []
    if not isinstance(telegram_user_names, list):
        telegram_user_names = []

    # Names are user-supplied: escape them so they cannot break the Markdown
    device_list = '\n'.join(f"  📟 {md_escape(name)}" for name in device_names) if device_names else "  No devices connected"
    user_list = '\n'.join(f"  👤 {md_escape(name)}" for name in telegram_user_names) if telegram_user_names else "  No users connected"

    text = (
        f"**Crew Status: `{crew_code}`**\n\n"
        f"Devices online: {devices_online}\n"
        f"{device_list}\n\n"
        f"Telegram users: {len(telegram_user_names)}\n"
        f"{user_list}"
    )

    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data='crew_status')],
        [InlineKeyboardButton("Send Message", callback_data='send_message')],
        [InlineKeyboardButton("Back", callback_data='my_crew')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(query.edit_message_text, text, markdown=True, reply_markup=reply_markup)


async def show_pairing_code(query, user_id: int):
    """Show pairing info for device"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code', '')

    if not crew_code:
        await _send(query.edit_message_text, "You need to create or join a crew first! Use /start")
        return

    text = (
        "**Pair Your LEELOO Device**\n\n"
        "1. Power on your LEELOO device\n"
        "2. Wait for the setup screen\n"
        "3. Enter this crew code:\n\n"
        f"`{crew_code}`\n\n"
        "Your device will connect to your crew automatically!\n\n"
        "**Troubleshooting:**\n"
        "- Make sure your LEELOO is connected to WiFi\n"
        "- The code is case-insensitive\n"
        "- Contact support if issues persist"
    )

    keyboard = [
        [InlineKeyboardButton("Back", callback_data='my_crew')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(query.edit_message_text, text, markdown=True, reply_markup=reply_markup)


async def prompt_send_message(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to type a message"""
    state = user_state.get(user_id, {})
    if not state.get('crew_code'):
        await _send(query.edit_message_text, "You need to join a crew first! Use /start")
        return

    context.user_data['awaiting_message'] = True

    text = (
        "**Send Message to Crew**\n\n"
        "Type your message below. It will appear on all "
        "LEELOO devices in your crew!\n\n"
        "Keep it short - the display is small."
    )
    await _send(query.edit_message_text, text, markdown=True)


async def send_crew_message(update: Update, user_id: int, message_text: str):
    """Send a message to all crew devices via relay API"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code')
    display_name = state.get('display_name') or _first_name(update.effective_user)

    if not crew_code:
        await _send(update.message.reply_text, "You need to join a crew first!")
        return

    data = await relay_post('/api/telegram/message', {
        'telegram_user_id': user_id,
        'crew_code': crew_code,
        'sender_name': display_name,
        'msg_type': 'text',
        'payload': {
            'text': message_text,
            'timestamp': time.time()
        }
    })

    if 'error' in data:
        if data.get('error') == 'not_in_crew':
            # The relay is the source of truth for membership
            forget_user_crew(user_id)
            reply = (
                f"Message not sent: you're no longer a member of crew {crew_code}.\n\n"
                "Rejoin the crew with /start"
            )
        elif data.get('error') == 'network_error':
            reply = (
                "Can't reach the LEELOO server right now, so your message may not have been sent.\n\n"
                "Please try again in a minute."
            )
        else:
            reply = f"Message not sent. {relay_error_text(data)}"
        await _send(update.message.reply_text, reply)
        return

    devices_reached = _count(data.get('devices_reached', 0))
    devices_queued = _count(data.get('devices_queued', 0))

    if devices_reached and devices_queued:
        title = "Message Sent!"
        delivery = (
            f"Delivered now to {_devices(devices_reached)}. "
            f"{_devices(devices_queued).capitalize()} offline: "
            "it will be delivered when their LEELOO is back online."
        )
    elif devices_reached:
        title = "Message Sent!"
        delivery = f"Delivered now to {_devices(devices_reached)}."
    elif devices_queued:
        title = "Message Queued"
        delivery = (
            f"No LEELOO is online right now ({_devices(devices_queued)} offline). "
            "Your message will be delivered when their LEELOO is back online."
        )
    else:
        title = "Message Not Delivered"
        delivery = "No device received it: there is no LEELOO in this crew to deliver to yet."

    # The message is already with the relay at this point. The confirmation
    # escapes all user text, and _send falls back to plain text, so a rejected
    # reply can never look like a failed send.
    text = (
        f"**{title}**\n\n"
        f"From: {md_escape(display_name)}\n"
        f"To: Crew {md_escape(crew_code)}\n"
        f"{delivery}\n\n"
        f"\"{md_escape(message_text)}\""
    )

    keyboard = [
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
        [InlineKeyboardButton("Back to Crew", callback_data='my_crew')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send(update.message.reply_text, text, markdown=True, reply_markup=reply_markup)
    log.info(
        f"User {user_id} sent message to crew {crew_code} "
        f"({devices_reached} devices reached, {devices_queued} queued)"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    if update.message is None:
        return
    text = (
        "**LEELOO Help**\n\n"
        "LEELOO is a music sharing device for you and your friends.\n\n"
        "**Commands:**\n"
        "/start - Main menu\n"
        "/help - This help message\n\n"
        "**Sending messages:**\n"
        "Once you're in a crew, just *type and send* — no commands needed. "
        "Your message pops up on every LEELOO device in the crew.\n\n"
        "**Sharing music:**\n"
        "Type a song or artist name (e.g. _The Strokes - Last Nite_) and "
        "LEELOO will search Spotify and push it to the crew.\n\n"
        "**Crew status:**\n"
        "Use /start → Crew Status to see who's online.\n\n"
        "**What is a Crew?**\n"
        "A crew is your group of friends with LEELOO devices. "
        "When someone pushes a song, it shows on everyone's device!"
    )
    await _send(update.message.reply_text, text, markdown=True)


def main():
    """Start the bot"""
    if not BOT_TOKEN:
        print("ERROR: Set LEELOO_BOT_TOKEN environment variable")
        print("Get a token from @BotFather on Telegram")
        return

    log.info(f"Starting LEELOO Telegram Bot...")
    log.info(f"Relay API: {RELAY_API_URL}")
    log.info(f"State file: {STATE_FILE}")
    load_state()

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == '__main__':
    main()
