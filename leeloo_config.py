#!/usr/bin/env python3
"""
LEELOO Configuration
Centralized configuration for all LEELOO components.
"""

import os
import json
from pathlib import Path


class Config:
    """LEELOO device configuration"""

    # =========================================================================
    # PATHS
    # =========================================================================

    # Base directory (can be overridden with LEELOO_HOME env var)
    # Default to current directory if not on Pi
    _default_home = "/home/pi/leeloo-ui" if os.path.exists("/home/pi") else os.getcwd()
    LEELOO_HOME = os.environ.get("LEELOO_HOME", _default_home)

    # Device configuration file
    CONFIG_FILE = os.path.join(LEELOO_HOME, "device_config.json")

    # State files
    FIRST_RUN_COMPLETE = os.path.join(LEELOO_HOME, ".first_run_complete")
    WIFI_CONFIG_PATH = os.path.join(LEELOO_HOME, "wifi_config.json")
    CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, "crew_config.json")
    HANG_DATA_PATH = os.path.join(LEELOO_HOME, "hang_data.json")

    # Assets
    BOOT_IMAGE = os.path.join(LEELOO_HOME, "LeeLoo_boot.png")
    SCANCODE_PLACEHOLDER = os.path.join(LEELOO_HOME, "leeloo_empty_scancode.png")
    ALBUM_ART_DIR = os.path.join(LEELOO_HOME, "album_art")

    # =========================================================================
    # HARDWARE
    # =========================================================================

    # Framebuffer device
    FRAMEBUFFER = os.environ.get("FB_DEVICE", "/dev/fb1")

    # Screen dimensions (Waveshare 3.5" LCD)
    SCREEN_WIDTH = 480
    SCREEN_HEIGHT = 320

    # =========================================================================
    # NETWORK
    # =========================================================================

    # Relay server URL
    RELAY_URL = os.environ.get("RELAY_URL", "wss://relay.leeloo.fm")
    RELAY_URL_DEV = "ws://localhost:8765"

    # WiFi check settings
    WIFI_CHECK_HOST = os.environ.get("WIFI_CHECK_HOST", "8.8.8.8")
    WIFI_CHECK_TIMEOUT = 5  # seconds
    WIFI_RETRY_INTERVAL = 5  # seconds
    WIFI_MAX_RETRIES = 60  # 5 minutes max wait

    # =========================================================================
    # SERVICES
    # =========================================================================

    # Weather API (Open-Meteo)
    WEATHER_REFRESH_INTERVAL = 600  # 10 minutes
    DEFAULT_LATITUDE = 35.9101  # Carrboro, NC (default)
    DEFAULT_LONGITUDE = -79.0753

    # Telegram
    TELEGRAM_BOT_TOKEN = os.environ.get("LEELOO_BOT_TOKEN", "")

    # =========================================================================
    # DISPLAY
    # =========================================================================

    # Colors (LEELOO palette)
    COLORS = {
        'bg': '#1A1D2E',
        'green': '#719253',
        'purple': '#9C93DD',
        'rose': '#D6697F',
        'tan': '#C2995E',
        'lavender': '#A7AFD4',
        'white': '#FFFFFF',
        'orange': '#FF8800',
        'yellow': '#D4A84B',
    }

    # Fonts (with fallbacks)
    FONT_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    # Animation settings
    ANIMATION_FPS = 24
    ANIMATION_DURATION = 1.5  # seconds

    # =========================================================================
    # SPOTIFY
    # =========================================================================

    # Spotify scancode API
    SPOTIFY_SCANCODE_BASE_URL = "https://scannables.scdn.co/uri/plain/png"
    SPOTIFY_SCANCODE_BG_COLOR = "1A1D2E"  # Match LEELOO bg
    SPOTIFY_SCANCODE_FG_COLOR = "white"
    SPOTIFY_SCANCODE_SIZE = 280

    # =========================================================================
    # DEVICE INFO
    # =========================================================================

    VERSION = "1.0.0"
    DEVICE_NAME = "LEELOO"

    # =========================================================================
    # METHODS
    # =========================================================================

    _device_config = None

    @classmethod
    def load_device_config(cls):
        """Load device-specific configuration from file"""
        if cls._device_config is not None:
            return cls._device_config

        try:
            with open(cls.CONFIG_FILE, 'r') as f:
                cls._device_config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cls._device_config = {}

        return cls._device_config

    @classmethod
    def save_device_config(cls, config: dict):
        """Save device configuration to file"""
        cls._device_config = config
        os.makedirs(os.path.dirname(cls.CONFIG_FILE), exist_ok=True)
        with open(cls.CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)

    @classmethod
    def get(cls, key: str, default=None):
        """Get a config value from device config file"""
        config = cls.load_device_config()
        return config.get(key, default)

    @classmethod
    def set(cls, key: str, value):
        """Set a config value in device config file"""
        config = cls.load_device_config()
        config[key] = value
        cls.save_device_config(config)

    @classmethod
    def get_location(cls):
        """Get configured location or default"""
        config = cls.load_device_config()
        return (
            config.get('latitude', cls.DEFAULT_LATITUDE),
            config.get('longitude', cls.DEFAULT_LONGITUDE)
        )

    @classmethod
    def get_relay_url(cls):
        """Get relay URL based on environment"""
        if os.environ.get("LEELOO_DEV"):
            return cls.RELAY_URL_DEV
        return cls.RELAY_URL

    @classmethod
    def is_first_run(cls):
        """Check if this is a first-time boot"""
        return not os.path.exists(cls.FIRST_RUN_COMPLETE)

    @classmethod
    def mark_first_run_complete(cls):
        """Mark first run as complete"""
        Path(cls.FIRST_RUN_COMPLETE).touch()

    @classmethod
    def ensure_directories(cls):
        """Ensure all required directories exist"""
        dirs = [
            cls.LEELOO_HOME,
            cls.ALBUM_ART_DIR,
            os.path.dirname(cls.CONFIG_FILE),
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)


# Convenience access
config = Config()
