"""
TTS by fakedevbrad — local speech server.

Kokoro-ONNX synthesis plus a small static file server for the UI.
Everything stays on this machine; nothing is uploaded anywhere.

    python server.py [--port 5050] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
MODEL_PATH = os.path.join(SCRIPT_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(SCRIPT_DIR, "voices-v1.0.bin")

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_URL = f"{RELEASE}/kokoro-v1.0.onnx"
VOICES_URL = f"{RELEASE}/voices-v1.0.bin"

VOICES = {
    "af_heart": "Heart (US Female)",
    "af_bella": "Bella (US Female)",
    "af_sarah": "Sarah (US Female)",
    "af_nicole": "Nicole (US Female)",
    "am_adam": "Adam (US Male)",
    "am_michael": "Michael (US Male)",
    "bf_emma": "Emma (UK Female)",
    "bf_isabella": "Isabella (UK Female)",
    "bm_george": "George (UK Male)",
    "bm_lewis": "Lewis (UK Male)",
}

# Windows' registry regularly reports text/plain for .js, which makes browsers
# refuse ES modules outright. An explicit table avoids guessing.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".map": "application/json",
}

# Kokoro inference is not thread-safe, so synthesis is serialised. Static files
# and health checks still get served meanwhile, which is why this is a threading
# server: the old single-threaded one stalled the whole UI during generation.
SYNTH_LOCK = threading.Lock()

kokoro = None  # set by boot_model()

# The socket binds before the model loads, so the window can open instantly and
# show warm-up progress instead of a browser connection error.
STATUS = {"phase": "starting", "detail": "Starting up", "percent": None, "error": None}

# ── lifetime ──────────────────────────────────────────────────────────────────
# Waiting on the browser process to exit is unreliable on Windows: Edge's
# startup boost and background mode both keep msedge.exe alive after its last
# window closes, which would leak a server (and a loaded 310 MB model) per
# launch. So the server watches its own traffic instead.
#
#   * the page beacons /bye on pagehide, and we exit after a short grace period
#     unless another request arrives first (which is what a reload looks like)
#   * failing that, a long idle timeout catches a crashed or killed browser
#
# Only armed by --auto-exit, so running server.py by hand still runs forever.
LIFETIME = {"last_request": time.monotonic(), "bye_at": None}
LIFETIME_LOCK = threading.Lock()

BYE_GRACE = 10.0      # seconds to wait after /bye, in case it was a reload
IDLE_LIMIT = 1800.0   # 30 min with no traffic at all


# ── setup ─────────────────────────────────────────────────────────────────────

def require_deps() -> None:
    missing = [
        name
        for name, module in (("kokoro-onnx", "kokoro_onnx"), ("soundfile", "soundfile"), ("numpy", "numpy"))
        if not _importable(module)
    ]
    if missing:
        sys.exit(f"\n  Missing packages. Run:\n\n      pip install {' '.join(missing)}\n")


def _importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def download(url: str, dest: str, label: str) -> None:
    print(f"  Downloading {label}...")
    STATUS.update(phase="downloading", detail=f"Downloading {label}", percent=0)

    def progress(block, block_size, total):
        if total > 0:
            pct = min(100, block * block_size * 100 // total)
            STATUS["percent"] = pct
            print(f"\r    {pct}%   ", end="", flush=True)

    # Download beside the target, then move, so an interrupted run never leaves
    # a truncated model that looks complete on the next start.
    partial = dest + ".part"
    urllib.request.urlretrieve(url, partial, reporthook=progress)
    os.replace(partial, dest)
    print("\r    done.     ")


def boot_model() -> None:
    """Fetch and load the model. Runs on a background thread."""
    global kokoro
    try:
        if not os.path.exists(MODEL_PATH):
            download(MODEL_URL, MODEL_PATH, "Kokoro model (~310 MB)")
        if not os.path.exists(VOICES_PATH):
            download(VOICES_URL, VOICES_PATH, "voices (~20 MB)")

        print("  Loading model...")
        STATUS.update(phase="loading", detail="Warming up Kokoro", percent=None)
        from kokoro_onnx import Kokoro

        kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
        STATUS.update(phase="ready", detail="Kokoro ready", percent=None)
        print("  Model ready.\n")
    except Exception as exc:  # noqa: BLE001 - report any startup failure to the UI
        STATUS.update(phase="error", detail=str(exc), error=str(exc))
        print(f"\n  Startup failed: {exc}\n")


def to_wav(samples, rate: int) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


# ── batching ────────────────────────────────────────────────────────────
# Kokoro's context is 510 style vectors -- the voice arrays are (510, 1, 256) --
# and _create_audio indexes voice[len(tokens)], so a batch of exactly 510
# phonemes reads one past the end and raises. Its own batcher only breaks at
# .,!?; so a long unpunctuated run -- a URL, a chat line, an OCR'd column, a
# heading -- is handed over whole, truncated to exactly 510, and fails. Batching
# happens here instead, at the one point the window and QuickRead both go
# through, and 500 leaves room for the pad token at each end.

PHONEME_LIMIT = 500

CLAUSE_END = ".,!?;"


def _clauses(phonemes: str) -> list[str]:
    """Split at clause ends, keeping the punctuation on the piece it closed."""
    pieces: list[str] = []
    current = ""
    for char in phonemes:
        current += char
        if char in CLAUSE_END:
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    return pieces


def _fit(clause: str, limit: int) -> list[str]:
    """Break one over-long clause down, on word boundaries where it can."""
    if len(clause) <= limit:
        return [clause]

    pieces: list[str] = []
    current = ""
    for word in clause.split(" "):
        # A single token longer than the whole context is pathological -- one
        # unbroken 600-character "word" -- but it still must not reach Kokoro.
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        if not word:
            continue
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def phoneme_batches(phonemes: str, limit: int = PHONEME_LIMIT) -> list[str]:
    """Split phonemes into runs of at most `limit`, breaking as late as it can.

    Prefers a clause end, falls back to a word boundary, and only cuts mid-word
    when a single token is longer than the context. Never returns an empty
    batch, and returns [] when there is nothing to say -- which is not an error,
    just silence.
    """
    phonemes = phonemes.strip()
    if not phonemes:
        return []

    batches: list[str] = []
    current = ""
    for clause in _clauses(phonemes):
        for piece in _fit(clause, limit):
            if not current:
                current = piece
            elif len(current) + len(piece) <= limit:
                current += piece
            else:
                batches.append(current.strip())
                current = piece.lstrip()
    if current.strip():
        batches.append(current.strip())

    return [batch for batch in batches if batch]


def synthesize(text: str, voice: str, speed: float):
    """Text -> (samples, rate), or None when there is nothing pronounceable.

    An emoji row, a rule of dashes, a block of ASCII art: Kokoro phonemizes
    those to nothing, ends up concatenating an empty list, and raises "need at
    least one array to concatenate". That is silence being reported as a fault,
    and one such passage used to end the whole read.
    """
    import numpy as np

    batches = phoneme_batches(kokoro.tokenizer.phonemize(text, "en-us"))
    if not batches:
        return None

    parts = []
    rate = 24000
    for batch in batches:
        audio, rate = kokoro.create(batch, voice=voice, speed=speed, is_phonemes=True)
        parts.append(audio)
    return np.concatenate(parts), rate


# ── request handling ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "ttsbyfakedevbrad/2.0"

    def log_message(self, fmt, *args):  # quiet for normal traffic
        if args and str(args[1]) not in ("200", "204", "304"):
            super().log_message(fmt, *args)

    def _touch(self) -> float:
        """Record real traffic.

        Deliberately not done in handle_one_request: that runs again on a
        keep-alive connection while it blocks waiting for a request that may
        never come, which would stamp a time after /bye and cancel the
        shutdown for good.
        """
        now = time.monotonic()
        with LIFETIME_LOCK:
            LIFETIME["last_request"] = now
        return now

    # ── helpers ──

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _resolve(self, url_path: str) -> str | None:
        """Map a URL path to a file inside static/, refusing to escape it."""
        rel = url_path.lstrip("/") or "index.html"
        target = os.path.realpath(os.path.join(STATIC_DIR, rel))
        root = os.path.realpath(STATIC_DIR)
        if target != root and not target.startswith(root + os.sep):
            return None
        return target if os.path.isfile(target) else None

    # ── routes ──

    def do_GET(self):
        self._touch()
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json(200, {
                "ok": STATUS["phase"] == "ready",
                "phase": STATUS["phase"],
                "detail": STATUS["detail"],
                "percent": STATUS["percent"],
                "voices": VOICES,
            })
            return

        target = self._resolve(path)
        if target is None:
            self._send(404, b"Not found")
            return

        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as handle:
            body = handle.read()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        now = self._touch()
        path = urlparse(self.path).path

        if path == "/bye":
            # navigator.sendBeacon on pagehide. Arms the grace timer; a reload
            # cancels it by bumping last_request past bye_at.
            with LIFETIME_LOCK:
                LIFETIME["bye_at"] = now
            self._send(204)
            return

        if path != "/speak":
            self._send(404, b"Not found")
            return

        if kokoro is None:
            self._send_json(503, {"error": STATUS["error"] or "Still warming up — try again in a moment."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "Malformed request body."})
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._send_json(400, {"error": "Nothing to say."})
            return

        voice = payload.get("voice") if payload.get("voice") in VOICES else "af_heart"
        try:
            speed = min(2.0, max(0.5, float(payload.get("speed", 1.0))))
        except (TypeError, ValueError):
            speed = 1.0

        print(f"  {VOICES[voice]}  {speed:g}x  ({len(text)} chars)")

        try:
            with SYNTH_LOCK:
                result = synthesize(text, voice, speed)
        except Exception as exc:  # noqa: BLE001 - surface any synthesis failure
            print(f"  synthesis failed: {exc}")
            self._send_json(500, {"error": str(exc)})
            return

        # Distinct from a failure on purpose: the caller should move on to the
        # next passage, not stop reading.
        if result is None:
            print("  nothing pronounceable - skipped")
            self._send_json(400, {"error": "Nothing pronounceable in that passage."})
            return

        body = to_wav(*result)

        self._send(200, body, "audio/wav")


# ── lifetime watchdog ─────────────────────────────────────────────────────────

def watch_lifetime(server: ThreadingHTTPServer) -> None:
    """Shut the server down once the UI is demonstrably gone."""
    while True:
        time.sleep(2.0)
        now = time.monotonic()
        with LIFETIME_LOCK:
            last = LIFETIME["last_request"]
            bye = LIFETIME["bye_at"]

        said_goodbye = bye is not None and last <= bye and now - bye > BYE_GRACE
        gone_quiet = now - last > IDLE_LIMIT

        if said_goodbye or gone_quiet:
            # ASCII only: the Windows console is cp1252, not UTF-8.
            print("\n  UI closed - stopping.\n" if said_goodbye else "\n  Idle - stopping.\n")
            threading.Thread(target=server.shutdown, daemon=True).start()
            return


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Local Kokoro TTS server.")
    parser.add_argument("--port", type=int, default=5050)
    # Loopback only: this is a personal reader, not something the LAN needs.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--auto-exit",
        action="store_true",
        help="stop once the UI closes (used by the desktop launcher)",
    )
    args = parser.parse_args()

    print("\n  TTS by fakedevbrad - starting up\n")
    require_deps()

    # Bind first, load second: the UI can then open immediately and watch
    # /health rather than meeting a connection-refused page.
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=boot_model, daemon=True).start()
    if args.auto_exit:
        threading.Thread(target=watch_lifetime, args=(server,), daemon=True).start()

    print(f"  Listening on http://{args.host}:{args.port}")
    print("  Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
