#!/usr/bin/env python3
"""
Captive Portal - Web-based setup flow for LEELOO
Serves a cyberpunk terminal-styled UI on phone
"""

import os
import json
import time
import threading
from flask import Flask, request, jsonify, redirect

# Import our modules
from wifi_manager import (
    start_ap_mode, stop_ap_mode, scan_wifi_networks,
    connect_to_wifi, is_connected, get_device_id
)

app = Flask(__name__)

# Config path
CONFIG_PATH = os.environ.get("LEELOO_CONFIG_PATH", "/home/pi/leeloo_config.json")

# State
setup_state = {
    'step': 'wifi',  # wifi, info, connecting, guide, done
    'ssid': None,
    'connected': False,
    'error': None,
    'dev_mode': False  # If True, skip AP mode and WiFi setup
}

# LCD update callback (set by main app)
lcd_callback = None


def set_lcd_callback(callback):
    """Set callback for LCD updates"""
    global lcd_callback
    lcd_callback = callback


def update_lcd(screen, **kwargs):
    """Update LCD display"""
    if lcd_callback:
        lcd_callback(screen, **kwargs)


# ============================================
# HTML TEMPLATES (Cyberpunk Terminal Style)
# ============================================

STYLE = """
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Courier New', monospace;
        background: #1A1D2E;
        color: #A7AFD4;
        min-height: 100vh;
        padding: 20px;
    }
    .container {
        max-width: 400px;
        margin: 0 auto;
    }
    .terminal-box {
        border: 2px solid #719253;
        padding: 20px;
        margin-bottom: 20px;
    }
    .header {
        color: #9C93DD;
        font-size: 14px;
        margin-bottom: 20px;
        text-align: center;
    }
    .header-line {
        border-bottom: 1px solid #9C93DD;
        margin-bottom: 10px;
        padding-bottom: 10px;
    }
    h1 {
        font-size: 18px;
        color: #719253;
        margin-bottom: 20px;
    }
    label {
        display: block;
        color: #C2995E;
        margin-bottom: 5px;
        font-size: 12px;
    }
    select, input[type="text"], input[type="password"] {
        width: 100%;
        padding: 12px;
        margin-bottom: 15px;
        background: #2A2D3E;
        border: 1px solid #A7AFD4;
        color: #FFFFFF;
        font-family: monospace;
        font-size: 16px;
    }
    select:focus, input:focus {
        outline: none;
        border-color: #719253;
    }
    button {
        width: 100%;
        padding: 15px;
        background: #719253;
        border: none;
        color: #1A1D2E;
        font-family: monospace;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        margin-top: 10px;
    }
    button:hover {
        background: #8BA76A;
    }
    button:disabled {
        background: #4A4A6A;
        cursor: not-allowed;
    }
    .hint {
        font-size: 11px;
        color: #6A6A8A;
        margin-top: -10px;
        margin-bottom: 15px;
    }
    .error {
        background: #D6697F;
        color: #1A1D2E;
        padding: 10px;
        margin-bottom: 15px;
        font-size: 12px;
    }
    .success {
        background: #719253;
        color: #1A1D2E;
        padding: 10px;
        margin-bottom: 15px;
        font-size: 12px;
    }
    .loading {
        text-align: center;
        padding: 40px;
    }
    .spinner {
        display: inline-block;
        animation: spin 1s linear infinite;
    }
    @keyframes spin {
        0% { content: '|'; }
        25% { content: '/'; }
        50% { content: '-'; }
        75% { content: '\\\\'; }
    }
    .progress {
        font-size: 14px;
        color: #719253;
        margin-top: 20px;
    }
    /* Guide slides */
    .slide {
        text-align: center;
        padding: 20px 0;
    }
    .slide h2 {
        color: #719253;
        font-size: 20px;
        margin-bottom: 15px;
    }
    .slide p {
        font-size: 14px;
        line-height: 1.6;
        margin-bottom: 10px;
    }
    .reaction-list {
        text-align: left;
        margin: 15px auto;
        max-width: 200px;
    }
    .reaction-list div {
        padding: 5px 0;
    }
    .tagline {
        color: #719253;
        font-size: 24px;
        font-weight: bold;
        margin: 20px 0;
    }
    .dots {
        display: flex;
        justify-content: center;
        gap: 8px;
        margin-top: 20px;
    }
    .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #4A4A6A;
    }
    .dot.active {
        background: #719253;
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


def render_page(title, content, auto_refresh=None):
    """Render a complete HTML page"""
    refresh_meta = f'<meta http-equiv="refresh" content="{auto_refresh}">' if auto_refresh else ''
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
    <title>{title}</title>
    {refresh_meta}
    {STYLE}
    {SCRIPT_COMMON}
</head>
<body>
    <div class="container">
        {content}
    </div>
</body>
</html>"""


# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    """Main setup page - redirect based on mode"""
    if setup_state.get('dev_mode'):
        # In dev mode, skip WiFi setup (already connected)
        return redirect('/setup/info')
    return redirect('/setup/wifi')


# ============================================
# CAPTIVE PORTAL DETECTION ROUTES
# ============================================

@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
def apple_captive_portal():
    """iOS/macOS captive portal detection"""
    return redirect('/setup/wifi')


@app.route('/generate_204')
@app.route('/gen_204')
def android_captive_portal():
    """Android captive portal detection - expects HTTP 204 or redirect"""
    # Return redirect instead of 204 to force portal open
    return redirect('/setup/wifi')


@app.route('/connecttest.txt')
@app.route('/redirect')
def windows_captive_portal():
    """Windows captive portal detection"""
    return redirect('/setup/wifi')


@app.route('/success.txt')
@app.route('/canonical.html')
def firefox_captive_portal():
    """Firefox captive portal detection"""
    return redirect('/setup/wifi')


@app.route('/setup/wifi')
def setup_wifi():
    """Step 1: WiFi network selection"""
    error_html = ''
    if setup_state.get('error'):
        error_html = f'<div class="error">{setup_state["error"]}</div>'
        setup_state['error'] = None

    content = f"""
    <div class="header header-line">
        LEELOO v1.0 ─── SETUP
    </div>

    <div class="terminal-box">
        <h1>┌─ CONNECT TO WIFI ─┐</h1>

        {error_html}

        <form action="/api/wifi" method="POST">
            <label>SELECT NETWORK:</label>
            <select name="ssid" id="ssid" required>
                <option value="">Scanning...</option>
            </select>

            <label>PASSWORD:</label>
            <input type="password" name="password" id="password" placeholder="Enter WiFi password" required>

            <button type="submit">CONNECT →</button>
        </form>

        <div class="progress">Step 1 of 4</div>
    </div>

    <script>
        // Load networks on page load
        async function loadNetworks() {{
            try {{
                const data = await fetchJSON('/api/networks');
                const select = document.getElementById('ssid');
                select.innerHTML = data.networks.map(n =>
                    `<option value="${{n}}">${{n}}</option>`
                ).join('');
                if (data.networks.length === 0) {{
                    select.innerHTML = '<option value="">No networks found</option>';
                }}
            }} catch (e) {{
                console.error('Failed to load networks:', e);
            }}
        }}
        loadNetworks();

        // Handle form submit
        document.querySelector('form').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'CONNECTING...';

            const formData = new FormData(e.target);
            const data = {{
                ssid: formData.get('ssid'),
                password: formData.get('password')
            }};

            try {{
                const result = await fetchJSON('/api/wifi', {{
                    method: 'POST',
                    body: JSON.stringify(data)
                }});

                if (result.success) {{
                    window.location.href = '/setup/crew';
                }} else {{
                    btn.disabled = false;
                    btn.textContent = 'CONNECT →';
                    alert(result.error || 'Connection failed');
                }}
            }} catch (e) {{
                btn.disabled = false;
                btn.textContent = 'CONNECT →';
                alert('Error: ' + e.message);
            }}
        }});
    </script>
    """
    return render_page("LEELOO Setup - WiFi", content)


@app.route('/setup/info')
def setup_info():
    """Step 2: User info (name, contacts, location)"""
    content = """
    <div class="header header-line">
        LEELOO v1.0 ─── SETUP
    </div>

    <div class="terminal-box">
        <h1>┌─ WHO ARE YOU? ─┐</h1>

        <form action="/api/info" method="POST">
            <label>FIRST NAME:</label>
            <input type="text" name="user_name" placeholder="Enter your first name" required>

            <label>FRIEND NAMES (optional):</label>
            <input type="text" name="contacts" placeholder="Amy, Ben, Sarah">
            <div class="hint">Names of friends you want to share music with</div>

            <label>ZIP CODE (for weather):</label>
            <input type="text" name="zip_code" placeholder="27601" pattern="[0-9]{5}" maxlength="5" required>

            <button type="submit">CONTINUE →</button>
        </form>

        <div class="progress">Step 2 of 4</div>
    </div>

    <script>
        document.querySelector('form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'SAVING...';

            const formData = new FormData(e.target);
            const data = {
                user_name: formData.get('user_name'),
                contacts: formData.get('contacts'),
                zip_code: formData.get('zip_code')
            };

            try {
                const result = await fetchJSON('/api/info', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                if (result.success) {
                    window.location.href = '/setup/guide';
                } else {
                    btn.disabled = false;
                    btn.textContent = 'CONTINUE →';
                    alert(result.error || 'Save failed');
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'CONTINUE →';
                alert('Error: ' + e.message);
            }
        });
    </script>
    """
    return render_page("LEELOO Setup - Info", content)


@app.route('/setup/crew')
def setup_crew():
    """Step 3: Crew setup - create or join"""
    content = """
    <div class="header header-line">
        LEELOO v1.0 ─── SETUP
    </div>

    <div class="terminal-box">
        <h1>┌─ JOIN YOUR CREW ─┐</h1>

        <p style="margin-bottom: 20px; color: #A7AFD4;">
            Connect with friends who have LEELOOs
        </p>

        <div class="crew-options">
            <label class="radio-option">
                <input type="radio" name="crew_action" value="create" checked>
                <span class="radio-label">Create a new crew</span>
                <span class="radio-desc">You're the first one setting up</span>
            </label>

            <label class="radio-option">
                <input type="radio" name="crew_action" value="join">
                <span class="radio-label">Join an existing crew</span>
                <span class="radio-desc">A friend shared a code with you</span>
            </label>
        </div>

        <button type="button" id="continueBtn">CONTINUE</button>

        <div class="progress">Step 3 of 4</div>
    </div>

    <style>
        .crew-options {
            margin: 20px 0;
        }
        .radio-option {
            display: block;
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid #4A4A6A;
            cursor: pointer;
            transition: border-color 0.2s;
        }
        .radio-option:hover {
            border-color: #719253;
        }
        .radio-option input {
            margin-right: 10px;
        }
        .radio-option input:checked + .radio-label {
            color: #719253;
        }
        .radio-label {
            font-size: 14px;
            color: #FFFFFF;
        }
        .radio-desc {
            display: block;
            margin-top: 5px;
            margin-left: 25px;
            font-size: 11px;
            color: #6A6A8A;
        }
    </style>

    <script>
        document.getElementById('continueBtn').addEventListener('click', function() {
            const action = document.querySelector('input[name="crew_action"]:checked').value;
            if (action === 'create') {
                window.location.href = '/setup/crew/create';
            } else {
                window.location.href = '/setup/crew/join';
            }
        });
    </script>
    """
    return render_page("LEELOO Setup - Crew", content)


@app.route('/setup/crew/create')
def setup_crew_create():
    """Step 3a: Create a new crew"""
    content = """
    <div class="header header-line">
        LEELOO v1.0 ─── CREATE CREW
    </div>

    <div class="terminal-box">
        <h1>┌─ NAME YOUR CREW ─┐</h1>

        <p style="margin-bottom: 20px; color: #A7AFD4;">
            Pick something fun your friends will recognize
        </p>

        <form id="createForm">
            <label>CREW NAME:</label>
            <input type="text" name="crew_name" placeholder="The Music Nerds" required maxlength="30">

            <button type="submit">CREATE CREW</button>
        </form>

        <div class="progress">Step 3 of 4</div>
    </div>

    <script>
        document.getElementById('createForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'CREATING...';

            const formData = new FormData(e.target);
            const data = {
                crew_name: formData.get('crew_name')
            };

            try {
                const result = await fetchJSON('/api/crew/create', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                if (result.success) {
                    window.location.href = '/setup/crew/created?code=' + result.invite_code + '&name=' + encodeURIComponent(result.crew_name);
                } else {
                    btn.disabled = false;
                    btn.textContent = 'CREATE CREW';
                    alert(result.error || 'Failed to create crew');
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'CREATE CREW';
                alert('Error: ' + e.message);
            }
        });
    </script>
    """
    return render_page("LEELOO Setup - Create Crew", content)


@app.route('/setup/crew/created')
def setup_crew_created():
    """Step 3b: Crew created - show invite code"""
    invite_code = request.args.get('code', 'XXXX123')
    crew_name = request.args.get('name', 'Your Crew')
    invite_url = f"leeloo.app/join/{invite_code}"

    content = f"""
    <div class="header header-line">
        LEELOO v1.0 ─── CREW CREATED
    </div>

    <div class="terminal-box" style="text-align: center;">
        <div style="font-size: 36px; margin: 10px 0; color: #719253;">✓</div>
        <h1 style="color: #719253;">CREW CREATED!</h1>

        <p style="margin: 15px 0; color: #FFFFFF;">"{crew_name}"</p>

        <p style="margin: 20px 0; color: #A7AFD4;">
            Share this with your friends:
        </p>

        <div class="invite-code" id="inviteCode" onclick="selectCode()">
            {invite_url}
        </div>
        <p style="font-size: 11px; color: #6A6A8A; margin-top: -10px;">
            ☝️ Tap to select, then copy/paste
        </p>

        <div class="share-buttons">
            <button type="button" class="share-btn" onclick="copyCode()">COPY TO CLIPBOARD</button>
        </div>

        <p style="font-size: 12px; color: #C2995E; margin: 15px 0; padding: 10px; background: #2A2D3E;">
            💡 This code is saved on your device!<br>
            Send to homies when you're done with setup.
        </p>

        <button type="button" onclick="window.location.href='/setup/info'" style="margin-top: 10px;">
            CONTINUE
        </button>
    </div>

    <style>
        .invite-code {{
            background: #2A2D3E;
            padding: 15px;
            margin: 15px 0;
            font-family: monospace;
            font-size: 14px;
            color: #719253;
            border: 1px solid #719253;
            word-break: break-all;
        }}
        .share-buttons {{
            display: flex;
            gap: 10px;
            justify-content: center;
            margin: 15px 0;
        }}
        .share-btn {{
            padding: 10px 20px;
            font-size: 12px;
            background: #2A2D3E;
            border: 1px solid #A7AFD4;
        }}
        .share-btn:hover {{
            background: #3A3D4E;
        }}
    </style>

    <script>
        const inviteUrl = '{invite_url}';
        const crewName = '{crew_name}';
        const shareMessage = `Join my LEELOO crew "${{crewName}}"! Go to ${{inviteUrl}}`;

        function selectCode() {{
            // Select text when tapped
            const el = document.getElementById('inviteCode');
            const range = document.createRange();
            range.selectNodeContents(el);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }}

        function copyCode() {{
            // Try modern clipboard API first
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(inviteUrl).then(() => {{
                    alert('✅ Copied to clipboard!\\n\\nYou can now paste it in a text or email.');
                }}).catch(err => {{
                    console.error('Clipboard error:', err);
                    selectCode();
                    alert('Text selected! Use your phone\\'s copy function.');
                }});
            }} else {{
                // Fallback: select text so user can manually copy
                selectCode();
                alert('Text selected! Tap Copy in the menu that appears.');
            }}
        }}

        function shareNative() {{
            // Use native share if available (works on iOS/Android)
            if (navigator.share) {{
                navigator.share({{
                    title: 'Join my LEELOO crew!',
                    text: shareMessage
                }}).catch(err => {{
                    // User cancelled or share failed
                    if (err.name !== 'AbortError') {{
                        console.error('Share error:', err);
                    }}
                }});
            }} else {{
                // Fallback to SMS
                window.open('sms:?body=' + encodeURIComponent(shareMessage), '_blank');
            }}
        }}
    </script>
    """
    return render_page("LEELOO Setup - Crew Created", content)


@app.route('/setup/crew/join')
def setup_crew_join():
    """Step 3c: Join an existing crew"""
    content = """
    <div class="header header-line">
        LEELOO v1.0 ─── JOIN CREW
    </div>

    <div class="terminal-box">
        <h1>┌─ JOIN A CREW ─┐</h1>

        <p style="margin-bottom: 20px; color: #A7AFD4;">
            Enter the invite code your friend shared with you
        </p>

        <form id="joinForm">
            <label>INVITE CODE:</label>
            <input type="text" name="invite_code" placeholder="WXYZ123" required
                   style="text-transform: uppercase;" maxlength="10"
                   pattern="[A-Za-z0-9]+">
            <div class="hint">The code at the end of leeloo.app/join/...</div>

            <button type="submit">JOIN CREW</button>
        </form>

        <div class="progress">Step 3 of 4</div>
    </div>

    <script>
        document.getElementById('joinForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.querySelector('button');
            btn.disabled = true;
            btn.textContent = 'JOINING...';

            const formData = new FormData(e.target);
            const data = {
                invite_code: formData.get('invite_code').toUpperCase()
            };

            try {
                const result = await fetchJSON('/api/crew/join', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                if (result.success) {
                    window.location.href = '/setup/crew/joined?name=' + encodeURIComponent(result.crew_name) + '&members=' + encodeURIComponent(result.members.join(', '));
                } else {
                    btn.disabled = false;
                    btn.textContent = 'JOIN CREW';
                    alert(result.error || 'Invalid invite code');
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'JOIN CREW';
                alert('Error: ' + e.message);
            }
        });
    </script>
    """
    return render_page("LEELOO Setup - Join Crew", content)


@app.route('/setup/crew/joined')
def setup_crew_joined():
    """Step 3d: Successfully joined crew"""
    crew_name = request.args.get('name', 'Your Crew')
    members = request.args.get('members', '')

    content = f"""
    <div class="header header-line">
        LEELOO v1.0 ─── JOINED!
    </div>

    <div class="terminal-box" style="text-align: center;">
        <div style="font-size: 36px; margin: 10px 0; color: #719253;">✓</div>
        <h1 style="color: #719253;">YOU'RE IN!</h1>

        <p style="margin: 20px 0; color: #FFFFFF;">
            Welcome to "{crew_name}"
        </p>

        <p style="margin: 15px 0; color: #A7AFD4;">
            Members: {members if members else 'You'}
        </p>

        <button type="button" onclick="window.location.href='/setup/info'" style="margin-top: 20px;">
            CONTINUE
        </button>
    </div>
    """
    return render_page("LEELOO Setup - Joined Crew", content)


@app.route('/setup/guide')
def setup_guide():
    """Step 3: Quick start guide"""
    content = """
    <div class="header header-line">
        LEELOO v1.0 ─── QUICK GUIDE
    </div>

    <div class="slides" id="slides">
        <!-- Slide 1: Welcome -->
        <div class="slide" data-slide="0">
            <div class="terminal-box">
                <h2>You're all set!</h2>
                <p>Quick guide to your new LEELOO.</p>
                <p style="color: #6A6A8A; margin-top: 20px;">Swipe to learn →</p>
                <div class="dots">
                    <div class="dot active"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                </div>
            </div>
        </div>

        <!-- Slide 2: Share Things -->
        <div class="slide" data-slide="1" style="display:none;">
            <div class="terminal-box">
                <h2>Share Things</h2>
                <p>Tap the TOP of your LEELOO (not the screen) and talk to it.</p>
                <p style="margin-top: 10px;">Ask about weather, time, send messages, and music.</p>
                <p style="background:#2A2D3E; padding:10px; margin:15px 0; font-size: 13px;">"send my homies Lets Dance by David Bowie"</p>
                <p style="font-size: 12px; color: #6A6A8A;">Tap and ask "what can I ask?" for more help.</p>
                <div class="dots">
                    <div class="dot"></div>
                    <div class="dot active"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                </div>
            </div>
        </div>

        <!-- Slide 3: Tap Reactions -->
        <div class="slide" data-slide="2" style="display:none;">
            <div class="terminal-box">
                <h2>Tap Reactions</h2>
                <p>During a message or music share:</p>
                <div class="reaction-list" style="text-align: center; margin: 20px 0;">
                    <div style="margin-bottom: 10px;">Double tap = Love</div>
                    <div>Triple tap = Fire</div>
                    <pre style="color: #C2995E; font-size: 10px; margin: 10px 0;">    )
   ) \\
  (   )
   ) (
  (   )
 __)  (__</pre>
                </div>
                <div class="dots">
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot active"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                </div>
            </div>
        </div>

        <!-- Slide 4: Thinking of You -->
        <div class="slide" data-slide="3" style="display:none;">
            <div class="terminal-box">
                <h2>Thinking of You</h2>
                <p>Miss your friends?</p>
                <p style="margin-top: 15px;">Double tap when NOT receiving music or messages.</p>
                <p style="margin-top: 10px;">It sends a "thinking of you" notification.</p>
                <p style="background:#2A2D3E; padding:10px; margin:15px 0; font-size: 13px;">Like passing a note under the table in class.</p>
                <div class="dots">
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot active"></div>
                    <div class="dot"></div>
                </div>
            </div>
        </div>

        <!-- Slide 5: Done -->
        <div class="slide" data-slide="4" style="display:none;">
            <div class="terminal-box" style="border-color: #719253;">
                <p>Now put your phone away and enjoy tech that adds value to your life.</p>
                <div class="tagline">More fun, less phone.</div>
                <button onclick="window.location.href='/done'">START USING LEELOO</button>
                <div class="dots">
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot active"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentSlide = 0;
        const totalSlides = 5;

        function showSlide(n) {
            document.querySelectorAll('.slide').forEach((s, i) => {
                s.style.display = i === n ? 'block' : 'none';
            });
            currentSlide = n;
        }

        // Swipe detection
        let startX = 0;
        document.addEventListener('touchstart', e => startX = e.touches[0].clientX);
        document.addEventListener('touchend', e => {
            const diff = startX - e.changedTouches[0].clientX;
            if (Math.abs(diff) > 50) {
                if (diff > 0 && currentSlide < totalSlides - 1) {
                    showSlide(currentSlide + 1);
                } else if (diff < 0 && currentSlide > 0) {
                    showSlide(currentSlide - 1);
                }
            }
        });

        // Also allow click to advance
        document.addEventListener('click', e => {
            if (e.target.tagName !== 'BUTTON') {
                if (currentSlide < totalSlides - 1) {
                    showSlide(currentSlide + 1);
                }
            }
        });
    </script>
    """
    return render_page("LEELOO - Quick Guide", content)


@app.route('/done')
def done():
    """Setup complete - trigger WiFi connection"""
    content = """
    <div class="terminal-box" style="text-align: center; border-color: #719253;">
        <div style="font-size: 48px; margin: 20px 0;">✓</div>
        <h1 style="color: #719253;">SETUP COMPLETE</h1>
        <p style="margin: 20px 0;" id="status">Connecting to WiFi...</p>
        <p style="color: #6A6A8A; font-size: 12px;" id="instructions">Please wait...</p>
    </div>

    <script>
        // Trigger WiFi connection immediately
        fetch('/api/finish', {method: 'POST'})
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('status').textContent = 'Your LEELOO is ready!';
                    document.getElementById('instructions').textContent = 'You can close this page now.';
                } else {
                    document.getElementById('status').textContent = 'Setup saved!';
                    document.getElementById('instructions').textContent = 'Power cycle your LEELOO to connect to WiFi.';
                }
            })
            .catch(err => {
                document.getElementById('status').textContent = 'Setup saved!';
                document.getElementById('instructions').textContent = 'Power cycle your LEELOO to connect to WiFi.';
            });
    </script>
    """
    return render_page("LEELOO - Ready", content)


@app.route('/connecting')
def connecting():
    """Connecting screen (auto-refreshes)"""
    ssid = setup_state.get('ssid', 'WiFi')
    content = f"""
    <div class="terminal-box" style="text-align: center;">
        <h1>CONNECTING...</h1>
        <p style="margin: 20px 0;">Connecting to {ssid}</p>
        <div class="loading">
            <span class="spinner">|</span>
        </div>
        <p style="color: #6A6A8A; font-size: 12px;">Please wait...</p>
    </div>
    """
    return render_page("LEELOO - Connecting", content, auto_refresh=3)


# ============================================
# API ENDPOINTS
# ============================================

@app.route('/api/networks', methods=['GET'])
def api_networks():
    """Return list of available WiFi networks"""
    networks = scan_wifi_networks()
    return jsonify({'networks': networks})


@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    """Save WiFi credentials (don't connect yet - wait until setup is complete)"""
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')

    if not ssid or not password:
        return jsonify({'success': False, 'error': 'Missing SSID or password'})

    # Save WiFi credentials to config (we'll connect after setup is complete)
    config = load_config()
    config['wifi_ssid'] = ssid
    config['wifi_password'] = password

    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        sync_config_to_gadget()
        print(f"WiFi credentials saved: {ssid}")
    except Exception as e:
        print(f"Error saving WiFi config: {e}")
        return jsonify({'success': False, 'error': 'Failed to save WiFi credentials'})

    setup_state['ssid'] = ssid
    setup_state['step'] = 'info'

    # Success! Phone can now proceed to step 2
    return jsonify({'success': True})


@app.route('/api/info', methods=['POST'])
def api_info():
    """Save user info and complete setup"""
    data = request.get_json()

    user_name = data.get('user_name', '').strip()
    contacts_str = data.get('contacts', '')
    zip_code = data.get('zip_code', '').strip()

    if not user_name:
        return jsonify({'success': False, 'error': 'Name is required'})

    if not zip_code or len(zip_code) != 5 or not zip_code.isdigit():
        return jsonify({'success': False, 'error': 'Valid 5-digit ZIP code required'})

    # Parse contacts
    contacts = [c.strip() for c in contacts_str.split(',') if c.strip()]

    # Convert ZIP to lat/lon using geocoding API (free, no key needed)
    latitude, longitude = None, None
    try:
        import requests
        # Use Nominatim (OpenStreetMap's free geocoding service)
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=US&format=json&limit=1"
        headers = {'User-Agent': 'LEELOO-Setup/1.0'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                latitude = float(data[0]['lat'])
                longitude = float(data[0]['lon'])
                print(f"Converted ZIP {zip_code} to lat/lon: {latitude}, {longitude}")
    except Exception as e:
        print(f"ZIP to lat/lon conversion failed: {e}")
        # Continue without coordinates - weather won't work but setup can complete

    # Save config with lat/lon for weather
    # Note: setup_complete will be set after crew setup
    config = load_config()
    config.update({
        'user_name': user_name,
        'contacts': contacts,
        'location': {'zip_code': zip_code},
        'wifi_ssid': setup_state.get('ssid', ''),
    })

    # Add lat/lon if we got them
    if latitude and longitude:
        config['latitude'] = latitude
        config['longitude'] = longitude

    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        sync_config_to_gadget()
        print(f"Config saved: {config}")
    except Exception as e:
        print(f"Error saving config: {e}")
        return jsonify({'success': False, 'error': 'Failed to save config'})

    return jsonify({'success': True})


def load_config():
    """Load existing config or return empty dict"""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except:
        return {}


def sync_config_to_gadget():
    """Sync captive portal config to gadget_main format"""
    try:
        # Read captive portal config
        with open(CONFIG_PATH, 'r') as f:
            portal_config = json.load(f)

        # Create device_config.json for gadget_main
        device_config = {
            "latitude": portal_config.get("location", {}).get("latitude"),
            "longitude": portal_config.get("location", {}).get("longitude"),
            "zip_code": portal_config.get("location", {}).get("zip_code"),
            "user_name": portal_config.get("user_name")
        }

        device_config_path = "/home/pi/leeloo-ui/device_config.json"
        with open(device_config_path, 'w') as f:
            json.dump(device_config, f, indent=2)

        # Create crew_config.json for gadget_main
        crew_data = portal_config.get("crew", {})
        crew_config = {
            "name": crew_data.get("name"),
            "invite_code": crew_data.get("invite_code"),
            "is_creator": crew_data.get("is_creator", False),
            "members": portal_config.get("contacts", [])
        }

        crew_config_path = "/home/pi/leeloo-ui/crew_config.json"
        with open(crew_config_path, 'w') as f:
            json.dump(crew_config, f, indent=2)

        print(f"Synced config to gadget_main format: device_config.json, crew_config.json")
    except Exception as e:
        print(f"Error syncing config to gadget format: {e}")


def generate_invite_code():
    """Generate a random 7-character invite code"""
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(7))


@app.route('/api/crew/create', methods=['POST'])
def api_crew_create():
    """Create a new crew"""
    data = request.get_json()
    crew_name = data.get('crew_name', '').strip()

    if not crew_name:
        return jsonify({'success': False, 'error': 'Crew name is required'})

    if len(crew_name) > 30:
        return jsonify({'success': False, 'error': 'Crew name too long (max 30 chars)'})

    # Generate invite code
    invite_code = generate_invite_code()

    # Save to config
    config = load_config()
    config['crew'] = {
        'name': crew_name,
        'invite_code': invite_code,
        'is_creator': True,
        'members': [config.get('user_name', 'You')]
    }
    config['setup_complete'] = True

    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        sync_config_to_gadget()
        print(f"Crew created: {crew_name} ({invite_code})")
    except Exception as e:
        print(f"Error saving crew config: {e}")
        return jsonify({'success': False, 'error': 'Failed to save crew'})

    setup_state['step'] = 'done'
    update_lcd('success')

    # Note: WiFi connection happens AFTER portal exits
    # See connect_saved_wifi.py which runs after Ctrl+C

    return jsonify({
        'success': True,
        'crew_name': crew_name,
        'invite_code': invite_code
    })


@app.route('/api/crew/join', methods=['POST'])
def api_crew_join():
    """Join an existing crew"""
    data = request.get_json()
    invite_code = data.get('invite_code', '').strip().upper()

    if not invite_code:
        return jsonify({'success': False, 'error': 'Invite code is required'})

    # In a real implementation, this would validate against a backend server
    # For now, we'll accept any code and simulate a successful join
    # The backend would return the crew name and member list

    # TODO: Replace with actual backend API call
    # response = requests.post('https://api.leeloo.app/crew/join', json={'code': invite_code})

    # For demo purposes, simulate a successful join
    # In production, this would validate the code against the server
    simulated_crew_name = f"Crew {invite_code[:4]}"
    simulated_members = ['Friend 1', 'Friend 2']

    # Save to config
    config = load_config()
    config['crew'] = {
        'name': simulated_crew_name,
        'invite_code': invite_code,
        'is_creator': False,
        'members': simulated_members + [config.get('user_name', 'You')]
    }
    config['setup_complete'] = True

    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        sync_config_to_gadget()
        print(f"Joined crew: {simulated_crew_name}")
    except Exception as e:
        print(f"Error saving crew config: {e}")
        return jsonify({'success': False, 'error': 'Failed to save crew'})

    setup_state['step'] = 'done'
    update_lcd('success')

    # Note: WiFi connection happens AFTER portal exits
    # See connect_saved_wifi.py which runs after Ctrl+C

    return jsonify({
        'success': True,
        'crew_name': simulated_crew_name,
        'members': simulated_members
    })


@app.route('/api/finish', methods=['POST'])
def api_finish():
    """Finish setup and connect to WiFi"""
    import subprocess

    config = load_config()

    if not config.get('setup_complete'):
        return jsonify({'success': False, 'error': 'Setup not complete'})

    # Trigger WiFi connection in background (don't wait for it)
    try:
        subprocess.Popen(
            ['sudo', 'python3', '/home/pi/leeloo-ui/connect_saved_wifi.py'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # Detach from parent
        )
        return jsonify({'success': True, 'message': 'Connecting to WiFi...'})
    except Exception as e:
        print(f"Failed to trigger WiFi connection: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ============================================
# MAIN ENTRY POINT
# ============================================

def run_captive_portal(lcd_update_callback=None, dev_mode=False):
    """
    Run the captive portal setup flow
    Blocks until setup is complete

    Args:
        lcd_update_callback: Function to call for LCD updates
        dev_mode: If True, skip AP mode and run on port 8080
    """
    global lcd_callback
    lcd_callback = lcd_update_callback
    setup_state['dev_mode'] = dev_mode

    if dev_mode:
        print("Starting setup server in DEV MODE (port 8080)...")
        print("Open http://leeloo.local:8080 on your phone")
        update_lcd('phone_connected')  # Show "complete setup on phone" screen
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
    else:
        print("Starting captive portal in PRODUCTION MODE...")
        # Start AP mode
        ssid = start_ap_mode()
        update_lcd('ap_mode', ssid=ssid)

        # Run Flask on port 80 (for captive portal auto-open)
        try:
            app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
        except PermissionError:
            print("Port 80 requires root. Trying port 8080...")
            app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)


if __name__ == "__main__":
    import sys
    dev_mode = '--dev' in sys.argv or '-d' in sys.argv
    run_captive_portal(dev_mode=dev_mode)
