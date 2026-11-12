#!/usr/bin/env python3
"""
LEELOO Voice Manager — Microphone recording + Deepgram Nova-2 STT

Records audio from the INMP441 I2S microphone via ALSA (arecord subprocess),
streams chunks to Deepgram's WebSocket API for real-time transcription.

Features:
- arecord starts immediately; the Deepgram WebSocket opens concurrently and
  audio captured meanwhile is buffered and sent in order once it is open
- Streaming STT via Deepgram Nova-2 (wss://api.deepgram.com/v1/listen)
- Local silence detection (RMS energy threshold) to stop recording
- 15-second max recording duration
- Returns final transcript string; `VoiceManager.last_error` says why it is
  empty (None = fine / no speech, "network", "auth", "mic")
- Full cleanup (arecord killed, socket closed) on every exit path,
  including task cancellation

Audio format:
- Device: plughw:0 (INMP441 via googlevoicehat-soundcard overlay)
- Format: S16_LE (16-bit signed little-endian)
- Rate: 16000 Hz
- Channels: 1 (mono)
"""

import asyncio
import inspect
import json
import math
import operator
import sys
from array import array

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WEBSOCKETS_AVAILABLE = False
    print("[VOICE] websockets not installed — voice disabled")

try:
    import numpy as _np
except Exception:  # numpy is optional
    _np = None


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
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE * CHANNELS

# Silence detection
SILENCE_THRESHOLD = 30        # RMS below this = silence (INMP441 is quiet: speech ~40-70, ambient ~3-5)
SILENCE_DURATION = 1.5        # Seconds of silence after speech before stopping
NO_SPEECH_TIMEOUT = 5.0       # Stop if no speech detected within this time
INITIAL_GRACE_PERIOD = 0.5    # Don't detect silence in first 0.5s

# Timeouts (seconds)
CONNECT_TIMEOUT = 5.0         # Deepgram WebSocket open
READ_TIMEOUT = 1.0            # One chunk from arecord
SEND_DRAIN_TIMEOUT = 3.0      # Flushing buffered audio after recording stops
FINAL_RESULT_WAIT = 2.0       # Waiting for last results after CloseStream
CLOSE_TIMEOUT = 2.0           # WebSocket close handshake / arecord exit

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

# last_error values
ERROR_NETWORK = "network"
ERROR_AUTH = "auth"
ERROR_MIC = "mic"

# HTTP statuses that mean "the key/account was rejected", not "network down"
_AUTH_STATUSES = (401, 402, 403)


# =============================================================================
# RMS
# =============================================================================

_RMS_DECIMATE = 4                 # fallback only (no numpy)
_RMS_DECIMATE_MIN_SAMPLES = 400


def _compute_rms_numpy(chunk_bytes):
    n_samples = len(chunk_bytes) // 2
    if n_samples == 0:
        return 0
    samples = _np.frombuffer(chunk_bytes, dtype="<i2", count=n_samples)
    samples = samples.astype(_np.float32)
    return math.sqrt(float(_np.dot(samples, samples)) / n_samples)


def _compute_rms_array(chunk_bytes):
    n_samples = len(chunk_bytes) // 2
    if n_samples == 0:
        return 0
    samples = array("h")
    samples.frombytes(bytes(chunk_bytes[:n_samples * 2]))
    if sys.byteorder == "big":
        samples.byteswap()
    # Pure-Python cost is per sample, so measure every 4th sample of a
    # full-size chunk: energy estimate is unchanged for silence detection
    if n_samples >= _RMS_DECIMATE_MIN_SAMPLES:
        samples = samples[::_RMS_DECIMATE]
    return math.sqrt(sum(map(operator.mul, samples, samples)) / len(samples))


def _compute_rms(chunk_bytes):
    """Compute RMS energy of a 16-bit little-endian PCM audio chunk"""
    try:
        if _np is not None:
            return _compute_rms_numpy(chunk_bytes)
        return _compute_rms_array(chunk_bytes)
    except Exception:
        return 0


# =============================================================================
# Seams (replaced by fakes in tests)
# =============================================================================

def _header_kwarg():
    """Name of the header argument of websockets.connect in this version.

    Legacy client (websockets <= 13 default): extra_headers
    New asyncio client (websockets >= 14 default): additional_headers
    """
    try:
        params = inspect.signature(websockets.connect).parameters
        if "extra_headers" in params:
            return "extra_headers"
    except (TypeError, ValueError):
        pass
    return "additional_headers"


async def _ws_connect(url, headers):
    """Open the Deepgram WebSocket (works with old and new websockets)."""
    kwargs = {
        "ping_interval": None,  # Deepgram handles keepalive
        "open_timeout": CONNECT_TIMEOUT,
        "close_timeout": CLOSE_TIMEOUT,
        _header_kwarg(): headers,
    }
    return await websockets.connect(url, **kwargs)


async def _start_arecord():
    """Start arecord writing raw PCM to stdout."""
    return await asyncio.create_subprocess_exec(
        "arecord",
        "-D", DEVICE,
        "-f", SAMPLE_FORMAT,
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-t", "raw",
        "-q",  # quiet
        "-",   # stdout
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def _classify_connect_error(exc):
    """Map a connect exception to "auth" or "network".

    New websockets: InvalidStatus with .response.status_code
    Legacy websockets: InvalidStatusCode with .status_code
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status in _AUTH_STATUSES:
        return ERROR_AUTH
    return ERROR_NETWORK


def _kill_process(process):
    """Kill arecord (synchronous, safe to call repeatedly)."""
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    except Exception as e:
        print(f"[VOICE] Could not kill arecord: {e}")


class _Session:
    """State shared between the capture loop and the Deepgram tasks."""

    def __init__(self):
        self.ws = None
        self.recv_task = None
        self.error = None          # None | "network" | "auth" | "mic"
        self.close_sent = False
        self.finals = []
        self.interim = ""


async def _read_with_timeout(stream, n, timeout):
    """Read up to n bytes or raise asyncio.TimeoutError.

    Uses asyncio.timeout() where it exists (Python 3.11+, the Pi). On
    Python <= 3.11, asyncio.wait_for() can swallow a task cancellation that
    arrives in the same loop step as the data, and the recording then runs
    on after it was cancelled."""
    if hasattr(asyncio, "timeout"):
        async with asyncio.timeout(timeout):
            return await stream.read(n)
    return await asyncio.wait_for(stream.read(n), timeout=timeout)


class VoiceManager:
    """Async voice recording + Deepgram streaming STT"""

    def __init__(self, deepgram_api_key):
        """
        Args:
            deepgram_api_key: Deepgram API key for authentication
        """
        self.api_key = deepgram_api_key
        self._recording = False
        # Why the last record_and_transcribe() returned "":
        # None (ok / no speech), "network", "auth" or "mic"
        self.last_error = None

    async def record_and_transcribe(self) -> str:
        """
        Record audio from microphone and transcribe via Deepgram.

        Returns:
            Transcript string, or empty string on failure/silence
            (see self.last_error for the reason).
        """
        self.last_error = None

        if not WEBSOCKETS_AVAILABLE:
            print("[VOICE] websockets not available")
            self.last_error = ERROR_NETWORK
            return ""

        if not self.api_key:
            print("[VOICE] No Deepgram API key configured")
            self.last_error = ERROR_AUTH
            return ""

        self._recording = True
        transcript = ""
        process = None
        sender_task = None
        session = _Session()
        queue = asyncio.Queue()

        try:
            # Microphone first: the user is already talking
            try:
                process = await _start_arecord()
            except Exception as e:
                print(f"[VOICE] Could not start arecord: {e}")
                self.last_error = ERROR_MIC
                return ""

            print(f"[VOICE] Recording... (max {MAX_RECORD_SECONDS}s)")

            # Deepgram connects in the background; audio is queued meanwhile
            sender_task = asyncio.create_task(self._deepgram_sender(queue, session))

            captured = await self._capture_audio(process, queue, session)

            # Free the microphone before waiting on the network
            _kill_process(process)

            if session.error is None and captured == 0 and self._recording:
                print("[VOICE] No audio from microphone")
                session.error = ERROR_MIC

            if session.error is None:
                # Flush buffered audio, then ask Deepgram for the last results
                queue.put_nowait(None)
                await asyncio.wait(
                    {sender_task}, timeout=CONNECT_TIMEOUT + SEND_DRAIN_TIMEOUT
                )
                if not sender_task.done():
                    print("[VOICE] Deepgram send stalled")
                    session.error = ERROR_NETWORK
                elif session.error is None and session.ws is not None:
                    await self._send_close_stream(session)
                    if session.recv_task is not None:
                        await asyncio.wait(
                            {session.recv_task}, timeout=FINAL_RESULT_WAIT
                        )

            # Combine all final transcripts
            transcript = " ".join(session.finals).strip()

            # If no final transcript but we have interim, use that
            if not transcript and session.interim:
                transcript = session.interim.strip()

            if not transcript:
                self.last_error = session.error

        except asyncio.CancelledError:
            print("[VOICE] Cancelled")
            raise
        except Exception as e:
            print(f"[VOICE] Error: {e}")
            import traceback
            traceback.print_exc()
            self.last_error = session.error or ERROR_NETWORK
        finally:
            self._recording = False
            await self._cleanup(process, sender_task, session)

        print(f"[VOICE] Result: '{transcript}'")
        return transcript

    async def _capture_audio(self, process, queue, session):
        """Read arecord chunks into the queue; stop on silence, timeout,
        cancel() or a Deepgram failure. Returns the number of bytes captured.

        All timing is on the audio timeline (bytes captured since arecord
        started), independent of when the Deepgram connection opens.
        """
        total_bytes = 0
        total_chunks = 0
        silence_seconds = 0.0
        has_heard_speech = False

        while self._recording:
            # Always yield: stdout.read() returns without suspending when
            # data is already buffered, which would starve the sender task
            await asyncio.sleep(0)

            if session.error is not None:
                print("[VOICE] Stopping recording (Deepgram unavailable)")
                break

            if total_bytes / BYTES_PER_SECOND >= MAX_RECORD_SECONDS:
                break

            try:
                chunk = await _read_with_timeout(process.stdout, CHUNK_BYTES, READ_TIMEOUT)
            except asyncio.TimeoutError:
                print("[VOICE] Read timeout")
                break
            except Exception as e:
                print(f"[VOICE] Stream error: {e}")
                break

            if not chunk:
                print("[VOICE] arecord ended")
                break

            queue.put_nowait(chunk)
            total_bytes += len(chunk)
            total_chunks += 1
            chunk_seconds = len(chunk) / BYTES_PER_SECOND
            elapsed = total_bytes / BYTES_PER_SECOND

            # Check RMS for silence detection (skip grace period)
            if elapsed > INITIAL_GRACE_PERIOD:
                if _compute_rms(chunk) > SILENCE_THRESHOLD:
                    has_heard_speech = True
                    silence_seconds = 0.0
                else:
                    silence_seconds += chunk_seconds

                # Stop on silence after speech (epsilon: float accumulation)
                if has_heard_speech and silence_seconds >= SILENCE_DURATION - 1e-6:
                    print(f"[VOICE] Silence detected after {elapsed:.1f}s")
                    break

                # Stop if no speech detected at all within timeout
                if not has_heard_speech and elapsed >= NO_SPEECH_TIMEOUT - 1e-6:
                    print(f"[VOICE] No speech detected after {elapsed:.1f}s")
                    break

        elapsed = total_bytes / BYTES_PER_SECOND
        print(f"[VOICE] Recording stopped ({elapsed:.1f}s, {total_chunks} chunks)")
        return total_bytes

    async def _deepgram_sender(self, queue, session):
        """Connect to Deepgram, then forward queued audio in order.

        Never raises (except CancelledError); failures go to session.error.
        A None item in the queue means "end of audio".
        """
        ws_url = f"{DEEPGRAM_WS_URL}?{DEEPGRAM_PARAMS}"
        headers = {"Authorization": f"Token {self.api_key}"}

        print("[VOICE] Connecting to Deepgram...")
        try:
            ws = await asyncio.wait_for(
                _ws_connect(ws_url, headers), timeout=CONNECT_TIMEOUT
            )
        except asyncio.TimeoutError:
            print(f"[VOICE] Deepgram connect timed out ({CONNECT_TIMEOUT:.0f}s)")
            session.error = ERROR_NETWORK
            return
        except Exception as e:
            session.error = _classify_connect_error(e)
            if session.error == ERROR_AUTH:
                print(f"[VOICE] Deepgram auth failed: {e}")
            else:
                print(f"[VOICE] Deepgram connect failed: {e}")
            return

        session.ws = ws
        session.recv_task = asyncio.create_task(self._receive_results(ws, session))
        print(f"[VOICE] Connected to Deepgram Nova-2 ({queue.qsize()} chunks buffered)")

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                await ws.send(chunk)
        except Exception as e:
            print(f"[VOICE] Deepgram send failed: {e}")
            session.error = ERROR_NETWORK

    async def _receive_results(self, ws, session):
        """Collect Deepgram results until the server closes the socket."""
        try:
            async for msg in ws:
                try:
                    data = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict) or data.get("type") != "Results":
                    continue

                channel = data.get("channel") or {}
                alternatives = channel.get("alternatives") or []
                if not alternatives:
                    continue
                text = alternatives[0].get("transcript", "")
                if not text:
                    continue

                if data.get("is_final", False):
                    session.finals.append(text)
                    print(f"[VOICE] Final: '{text}'")
                else:
                    session.interim = text
        except Exception as e:
            # ConnectionClosed is the normal end of a session
            closed = getattr(getattr(websockets, "exceptions", None),
                             "ConnectionClosed", ())
            if not (closed and isinstance(e, closed)):
                print(f"[VOICE] Receive error: {e}")

    async def _send_close_stream(self, session):
        """Tell Deepgram the audio is finished (best effort, once)."""
        if session.ws is None or session.close_sent:
            return
        session.close_sent = True
        try:
            await asyncio.wait_for(
                session.ws.send(json.dumps({"type": "CloseStream"})),
                timeout=CLOSE_TIMEOUT,
            )
        except Exception:
            pass

    async def _cleanup(self, process, sender_task, session):
        """Release everything. Runs on every exit path, including cancellation.

        Synchronous steps first, so the microphone is freed and helper tasks
        are stopped even if this coroutine is cancelled again while awaiting.
        """
        _kill_process(process)
        tasks = [t for t in (sender_task, session.recv_task) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()

        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # The sender may have started the receiver just before stopping
            recv_task = session.recv_task
            if recv_task is not None and recv_task not in tasks:
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)

            if session.ws is not None:
                await self._send_close_stream(session)
                try:
                    await asyncio.wait_for(
                        session.ws.close(), timeout=CLOSE_TIMEOUT + 1.0
                    )
                except Exception:
                    pass
        finally:
            if process is not None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=CLOSE_TIMEOUT)
                except Exception:
                    pass

    def cancel(self):
        """Cancel current recording (stops capture; whatever was heard so
        far is still transcribed and returned)"""
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

    print(f"\n=== Transcript: '{transcript}' (last_error={voice.last_error}) ===")


if __name__ == "__main__":
    asyncio.run(demo())
