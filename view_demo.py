#!/usr/bin/env python3
"""
Web viewer for LEELOO reaction animations
Run this to see the animations in your browser
"""

import http.server
import socketserver
import webbrowser
import threading
import time
import os

PORT = 8080

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Expires', '0')
        super().end_headers()

def start_server():
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"✓ Server running at http://localhost:{PORT}")
        httpd.serve_forever()

def open_browser():
    time.sleep(1)
    webbrowser.open(f'http://localhost:{PORT}/tmp/leeloo_preview.png')

if __name__ == '__main__':
    print("\n🛸 LEELOO Animation Web Viewer")
    print("=" * 50)
    print(f"Starting web server on port {PORT}...")
    
    # Start server in background
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    # Open browser
    print("Opening browser...")
    open_browser()
    
    print("\n" + "=" * 50)
    print("Press Ctrl+C to stop the server")
    print("=" * 50 + "\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n✓ Server stopped")

