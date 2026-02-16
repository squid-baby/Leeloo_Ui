#!/usr/bin/env python3
"""
Interactive test server for LEELOO reactions
Serves the web UI and handles reaction triggers
"""

import http.server
import socketserver
import urllib.parse
import subprocess
import threading
import webbrowser
import time
from pathlib import Path

PORT = 8080


class LEELOOTestHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        """Handle POST requests to trigger reactions"""
        if self.path.startswith('/trigger-reaction'):
            # Parse query parameters
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            reaction_type = params.get('type', ['love'])[0]
            sender = params.get('sender', ['Amy'])[0]

            print(f"\n→ Triggering {reaction_type} reaction from {sender}")

            # Run the reaction trigger script in background
            try:
                subprocess.Popen([
                    'python3', 'reaction_trigger.py',
                    reaction_type, sender
                ], cwd='/Users/nathanmills/Desktop/TipTop UI')

                # Send success response
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status": "triggered"}')

            except Exception as e:
                print(f"Error triggering reaction: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error"}')

        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        # Add cache control headers
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        # Suppress standard request logging (too noisy)
        if '/trigger-reaction' in args[0]:
            return  # Don't log trigger requests
        # Only log page loads
        if 'GET' in args[0] and '.html' in args[0]:
            print(f"→ {args[0]}")


def start_server():
    """Start the HTTP server"""
    with socketserver.TCPServer(("", PORT), LEELOOTestHandler) as httpd:
        print(f"✓ Server running at http://localhost:{PORT}")
        httpd.serve_forever()


def open_browser():
    """Open browser to the interactive test page"""
    time.sleep(1.5)
    url = f'http://localhost:{PORT}/interactive_test.html'
    print(f"→ Opening browser to {url}")
    webbrowser.open(url)


if __name__ == '__main__':
    print("\n🛸 LEELOO Interactive Reaction Tester")
    print("=" * 60)
    print(f"Starting server on port {PORT}...")
    print()

    # Start server in background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Open browser
    open_browser()

    print("\n" + "=" * 60)
    print("✓ Server ready!")
    print("  Click reaction buttons in browser to test animations")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n✓ Server stopped")
