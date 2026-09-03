"""
Talking to the local TTS server, and making sure there is one.

QuickRead deliberately adds nothing to server.py: POST /speak already turns text
into a WAV, which is the whole contract needed here. What this module owns is
the awkward part around it -- starting the server when it is not running, not
using it before Kokoro has finished loading, and keeping it alive while audio is
still playing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(HERE, "server.py")
LOG = os.path.join(HERE, "launcher.log")

HOST = "127.0.0.1"
PORT = 5050
BASE = f"http://{HOST}:{PORT}"

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# A cold start downloads nothing (the model is already on disk) but still has to
# load ~310 MB into memory, which is slow on a cold file cache.
BOOT_TIMEOUT = 120.0
SPEAK_TIMEOUT = 180.0


def python_exe() -> str:
    """The console interpreter matching whatever is running us.

    Ten lines duplicated from launch.pyw, which is a .pyw and therefore not
    importable. Duplicating beats restructuring a launcher that works.
    """
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(console):
            return console
    return exe


def is_up(timeout: float = 0.4) -> bool:
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((HOST, PORT)) == 0


def health(timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def voices() -> dict:
    """Voice id -> display name, straight from the server. Empty if it is down."""
    report = health()
    return (report or {}).get("voices") or {}


def _spawn() -> None:
    handle = open(LOG, "a", encoding="utf-8", buffering=1)
    handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} quickread starting server ===\n")
    subprocess.Popen(
        [python_exe(), "-u", SERVER, "--host", HOST, "--port", str(PORT), "--auto-exit"],
        cwd=HERE,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )


def ensure(on_progress=None) -> None:
    """Block until the server is up and Kokoro is loaded. Raise if it will not be.

    --auto-exit is passed deliberately: the server's own idle timeout then
    releases the model half an hour after the last read, and the next hotkey
    press pays to load it again. Better than holding ~700 MB all day for a
    feature used in bursts.
    """
    if not is_up():
        if on_progress:
            on_progress("Warming up Kokoro…")
        _spawn()

    deadline = time.monotonic() + BOOT_TIMEOUT
    announced = False

    while time.monotonic() < deadline:
        report = health()
        if report:
            phase = report.get("phase")
            if phase == "ready":
                return
            if phase == "error":
                raise RuntimeError(report.get("detail") or "The server failed to start.")
            if on_progress and not announced:
                announced = True
                on_progress(report.get("detail") or "Warming up…")
        time.sleep(0.4)

    raise RuntimeError(f"The server did not come up in {BOOT_TIMEOUT:.0f}s. See launcher.log.")


def speak(text: str, voice: str, speed: float) -> bytes:
    """POST /speak and return WAV bytes. Raises RuntimeError with the server's reason."""
    payload = json.dumps({"text": text, "voice": voice, "speed": speed}).encode()
    request = urllib.request.Request(
        f"{BASE}/speak", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=SPEAK_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = "Synthesis failed."
        try:
            detail = json.load(error).get("error") or detail
        except (ValueError, OSError):
            pass
        raise RuntimeError(detail) from error
    except (urllib.error.URLError, OSError) as error:
        raise RuntimeError(f"Lost the server: {error}") from error


class Keepalive:
    """Ping /health while audio is playing.

    The server shuts itself down ten seconds after the web UI's /bye beacon.
    Without this, closing the app window part way through a hotkey read would
    take the server down mid-sentence -- the two share one server, and only the
    window knows how to say goodbye.
    """

    def __init__(self, every: float = 5.0):
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            health(timeout=2.0)
