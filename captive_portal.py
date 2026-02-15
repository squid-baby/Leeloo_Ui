#!/usr/bin/env python3
"""
Captive Portal — 5-step offline setup flow for LEELOO

Runs on the Pi's own WiFi AP with NO internet access.
All data is saved locally to JSON config files.
Real relay server work happens AFTER the portal closes
and the device connects to home WiFi.

Steps:
  1. WiFi    — pick home network, enter password (saved, not connected)
  2. You     — first name + ZIP code
  3. Crew    — start a new crew (show device code) or join a friend's crew
  4. Telegram — teaser/opt-in (never tells user to leave the portal)
  5. Done    — triggers WiFi switch, user can close page

Terminal aesthetic using LEELOO device color palette.
"""

import os
import json
import sys
import subprocess
from flask import Flask, request, jsonify, redirect

from wifi_manager import (
    start_ap_mode, stop_ap_mode, scan_wifi_networks,
    get_device_id
)
from leeloo_device_id import get_device_crew_code

app = Flask(__name__)

# Config paths
LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, "crew_config.json")

# State
setup_state = {
    'step': 'wifi',
    'ssid': None,
    'dev_mode': False,
}

# Cached network list (scanned before AP mode starts)
cached_networks = []

# LCD update callback (set by boot sequence)
lcd_callback = None


def set_lcd_callback(callback):
    global lcd_callback
    lcd_callback = callback


def update_lcd(screen, **kwargs):
    if lcd_callback:
        lcd_callback(screen, **kwargs)


# ============================================
# DEVICE CREW CODE (cached)
# ============================================
_device_crew_code = None

def device_crew_code():
    global _device_crew_code
    if _device_crew_code is None:
        _device_crew_code = get_device_crew_code()
    return _device_crew_code


# ============================================
# CONFIG HELPERS
# ============================================

def load_device_config():
    try:
        with open(DEVICE_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_device_config(data):
    existing = load_device_config()
    existing.update(data)
    os.makedirs(os.path.dirname(DEVICE_CONFIG_PATH), exist_ok=True)
    with open(DEVICE_CONFIG_PATH, 'w') as f:
        json.dump(existing, f, indent=2)


def save_crew_config(data):
    os.makedirs(os.path.dirname(CREW_CONFIG_PATH), exist_ok=True)
    with open(CREW_CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=2)


# ============================================
# TERMINAL CSS — CRT phosphor aesthetic
# ============================================

TERMINAL_CSS = """
<style>
    @keyframes blink {
        0%, 49% { opacity: 1; }
        50%, 100% { opacity: 0; }
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
        font-family: 'Courier New', Courier, monospace;
        background: #1A1D2E;
        color: #FFFFFF;
        min-height: 100vh;
        padding: 20px;
        /* Scanline overlay */
        background-image: repeating-linear-gradient(
            0deg,
            transparent,
            transparent 2px,
            rgba(26, 29, 46, 0.3) 2px,
            rgba(26, 29, 46, 0.3) 3px
        );
    }

    .container {
        max-width: 400px;
        margin: 0 auto;
    }

    /* ---- Terminal box with frame-breaking label ---- */
    .term-box {
        border: 2px solid #9C93DD;
        padding: 24px 18px 18px 18px;
        margin-bottom: 24px;
        position: relative;
    }
    .term-box.green  { border-color: #7beec0; }
    .term-box.purple { border-color: #9C93DD; }
    .term-box.tan    { border-color: #C2995E; }
    .term-box.lav    { border-color: #d978f9; }
    .term-box.rose   { border-color: #D6697F; }

    .term-label {
        position: absolute;
        top: -10px;
        left: 12px;
        background: #1A1D2E;
        padding: 0 6px;
        font-size: 12px;
        letter-spacing: 1px;
    }
    .term-box.green  .term-label { color: #7beec0; }
    .term-box.purple .term-label { color: #9C93DD; }
    .term-box.tan    .term-label { color: #C2995E; }
    .term-box.lav    .term-label { color: #d978f9; }
    .term-box.rose   .term-label { color: #D6697F; }

    /* ---- Headings ---- */
    h1 {
        font-size: 16px;
        color: #9C93DD;
        margin-bottom: 16px;
        font-weight: normal;
    }
    h1 .cursor {
        animation: blink 1s step-end infinite;
        color: #9C93DD;
    }

    /* ---- Prompt lines ---- */
    .prompt {
        color: #d978f9;
        font-size: 14px;
        margin-bottom: 12px;
    }
    .prompt::before {
        content: '> ';
        opacity: 0.6;
    }

    /* ---- Form elements ---- */
    label {
        display: block;
        color: #C2995E;
        font-size: 12px;
        margin-bottom: 4px;
        letter-spacing: 0.5px;
    }
    select, input[type="text"], input[type="password"] {
        width: 100%;
        padding: 12px;
        margin-bottom: 14px;
        background: #2A2D3E;
        border: 1px solid #4A4A6A;
        color: #FFFFFF;
        font-family: 'Courier New', Courier, monospace;
        font-size: 16px;
        border-radius: 0;
        -webkit-appearance: none;
    }
    select:focus, input:focus {
        outline: none;
        border-color: #7beec0;
    }
    select {
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%237beec0' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
        background-repeat: no-repeat;
        background-position: right 12px center;
        padding-right: 32px;
    }

    /* ---- Buttons ---- */
    .btn {
        width: 100%;
        padding: 14px;
        background: #7beec0;
        border: none;
        color: #1A1D2E;
        font-family: 'Courier New', Courier, monospace;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        margin-top: 8px;
        letter-spacing: 1px;
    }
    .btn:hover { background: #9af5d5; }
    .btn:disabled {
        background: #4A4A6A;
        color: #6A6A8A;
        cursor: not-allowed;
    }
    .btn:active { transform: scale(0.98); }

    .btn-secondary {
        background: transparent;
        border: 1px solid #4A4A6A;
        color: #A7AFD4;
        font-weight: normal;
    }
    .btn-secondary:hover {
        border-color: #9C93DD;
        color: #FFFFFF;
        background: transparent;
    }

    /* ---- Crew code display ---- */
    .crew-code {
        background: #2A2D3E;
        border: 2px solid #7beec0;
        padding: 16px;
        text-align: center;
        font-size: 22px;
        font-weight: bold;
        color: #7beec0;
        letter-spacing: 3px;
        margin: 16px 0;
        user-select: all;
        cursor: pointer;
        text-shadow: 0 0 10px rgba(123, 238, 192, 0.4);
    }

    /* ---- Step indicator ---- */
    .step-indicator {
        text-align: center;
        color: #9C93DD;
        font-size: 12px;
        margin-top: 16px;
        opacity: 0.7;
    }

    /* ---- Radio options ---- */
    .radio-opt {
        display: block;
        padding: 14px;
        margin-bottom: 10px;
        border: 1px solid #4A4A6A;
        cursor: pointer;
        transition: border-color 0.15s;
    }
    .radio-opt:hover { border-color: #7beec0; }
    .radio-opt input { margin-right: 10px; vertical-align: middle; }
    .radio-opt input:checked + .radio-text { color: #7beec0; }
    .radio-text {
        font-size: 14px;
        color: #FFFFFF;
    }
    .radio-desc {
        display: block;
        margin-top: 4px;
        margin-left: 26px;
        font-size: 11px;
        color: #6A6A8A;
    }

    /* ---- Misc ---- */
    .hint {
        font-size: 11px;
        color: #4A4A6A;
        margin-top: -10px;
        margin-bottom: 14px;
    }
    .error-msg {
        background: rgba(214, 105, 127, 0.15);
        border: 1px solid #D6697F;
        color: #D6697F;
        padding: 10px;
        margin-bottom: 14px;
        font-size: 12px;
    }
    .success-msg {
        background: rgba(123, 238, 192, 0.1);
        border: 1px solid #7beec0;
        color: #7beec0;
        padding: 10px;
        margin-bottom: 14px;
        font-size: 12px;
    }
    .note {
        background: #2A2D3E;
        padding: 12px;
        font-size: 12px;
        color: #C2995E;
        margin: 14px 0;
        border-left: 3px solid #C2995E;
    }
    .glow {
        text-shadow: 0 0 8px currentColor;
    }
    .dim { color: #4A4A6A; }

    /* Toggle switch */
    .toggle-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 0;
    }
    .toggle-label { color: #FFFFFF; font-size: 14px; }
    .toggle {
        position: relative;
        width: 48px;
        height: 26px;
    }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .toggle .slider {
        position: absolute;
        inset: 0;
        background: #4A4A6A;
        cursor: pointer;
        transition: 0.2s;
        border-radius: 26px;
    }
    .toggle .slider::before {
        content: '';
        position: absolute;
        height: 20px;
        width: 20px;
        left: 3px;
        bottom: 3px;
        background: #1A1D2E;
        transition: 0.2s;
        border-radius: 50%;
    }
    .toggle input:checked + .slider { background: #7beec0; }
    .toggle input:checked + .slider::before { transform: translateX(22px); }

    /* Done screen */
    .done-check {
        font-size: 48px;
        color: #7beec0;
        text-align: center;
        margin: 16px 0;
        text-shadow: 0 0 20px rgba(123, 238, 192, 0.5);
    }
</style>
"""

SCRIPT_COMMON = """
<script>
async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...options.headers }
    });
    return response.json();
}
</script>
"""


def render_page(title, content):
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    <title>{title}</title>
    {TERMINAL_CSS}
    {SCRIPT_COMMON}
</head>
<body>
    <div class="container">
        {content}
    </div>
</body>
</html>"""


# ============================================
# CAPTIVE PORTAL DETECTION ROUTES
# ============================================

@app.route('/')
def index():
    if setup_state.get('dev_mode'):
        return redirect('/setup/wifi')
    return redirect('/setup/wifi')


@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
def apple_captive_portal():
    return redirect('/setup/wifi')


@app.route('/generate_204')
@app.route('/gen_204')
def android_captive_portal():
    return redirect('/setup/wifi')


@app.route('/connecttest.txt')
@app.route('/redirect')
def windows_captive_portal():
    return redirect('/setup/wifi')


@app.route('/success.txt')
@app.route('/canonical.html')
def firefox_captive_portal():
    return redirect('/setup/wifi')


# ============================================
# STEP 1: WIFI
# ============================================

@app.route('/setup/wifi')
def setup_wifi():
    error_html = ''
    if setup_state.get('error'):
        error_html = f'<div class="error-msg">{setup_state["error"]}</div>'
        setup_state['error'] = None

    content = f"""
    <div class="term-box tan">
        <div class="term-label">wifi</div>

        <h1>connect to your home wifi<span class="cursor">\u258a</span></h1>

        {error_html}

        <form id="wifiForm">
            <label>NETWORK</label>
            <select name="ssid" id="ssid" required>
                <option value="">scanning...</option>
            </select>

            <label>PASSWORD</label>
            <input type="password" name="password" id="password"
                   placeholder="enter wifi password" required>

            <button type="submit" class="btn" id="wifiBtn">CONNECT</button>
        </form>

        <div class="step-indicator">[step 1 of 5]</div>
    </div>

    <script>
        async function loadNetworks() {{
            try {{
                const data = await fetchJSON('/api/networks');
                const select = document.getElementById('ssid');
                if (data.networks && data.networks.length > 0) {{
                    select.innerHTML = data.networks.map(n =>
                        '<option value="' + n + '">' + n + '</option>'
                    ).join('');
                }} else {{
                    select.innerHTML = '<option value="">no networks found</option>';
                }}
            }} catch (e) {{
                console.error('scan failed:', e);
            }}
        }}
        loadNetworks();

        document.getElementById('wifiForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.getElementById('wifiBtn');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            const data = {{
                ssid: document.getElementById('ssid').value,
                password: document.getElementById('password').value
            }};

            try {{
                const result = await fetchJSON('/api/wifi', {{
                    method: 'POST',
                    body: JSON.stringify(data)
                }});
                if (result.success) {{
                    window.location.href = '/setup/you';
                }} else {{
                    btn.disabled = false;
                    btn.textContent = 'CONNECT';
                    alert(result.error || 'failed to save');
                }}
            }} catch (err) {{
                btn.disabled = false;
                btn.textContent = 'CONNECT';
                alert('error: ' + err.message);
            }}
        }});
    </script>
    """
    return render_page("LEELOO - WiFi", content)


# ============================================
# STEP 2: YOU (name + zip)
# ============================================

@app.route('/setup/you')
def setup_you():
    content = """
    <div class="term-box purple">
        <div class="term-label">you</div>

        <h1>tell me about yourself<span class="cursor">\u258a</span></h1>

        <form id="youForm">
            <label>FIRST NAME</label>
            <input type="text" name="user_name" id="userName"
                   placeholder="your first name" required maxlength="20"
                   autocomplete="given-name">

            <label>ZIP CODE</label>
            <input type="text" name="zip_code" id="zipCode"
                   placeholder="27601" pattern="[0-9]{5}" maxlength="5" required
                   inputmode="numeric" autocomplete="postal-code">
            <div class="hint">for weather and time zone</div>

            <button type="submit" class="btn" id="youBtn">CONTINUE</button>
        </form>

        <div class="step-indicator">[step 2 of 5]</div>
    </div>

    <script>
        document.getElementById('youForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('youBtn');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            const data = {
                user_name: document.getElementById('userName').value.trim(),
                zip_code: document.getElementById('zipCode').value.trim()
            };

            try {
                const result = await fetchJSON('/api/info', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });
                if (result.success) {
                    window.location.href = '/setup/crew';
                } else {
                    btn.disabled = false;
                    btn.textContent = 'CONTINUE';
                    alert(result.error || 'failed to save');
                }
            } catch (err) {
                btn.disabled = false;
                btn.textContent = 'CONTINUE';
                alert('error: ' + err.message);
            }
        });
    </script>
    """
    return render_page("LEELOO - You", content)


# ============================================
# STEP 3: CREW (create or join)
# ============================================

@app.route('/setup/crew')
def setup_crew():
    code = device_crew_code()

    content = f"""
    <div class="term-box green">
        <div class="term-label">crew</div>

        <h1>your crew<span class="cursor">\u258a</span></h1>

        <p class="prompt">are you the first one setting up?</p>

        <label class="radio-opt" id="optCreate">
            <input type="radio" name="crew_action" value="create" checked>
            <span class="radio-text">start a new crew</span>
            <span class="radio-desc">you're the first one with a LEELOO</span>
        </label>

        <label class="radio-opt" id="optJoin">
            <input type="radio" name="crew_action" value="join">
            <span class="radio-text">join a friend's crew</span>
            <span class="radio-desc">a friend shared a crew code with you</span>
        </label>

        <button type="button" class="btn" id="crewBtn">CONTINUE</button>

        <div class="step-indicator">[step 3 of 5]</div>
    </div>

    <script>
        document.getElementById('crewBtn').addEventListener('click', function() {{
            const action = document.querySelector('input[name="crew_action"]:checked').value;
            if (action === 'create') {{
                window.location.href = '/setup/crew/create';
            }} else {{
                window.location.href = '/setup/crew/join';
            }}
        }});
    </script>
    """
    return render_page("LEELOO - Crew", content)


@app.route('/setup/crew/create')
def setup_crew_create():
    code = device_crew_code()

    content = f"""
    <div class="term-box green">
        <div class="term-label">crew</div>

        <h1>your crew code<span class="cursor">\u258a</span></h1>

        <p class="prompt">this is your device's unique crew code</p>

        <div class="crew-code" id="crewCode" onclick="copyCode()">{code}</div>

        <button type="button" class="btn" onclick="copyCode()" id="copyBtn">
            COPY TO CLIPBOARD
        </button>

        <div class="note">
            copy this code now. after setup, text it to your
            friend so they can join your crew.
        </div>

        <button type="button" class="btn" id="createBtn" onclick="createCrew()">
            CONTINUE
        </button>

        <div class="step-indicator">[step 3 of 5]</div>
    </div>

    <script>
        function copyCode() {{
            const code = '{code}';
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(code).then(() => {{
                    document.getElementById('copyBtn').textContent = 'COPIED!';
                    setTimeout(() => {{
                        document.getElementById('copyBtn').textContent = 'COPY TO CLIPBOARD';
                    }}, 2000);
                }}).catch(() => {{
                    fallbackCopy();
                }});
            }} else {{
                fallbackCopy();
            }}
        }}

        function fallbackCopy() {{
            const el = document.getElementById('crewCode');
            const range = document.createRange();
            range.selectNodeContents(el);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }}

        async function createCrew() {{
            const btn = document.getElementById('createBtn');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            try {{
                const result = await fetchJSON('/api/crew/create', {{
                    method: 'POST',
                    body: JSON.stringify({{ crew_code: '{code}' }})
                }});
                if (result.success) {{
                    window.location.href = '/setup/telegram';
                }} else {{
                    btn.disabled = false;
                    btn.textContent = 'CONTINUE';
                    alert(result.error || 'failed');
                }}
            }} catch (err) {{
                btn.disabled = false;
                btn.textContent = 'CONTINUE';
                alert('error: ' + err.message);
            }}
        }}
    </script>
    """
    return render_page("LEELOO - Create Crew", content)


@app.route('/setup/crew/join')
def setup_crew_join():
    own_code = device_crew_code()

    content = f"""
    <div class="term-box green">
        <div class="term-label">crew</div>

        <h1>join a crew<span class="cursor">\u258a</span></h1>

        <p class="prompt">enter the crew code your friend shared</p>

        <form id="joinForm">
            <label>CREW CODE</label>
            <input type="text" name="invite_code" id="inviteCode"
                   placeholder="LEELOO-XXXX" required maxlength="11"
                   style="text-transform: uppercase; text-align: center;
                          font-size: 20px; letter-spacing: 2px;"
                   autocomplete="off" autocorrect="off" autocapitalize="characters">

            <button type="submit" class="btn" id="joinBtn">JOIN CREW</button>
        </form>

        <div class="note" style="margin-top: 18px;">
            your device code is <strong style="color: #7beec0;">{own_code}</strong><br>
            in case you want to start a different crew later
        </div>

        <button type="button" class="btn btn-secondary"
                onclick="window.location.href='/setup/crew'"
                style="margin-top: 8px;">
            BACK
        </button>

        <div class="step-indicator">[step 3 of 5]</div>
    </div>

    <script>
        const input = document.getElementById('inviteCode');

        // Auto-prepend LEELOO- prefix
        input.addEventListener('input', function() {{
            let val = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
            if (val.length > 0 && !val.startsWith('LEELOO-') && !val.startsWith('L')) {{
                val = 'LEELOO-' + val;
            }}
            this.value = val;
        }});

        document.getElementById('joinForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.getElementById('joinBtn');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            let code = document.getElementById('inviteCode').value.trim().toUpperCase();
            // Normalize: if they just typed the 4-char suffix, prepend LEELOO-
            if (code.length === 4 && !code.startsWith('L')) {{
                code = 'LEELOO-' + code;
            }}

            try {{
                const result = await fetchJSON('/api/crew/join', {{
                    method: 'POST',
                    body: JSON.stringify({{ invite_code: code }})
                }});
                if (result.success) {{
                    window.location.href = '/setup/telegram';
                }} else {{
                    btn.disabled = false;
                    btn.textContent = 'JOIN CREW';
                    alert(result.error || 'invalid code');
                }}
            }} catch (err) {{
                btn.disabled = false;
                btn.textContent = 'JOIN CREW';
                alert('error: ' + err.message);
            }}
        }});
    </script>
    """
    return render_page("LEELOO - Join Crew", content)


# ============================================
# STEP 4: TELEGRAM TEASER
# ============================================

@app.route('/setup/telegram')
def setup_telegram():
    content = """
    <div class="term-box lav">
        <div class="term-label">telegram</div>

        <h1>message your crew from your phone<span class="cursor">\u258a</span></h1>

        <p class="prompt">want to send messages to your crew's LEELOOs from your phone?</p>

        <p style="color: #A7AFD4; font-size: 13px; margin-bottom: 16px; line-height: 1.5;">
            after LEELOO boots up, it'll show you how to connect
            Telegram. nothing to do right now.
        </p>

        <div class="toggle-row">
            <span class="toggle-label">show me Telegram setup after boot</span>
            <label class="toggle">
                <input type="checkbox" id="telegramToggle" checked>
                <span class="slider"></span>
            </label>
        </div>

        <button type="button" class="btn" id="telegramBtn" onclick="saveTelegram()">
            CONTINUE
        </button>

        <div class="step-indicator">[step 4 of 5]</div>
    </div>

    <script>
        async function saveTelegram() {
            const btn = document.getElementById('telegramBtn');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            const opted = document.getElementById('telegramToggle').checked;

            try {
                const result = await fetchJSON('/api/telegram-optin', {
                    method: 'POST',
                    body: JSON.stringify({ telegram_opted_in: opted })
                });
                if (result.success) {
                    window.location.href = '/setup/done';
                } else {
                    btn.disabled = false;
                    btn.textContent = 'CONTINUE';
                    alert(result.error || 'failed');
                }
            } catch (err) {
                btn.disabled = false;
                btn.textContent = 'CONTINUE';
                alert('error: ' + err.message);
            }
        }
    </script>
    """
    return render_page("LEELOO - Telegram", content)


# ============================================
# STEP 5: DONE
# ============================================

@app.route('/setup/done')
def setup_done():
    content = """
    <div class="term-box green">
        <div class="term-label">done</div>

        <div class="done-check">OK</div>

        <h1 style="text-align: center; color: #7beec0;" class="glow">
            all set!
        </h1>

        <p style="text-align: center; color: #A7AFD4; font-size: 14px;
                  margin: 16px 0; line-height: 1.5;">
            your LEELOO is booting up.<br>
            you can close this page.
        </p>

        <div class="step-indicator">[step 5 of 5]</div>
    </div>

    <script>
        // Trigger WiFi switch
        fetch('/api/finish', { method: 'POST' })
            .then(r => r.json())
            .catch(() => {});
    </script>
    """
    return render_page("LEELOO - Ready", content)


# ============================================
# API ENDPOINTS
# ============================================

@app.route('/api/networks', methods=['GET'])
def api_networks():
    # Use cached networks (scanned before AP mode started)
    # because wlan0 is owned by hostapd and can't scan
    if cached_networks:
        return jsonify({'networks': cached_networks})
    # Fallback: try scanning anyway (might work in dev mode)
    networks = scan_wifi_networks()
    return jsonify({'networks': networks})


@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    """Save WiFi credentials (don't connect yet)"""
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')

    if not ssid or not password:
        return jsonify({'success': False, 'error': 'missing ssid or password'})

    save_device_config({
        'wifi_ssid': ssid,
        'wifi_password': password,
    })

    setup_state['ssid'] = ssid
    setup_state['step'] = 'you'
    print(f"[PORTAL] WiFi credentials saved: {ssid}")

    return jsonify({'success': True})


@app.route('/api/info', methods=['POST'])
def api_info():
    """Save user name + zip code"""
    data = request.get_json()
    user_name = data.get('user_name', '').strip()
    zip_code = data.get('zip_code', '').strip()

    if not user_name:
        return jsonify({'success': False, 'error': 'name is required'})

    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        return jsonify({'success': False, 'error': 'valid 5-digit zip code required'})

    # Save locally — geocoding happens post-WiFi in boot sequence
    save_device_config({
        'user_name': user_name,
        'zip_code': zip_code,
    })

    setup_state['step'] = 'crew'
    print(f"[PORTAL] User info saved: {user_name}, {zip_code}")

    return jsonify({'success': True})


@app.route('/api/crew/create', methods=['POST'])
def api_crew_create():
    """Create a new crew (save locally, register on relay post-WiFi)"""
    data = request.get_json()
    crew_code = data.get('crew_code', device_crew_code())

    save_crew_config({
        'invite_code': crew_code,
        'is_creator': True,
        'device_crew_code': crew_code,
    })

    setup_state['step'] = 'telegram'
    print(f"[PORTAL] Crew created (local): {crew_code}")

    return jsonify({'success': True, 'crew_code': crew_code})


@app.route('/api/crew/join', methods=['POST'])
def api_crew_join():
    """Join an existing crew (save locally, validate on relay post-WiFi)"""
    data = request.get_json()
    invite_code = data.get('invite_code', '').strip().upper()

    if not invite_code:
        return jsonify({'success': False, 'error': 'crew code is required'})

    # Basic format validation
    if not invite_code.startswith('LEELOO-') or len(invite_code) != 11:
        return jsonify({'success': False, 'error': 'code should look like LEELOO-XXXX'})

    own_code = device_crew_code()

    save_crew_config({
        'invite_code': invite_code,
        'is_creator': False,
        'device_crew_code': own_code,
    })

    setup_state['step'] = 'telegram'
    print(f"[PORTAL] Crew join saved (local): {invite_code}")

    return jsonify({'success': True, 'crew_code': invite_code})


@app.route('/api/telegram-optin', methods=['POST'])
def api_telegram_optin():
    """Save Telegram opt-in preference"""
    data = request.get_json()
    opted = data.get('telegram_opted_in', True)

    save_device_config({
        'telegram_opted_in': opted,
    })

    print(f"[PORTAL] Telegram opt-in: {opted}")
    return jsonify({'success': True})


@app.route('/api/finish', methods=['POST'])
def api_finish():
    """Mark setup complete and trigger WiFi connection"""
    save_device_config({'setup_complete': True})

    setup_state['step'] = 'done'
    update_lcd('success')

    # Trigger WiFi connection in background
    try:
        connect_script = os.path.join(LEELOO_HOME, 'connect_saved_wifi.py')
        if os.path.exists(connect_script):
            subprocess.Popen(
                ['sudo', 'python3', connect_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        print("[PORTAL] Setup complete, WiFi switch triggered")
        return jsonify({'success': True})
    except Exception as e:
        print(f"[PORTAL] WiFi switch error: {e}")
        return jsonify({'success': True, 'wifi_note': 'manual reboot may be needed'})


# ============================================
# MAIN
# ============================================

def run_captive_portal(lcd_update_callback=None, dev_mode=False):
    """
    Run the captive portal setup flow.
    Blocks until setup is complete.
    """
    global lcd_callback, cached_networks
    lcd_callback = lcd_update_callback
    setup_state['dev_mode'] = dev_mode

    if dev_mode:
        print("[PORTAL] DEV MODE — http://localhost:8080")
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
    else:
        # Pre-scan WiFi networks BEFORE starting AP mode
        # (once hostapd owns wlan0, we can't scan anymore)
        print("[PORTAL] Pre-scanning WiFi networks...")
        cached_networks = scan_wifi_networks()
        print(f"[PORTAL] Found {len(cached_networks)} networks: {cached_networks}")

        print("[PORTAL] PRODUCTION MODE — starting AP...")
        ssid = start_ap_mode()
        update_lcd('ap_mode', ssid=ssid)
        try:
            app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
        except PermissionError:
            print("[PORTAL] Port 80 requires root, falling back to 8080")
            app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)


if __name__ == "__main__":
    dev_mode = '--dev' in sys.argv or '-d' in sys.argv
    run_captive_portal(dev_mode=dev_mode)
