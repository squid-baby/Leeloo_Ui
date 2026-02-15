#!/usr/bin/env python3
"""Test Spotify search to see what gets returned for bad queries"""

import requests
import base64
import os
import json

# Load from Pi's .env (SSH and read)
import subprocess

result = subprocess.run(
    ["sshpass", "-p", "gadget", "ssh", "pi@leeloo.local",
     "cd /home/pi/leeloo-ui && python3 -c \"import requests; import base64; import os; import json; exec(open('.env').read().replace('export ', '').replace('SPOTIFY_CLIENT_ID=', 'client_id=').replace('SPOTIFY_CLIENT_SECRET=', 'client_secret=')); auth_str=f'{client_id}:{client_secret}'; auth_b64=base64.b64encode(auth_str.encode()).decode(); token_resp=requests.post('https://accounts.spotify.com/api/token', data={'grant_type': 'client_credentials'}, headers={'Authorization': f'Basic {auth_b64}', 'Content-Type': 'application/x-www-form-urlencoded'}); token=token_resp.json()['access_token']; headers={'Authorization': f'Bearer {token}'}; search_resp=requests.get('https://api.spotify.com/v1/search', params={'q': 'smoke and intolerance', 'type': 'track', 'limit': 3}, headers=headers); print(json.dumps(search_resp.json(), indent=2))\""],
    capture_output=True,
    text=True
)

print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
