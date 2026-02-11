#!/usr/bin/env python3
"""
Geocode ZIP code to lat/lon after WiFi connection
Run this AFTER the Pi connects to internet
"""

import json
import time
import sys

DEVICE_CONFIG_PATH = "/home/pi/leeloo-ui/device_config.json"

def geocode_zip_with_geonames(zip_code, username='leeloo_2255'):
    """
    Convert ZIP code to lat/lon + timezone using GeoNames API
    Returns: (lat, lon, timezone) or (None, None, None) on failure

    Note: Create a free account at https://www.geonames.org/login
    """
    try:
        import requests

        # Step 1: Get coordinates from ZIP code
        print(f"Looking up ZIP {zip_code} via GeoNames...")
        postal_url = f"http://api.geonames.org/postalCodeSearchJSON?postalcode={zip_code}&country=US&username={username}"
        resp = requests.get(postal_url, timeout=10)

        if resp.status_code != 200:
            print(f"✗ GeoNames API returned status {resp.status_code}")
            return None, None, None

        data = resp.json()

        # Check for errors
        if 'status' in data:
            error_msg = data['status'].get('message', 'Unknown error')
            print(f"✗ GeoNames API error: {error_msg}")
            if 'user account not enabled' in error_msg.lower():
                print("  Enable account at https://www.geonames.org/manageaccount")
            return None, None, None

        # Parse coordinates
        if not data.get('postalCodes') or len(data['postalCodes']) == 0:
            print(f"✗ No results for ZIP {zip_code}")
            return None, None, None

        result = data['postalCodes'][0]
        lat = float(result['lat'])
        lon = float(result['lng'])
        place_name = result.get('placeName', 'Unknown')
        state = result.get('adminName1', '')

        print(f"✓ ZIP {zip_code}: {place_name}, {state}")
        print(f"✓ Coordinates: {lat}, {lon}")

        # Step 2: Get timezone from coordinates
        print(f"Looking up timezone...")
        tz_url = f"http://api.geonames.org/timezoneJSON?lat={lat}&lng={lon}&username={username}"
        tz_resp = requests.get(tz_url, timeout=10)

        if tz_resp.status_code == 200:
            tz_data = tz_resp.json()
            if 'status' not in tz_data and 'timezoneId' in tz_data:
                timezone = tz_data['timezoneId']
                print(f"✓ Timezone: {timezone}")
                return lat, lon, timezone
            else:
                print(f"⚠️  Could not get timezone data")
                return lat, lon, None
        else:
            print(f"⚠️  Timezone lookup failed")
            return lat, lon, None

    except Exception as e:
        print(f"✗ GeoNames lookup failed: {e}")
        return None, None, None


def geocode_zip(zip_code):
    """
    Convert ZIP code to lat/lon using GeoNames (with timezone) or fallback to Nominatim
    Returns: (lat, lon, timezone) or (lat, lon, None) on partial success
    """
    # Try GeoNames first (gets timezone too!)
    lat, lon, tz = geocode_zip_with_geonames(zip_code)

    if lat and lon:
        return lat, lon, tz

    # Fallback to Nominatim if GeoNames fails (no timezone though)
    print("⚠️  GeoNames failed, trying Nominatim fallback...")
    try:
        import requests
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=US&format=json&limit=1"
        headers = {'User-Agent': 'LEELOO-Setup/1.0'}

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                print(f"✓ Nominatim fallback: {lat}, {lon}")
                return lat, lon, None

    except Exception as e:
        print(f"✗ Nominatim fallback failed: {e}")

    return None, None, None


def main():
    """Main function - wait for internet, then geocode"""

    # Wait a bit for WiFi to fully connect
    print("Waiting 10 seconds for WiFi connection...")
    time.sleep(10)

    # Load device config
    try:
        with open(DEVICE_CONFIG_PATH, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"✗ Config file not found: {DEVICE_CONFIG_PATH}")
        sys.exit(1)

    # Check if we already have lat/lon
    if config.get('latitude') and config.get('longitude'):
        print(f"✓ Already have coordinates: {config['latitude']}, {config['longitude']}")
        sys.exit(0)

    # Check if we have a ZIP code to geocode
    zip_code = config.get('zip_code')
    if not zip_code:
        print("✗ No ZIP code in config, cannot geocode")
        sys.exit(1)

    # Geocode the ZIP (gets lat, lon, and timezone)
    lat, lon, tz_name = geocode_zip(zip_code)

    if lat and lon:
        # Update config with coordinates
        config['latitude'] = lat
        config['longitude'] = lon

        # Add timezone if we got it
        if tz_name:
            config['timezone'] = tz_name
            print(f"\n✓ Timezone: {tz_name}")
        else:
            print("\n⚠️  No timezone data - time will use system timezone")

        with open(DEVICE_CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"\n✓ Updated {DEVICE_CONFIG_PATH}")
        print("✓ Weather and local time should now work!")
    else:
        print("✗ Failed to geocode ZIP - weather will not work")
        sys.exit(1)


if __name__ == "__main__":
    main()
