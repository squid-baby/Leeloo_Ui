#!/usr/bin/env python3
"""
LEELOO Voice Manager — Microphone recording + Deepgram Nova-2 STT

Records audio from the INMP441 I2S microphone via ALSA (arecord subprocess),
streams chunks to Deepgram's WebSocket API for real-time transcription.

Features:
- Streaming STT via Deepgram Nova-2 (wss://api.deepgram.com/v1/listen)
- Local silence detection (RMS energy threshold) to stop recording
- 15-second max recording duration
- Returns final transcript string

Audio format:
- Device: plughw:0 (INMP441 via googlevoicehat-soundcard overlay)
- Format: S16_LE (16-bit signed little-endian)
- Rate: 16000 Hz
- Channels: 1 (mono)
"""

import asyncio
import json
import struct
import math

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("[VOICE] websockets not installed — voice disabled")


# Recording parameters
DEVICE = "plughw:0"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_FORMAT = "S16_LE"
BYTES_PER_SAMPLE = 2  # 16-bit

# Timing
MAX_RECORD_SECONDS = 15
CHUNK_DURATION_MS = 100  # Send chunks every 100ms
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)
CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE * CHANNELS

# Silence detection
SILENCE_THRESHOLD = 30        # RMS below this = silence (INMP441 is quiet: speech ~40-70, ambient ~3-5)
SILENCE_DURATION = 1.5        # Seconds of silence after speech before stopping
NO_SPEECH_TIMEOUT = 5.0       # Stop if no speech detected within this time
INITIAL_GRACE_PERIOD = 0.5    # Don't detect silence in first 0.5s

# Deepgram
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_PARAMS = (
    "model=nova-2"
    "&language=en"
    "&smart_format=true"
    "&endpointing=300"
    "&encoding=linear16"
    f"&sample_rate={SAMPLE_RATE}"
    f"&channels={CHANNELS}"
)


def _compute_rms(chunk_bytes):
    """Compute RMS energy of a 16-bit PCM audio chunk"""
    if len(chunk_bytes) < 2:
        return 0
    n_samples = len(chunk_bytes) // 2
    try:
        samples = struct.unpack(f'<{n_samples}h', chunk_bytes[:n_samples * 2])
        if not samples:
            return 0
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / n_samples)
    except struct.error:
        return 0


class VoiceManager:
    """Async voice recording + Deepgram streaming STT"""

    def __init__(self, deepgram_api_key):
        """
        Args:
            deepgram_api_key: Deepgram API key for authentication
        """
        self.api_key = deepgram_api_key
        self._recording = False

    async def record_and_transcribe(self) -> str:
        """
        Record audio from microphone and transcribe via Deepgram.

        Returns:
            Transcript string, or empty string on failure/silence.
        """
        if not WEBSOCKETS_AVAILABLE:
            print("[VOICE] websockets not available")
            return ""

        if not self.api_key:
            print("[VOICE] No Deepgram API key configured")
            return ""

        self._recording = True
        transcript = ""

        try:
            # Build Deepgram WebSocket URL
            ws_url = f"{DEEPGRAM_WS_URL}?{DEEPGRAM_PARAMS}"
            headers = {"Authorization": f"Token {self.api_key}"}

            # Connect to Deepgram
            print("[VOICE] Connecting to Deepgram...")
            async with websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=None,  # Deepgram handles keepalive
            ) as ws:
                print("[VOICE] Connected to Deepgram Nova-2")

                # Start arecord subprocess
                arecord_cmd = [
                    "arecord",
                    "-D", DEVICE,
                    "-f", SAMPLE_FORMAT,
                    "-r", str(SAMPLE_RATE),
                    "-c", str(CHANNELS),
                    "-t", "raw",
                    "-q",  # quiet
                    "-"    # stdout
                ]

                process = await asyncio.create_subprocess_exec(
                    *arecord_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )

                print(f"[VOICE] Recording... (max {MAX_RECORD_SECONDS}s)")

                # Collect transcripts from Deepgram
                final_transcript = []
                interim_transcript = ""

                async def receive_results():
                    """Receive Deepgram results in background"""
                    nonlocal interim_transcript
                    try:
                        async for msg in ws:
                            data = json.loads(msg)

                            # Check for final/interim results
                            if data.get("type") == "Results":
                                channel = data.get("channel", {})
                                alternatives = channel.get("alternatives", [])
                                if alternatives:
                                    text = alternatives[0].get("transcript", "")
                                    is_final = data.get("is_final", False)

                                    if is_final and text:
                                        final_transcript.append(text)
                                        print(f"[VOICE] Final: '{text}'")
                                    elif text:
                                        interim_transcript = text
                                        print(f"[VOICE] Interim: '{text}'")
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    except Exception as e:
                        print(f"[VOICE] Receive error: {e}")

                # Start receiving in background
                receive_task = asyncio.create_task(receive_results())

                # Stream audio to Deepgram + monitor silence
                try:
                    await self._stream_audio(process, ws)
                finally:
                    # Signal end of audio to Deepgram
                    try:
                        await ws.send(json.dumps({"type": "CloseStream"}))
                    except Exception:
                        pass

                    # Kill arecord
                    if process.returncode is None:
                        process.kill()
                        await process.wait()

                    # Wait briefly for final results
                    try:
                        await asyncio.wait_for(receive_task, timeout=2.0)
                    except asyncio.TimeoutError:
                        receive_task.cancel()
                        try:
                            await receive_task
                        except asyncio.CancelledError:
                            pass

                # Combine all final transcripts
                transcript = " ".join(final_transcript).strip()

                # If no final transcript but we have interim, use that
                if not transcript and interim_transcript:
                    transcript = interim_transcript.strip()

        except websockets.exceptions.InvalidStatusCode as e:
            print(f"[VOICE] Deepgram auth failed: {e}")
        except Exception as e:
            print(f"[VOICE] Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._recording = False

        print(f"[VOICE] Result: '{transcript}'")
        return transcript

    async def _stream_audio(self, process, ws):
        """Stream audio from arecord to Deepgram, stop on silence or timeout"""
        silence_samples = 0
        total_chunks = 0
        max_chunks = int(MAX_RECORD_SECONDS * 1000 / CHUNK_DURATION_MS)
        grace_chunks = int(INITIAL_GRACE_PERIOD * 1000 / CHUNK_DURATION_MS)
        silence_chunks_needed = int(SILENCE_DURATION * 1000 / CHUNK_DURATION_MS)
        no_speech_chunks = int(NO_SPEECH_TIMEOUT * 1000 / CHUNK_DURATION_MS)

        has_heard_speech = False

        while self._recording and total_chunks < max_chunks:
            try:
                # Read a chunk from arecord
                chunk = await asyncio.wait_for(
                    process.stdout.read(CHUNK_BYTES),
                    timeout=1.0
                )

                if not chunk:
                    print("[VOICE] arecord ended")
                    break

                # Send to Deepgram
                await ws.send(chunk)
                total_chunks += 1

                # Check RMS for silence detection (skip grace period)
                if total_chunks > grace_chunks:
                    rms = _compute_rms(chunk)

                    if rms > SILENCE_THRESHOLD:
                        has_heard_speech = True
                        silence_samples = 0
                    else:
                        silence_samples += 1

                    # Stop on silence after speech
                    if has_heard_speech and silence_samples >= silence_chunks_needed:
                        elapsed = total_chunks * CHUNK_DURATION_MS / 1000
                        print(f"[VOICE] Silence detected after {elapsed:.1f}s")
                        break

                    # Stop if no speech detected at all within timeout
                    if not has_heard_speech and total_chunks >= no_speech_chunks:
                        elapsed = total_chunks * CHUNK_DURATION_MS / 1000
                        print(f"[VOICE] No speech detected after {elapsed:.1f}s")
                        break

            except asyncio.TimeoutError:
                print("[VOICE] Read timeout")
                break
            except Exception as e:
                print(f"[VOICE] Stream error: {e}")
                break

        elapsed = total_chunks * CHUNK_DURATION_MS / 1000
        print(f"[VOICE] Recording stopped ({elapsed:.1f}s, {total_chunks} chunks)")

    def cancel(self):
        """Cancel current recording"""
        self._recording = False


# =============================================================================
# Test
# =============================================================================

async def demo():
    """Demo — record and transcribe on Pi"""
    import os

    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        # Try loading from .env
        env_path = "/home/pi/leeloo-ui/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPGRAM_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not api_key:
        print("Set DEEPGRAM_API_KEY env var or add to /home/pi/leeloo-ui/.env")
        return

    voice = VoiceManager(api_key)

    print("\n--- Voice Recording Demo ---")
    print("Speak now! (15s max, 1.5s silence to stop)\n")

    transcript = await voice.record_and_transcribe()

    print(f"\n=== Transcript: '{transcript}' ===")


if __name__ == "__main__":
    asyncio.run(demo())
