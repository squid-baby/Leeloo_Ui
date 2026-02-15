#!/usr/bin/env python3
"""
LEELOO Telegram Bot
Handles device pairing and messaging from phone to LEELOO devices.
Communicates with the relay server (server.js) via HTTP API.
"""

import os
import time
import logging

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

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
log = logging.getLogger('leeloo-bot')

# Config from environment
BOT_TOKEN = os.environ.get('LEELOO_BOT_TOKEN', '')
RELAY_API_URL = os.environ.get('RELAY_API_URL', 'http://localhost:3000')
TELEGRAM_API_SECRET = os.environ.get('TELEGRAM_API_SECRET', '')

# Store user state (in production, use Redis or similar)
user_state = {}  # telegram_user_id -> {crew_code, display_name}


def api_headers():
    """Build headers for relay API requests."""
    headers = {'Content-Type': 'application/json'}
    if TELEGRAM_API_SECRET:
        headers['X-Api-Secret'] = TELEGRAM_API_SECRET
    return headers


async def relay_post(path: str, body: dict) -> dict:
    """POST to relay API and return JSON response."""
    url = f'{RELAY_API_URL}{path}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=api_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    log.warning(f"API {path} returned {resp.status}: {data}")
                return data
    except Exception as e:
        log.error(f"API {path} failed: {e}")
        return {'error': str(e)}


async def relay_get(path: str, params: dict = None) -> dict:
    """GET from relay API and return JSON response."""
    url = f'{RELAY_API_URL}{path}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=api_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return await resp.json()
    except Exception as e:
        log.error(f"API {path} failed: {e}")
        return {'error': str(e)}


# --- Bot handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command, including deep links like /start LEELOO-XXXX"""
    user = update.effective_user
    user_id = user.id

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
        f"Welcome to LEELOO, {user.first_name}!\n\n"
        "LEELOO is a music sharing device that lets you and your "
        "friends push songs to each other.\n\n"
        "**What would you like to do?**"
    )

    keyboard = [
        [InlineKeyboardButton("Create New Crew", callback_data='create_crew')],
        [InlineKeyboardButton("Join Existing Crew", callback_data='join_crew')],
    ]

    if user_id in user_state and user_state[user_id].get('crew_code'):
        crew_code = user_state[user_id]['crew_code']
        keyboard.append([InlineKeyboardButton(f"My Crew: {crew_code}", callback_data='my_crew')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

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
        'telegram_user_id': user_id
    })

    if 'error' in data:
        await query.edit_message_text(
            f"Failed to create crew: {data.get('error')}\n\nTry again with /start"
        )
        return

    crew_code = data['crew_code']

    user_state[user_id] = {
        'crew_code': crew_code,
        'display_name': query.from_user.first_name
    }

    text = (
        "**Your crew has been created!**\n\n"
        f"Crew Code: `{crew_code}`\n\n"
        "**Next steps:**\n"
        "1. Power on your LEELOO device\n"
        "2. When it shows \"Join Crew\", enter this code\n"
        "3. Share this code with friends so they can join too!\n\n"
        "Each crew member will need:\n"
        "- Their own LEELOO device\n"
        "- This crew code to connect"
    )

    keyboard = [
        [InlineKeyboardButton("Pair My Device", callback_data='pair_device')],
        [InlineKeyboardButton("Send Message to Crew", callback_data='send_message')],
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    log.info(f"User {user_id} created crew {crew_code}")


async def prompt_join_crew(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for crew code to join"""
    context.user_data['awaiting_crew_code'] = True

    text = (
        "**Join an Existing Crew**\n\n"
        "Please enter the crew code your friend shared with you.\n\n"
        "It looks like: `LEELOO-XXXX`"
    )
    await query.edit_message_text(text, parse_mode='Markdown')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages from user"""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if context.user_data.get('awaiting_crew_code'):
        context.user_data['awaiting_crew_code'] = False
        await join_crew(update, user_id, text.upper())
        return

    if context.user_data.get('awaiting_message'):
        context.user_data['awaiting_message'] = False
        await send_crew_message(update, user_id, text)
        return

    await update.message.reply_text(
        "Use /start to see options, or /help for more info."
    )


async def join_crew(update: Update, user_id: int, crew_code: str):
    """Join user to an existing crew via relay API"""
    if not crew_code.startswith('LEELOO-') or len(crew_code) != 11:
        await update.message.reply_text(
            "Invalid crew code format. It should look like: LEELOO-XXXX\n\nTry again or use /start"
        )
        return

    data = await relay_post('/api/telegram/crew/join', {
        'telegram_user_id': user_id,
        'crew_code': crew_code
    })

    if 'error' in data:
        if data.get('error') == 'crew_not_found':
            await update.message.reply_text(
                f"Crew `{crew_code}` not found.\n\n"
                "Make sure a LEELOO device has connected with this code first, "
                "or create a new crew with /start",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"Failed to join crew: {data.get('message', data.get('error'))}\n\nTry /start"
            )
        return

    user_state[user_id] = {
        'crew_code': crew_code,
        'display_name': update.effective_user.first_name
    }

    devices_online = data.get('devices_online', 0)
    text = (
        "**You've joined the crew!**\n\n"
        f"Crew Code: `{crew_code}`\n"
        f"Devices online: {devices_online}\n\n"
        "Messages you send here will appear on all crew devices!"
    )

    keyboard = [
        [InlineKeyboardButton("Send Message to Crew", callback_data='send_message')],
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
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

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def show_crew_status(query, user_id: int):
    """Show live crew status from relay server"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code', '')

    if not crew_code:
        await query.edit_message_text("You need to join a crew first! Use /start")
        return

    data = await relay_get('/api/telegram/crew/status', {'crew_code': crew_code})

    if 'error' in data:
        await query.edit_message_text(f"Could not get crew status: {data.get('error')}")
        return

    devices_online = data.get('devices_online', 0)
    device_names = data.get('device_names', [])
    telegram_users = data.get('telegram_users', 0)

    if device_names:
        device_list = '\n'.join(f"  - {name}" for name in device_names)
    else:
        device_list = "  No devices connected"

    text = (
        f"**Crew Status: `{crew_code}`**\n\n"
        f"Devices online: {devices_online}\n"
        f"{device_list}\n\n"
        f"Telegram users: {telegram_users}"
    )

    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data='crew_status')],
        [InlineKeyboardButton("Send Message", callback_data='send_message')],
        [InlineKeyboardButton("Back", callback_data='my_crew')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def show_pairing_code(query, user_id: int):
    """Show pairing info for device"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code', '')

    if not crew_code:
        await query.edit_message_text("You need to create or join a crew first! Use /start")
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

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def prompt_send_message(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to type a message"""
    state = user_state.get(user_id, {})
    if not state.get('crew_code'):
        await query.edit_message_text("You need to join a crew first! Use /start")
        return

    context.user_data['awaiting_message'] = True

    text = (
        "**Send Message to Crew**\n\n"
        "Type your message below. It will appear on all "
        "LEELOO devices in your crew!\n\n"
        "Keep it short - the display is small."
    )
    await query.edit_message_text(text, parse_mode='Markdown')


async def send_crew_message(update: Update, user_id: int, message_text: str):
    """Send a message to all crew devices via relay API"""
    state = user_state.get(user_id, {})
    crew_code = state.get('crew_code')
    display_name = state.get('display_name', 'Phone')

    if not crew_code:
        await update.message.reply_text("You need to join a crew first!")
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
        await update.message.reply_text(
            f"Failed to send message: {data.get('message', data.get('error'))}\n\n"
            "You may need to rejoin the crew with /start"
        )
        return

    devices_reached = data.get('devices_reached', 0)

    text = (
        "**Message Sent!**\n\n"
        f"From: {display_name}\n"
        f"To: Crew {crew_code}\n"
        f"Devices reached: {devices_reached}\n\n"
        f"\"{message_text}\""
    )

    keyboard = [
        [InlineKeyboardButton("Send Another", callback_data='send_message')],
        [InlineKeyboardButton("Crew Status", callback_data='crew_status')],
        [InlineKeyboardButton("Back to Crew", callback_data='my_crew')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    log.info(f"User {user_id} sent message to crew {crew_code} ({devices_reached} devices)")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    text = (
        "**LEELOO Help**\n\n"
        "LEELOO is a music sharing device for you and your friends.\n\n"
        "**Commands:**\n"
        "/start - Main menu\n"
        "/help - This help message\n\n"
        "**What is a Crew?**\n"
        "A crew is a group of friends with LEELOO devices. "
        "When someone in your crew pushes a song, it shows up "
        "on everyone's device!\n\n"
        "**How to Set Up:**\n"
        "1. Create a crew (or get a code from a friend)\n"
        "2. Connect your LEELOO device to your crew\n"
        "3. Start sharing music!\n\n"
        "**Send messages from your phone:**\n"
        "Once in a crew, tap \"Send Message\" and your message "
        "will appear on all LEELOO devices in your crew."
    )
    await update.message.reply_text(text, parse_mode='Markdown')


def main():
    """Start the bot"""
    if not BOT_TOKEN:
        print("ERROR: Set LEELOO_BOT_TOKEN environment variable")
        print("Get a token from @BotFather on Telegram")
        return

    log.info(f"Starting LEELOO Telegram Bot...")
    log.info(f"Relay API: {RELAY_API_URL}")

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
