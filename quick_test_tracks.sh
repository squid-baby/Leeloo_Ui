#!/bin/bash
# Quick test with popular tracks
# Usage: ./quick_test_tracks.sh [number]

echo "🎵 LEELOO Spotify Scancode Quick Test"
echo ""
echo "Popular tracks to test:"
echo ""
echo "1. Sabotage - Beastie Boys"
echo "2. Mr. Brightside - The Killers"
echo "3. Bohemian Rhapsody - Queen"
echo "4. Song 2 - Blur (default)"
echo "5. Smells Like Teen Spirit - Nirvana"
echo ""

# Track URLs (in order)
declare -a TRACKS=(
    "https://open.spotify.com/track/7c9KnMJ6pRdgqLMSDFlJSw"  # Sabotage
    "https://open.spotify.com/track/003vvx7Niy0yvhvHt4a68B"  # Mr. Brightside
    "https://open.spotify.com/track/7tFiyTwD0nx5a1eklYtX2J"  # Bohemian Rhapsody
    "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"  # Song 2
    "https://open.spotify.com/track/1NMDYDkqLMHFDj94ywucqR"  # Smells Like Teen Spirit
)

# If number provided, use that track
if [ $# -eq 1 ]; then
    CHOICE=$1
else
    read -p "Choose a track (1-5) [4]: " CHOICE
    CHOICE=${CHOICE:-4}
fi

# Validate choice
if [[ ! $CHOICE =~ ^[1-5]$ ]]; then
    echo "Invalid choice. Using default (Song 2)"
    CHOICE=4
fi

# Get the track URL (arrays are 0-indexed)
INDEX=$((CHOICE - 1))
TRACK_URL="${TRACKS[$INDEX]}"

echo ""
echo "▶️  Testing track #$CHOICE"
echo "   URL: $TRACK_URL"
echo ""

# Run the test
python3 test_spotify_display.py "$TRACK_URL"

# If successful and on Pi, display it
if [ $? -eq 0 ] && [ -e /dev/fb1 ]; then
    echo ""
    echo "📺 Displaying on screen..."
    python3 show_on_display.py spotify_display_preview.png
fi
