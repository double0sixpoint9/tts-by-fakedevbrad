"""
Desktop launcher for TTS by fakedevbrad.

Double-clicking the shortcut runs this under pythonw.exe, so no console window
appears. It starts the local server, waits for the port, opens a chromeless app
window, and then gets out of the way. The server shuts itself down once the page
goes away (see --auto-exit in server.py).

    pythonw launch.pyw          normal launch
    python  launch.pyw --debug  keep the console and echo the server log
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
LOG = os.path.join(HERE, "launcher.log")

HOST = "127.0.0.1"
PORT = 5050
URL = f"http://{HOST}:{PORT}/"

PROFILE = os.path.join(
    os.environ.get("LOCALAPPDATA", HERE), "ttsbyfakedevbrad", "browser"
)

DEPENDENCIES = ("kokoro_onnx", "soundfile", "numpy")

# Windows: don't flash a console for any child process.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEBUG = "--debug" in sys.argv


def log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {message}"
    if DEBUG:
        print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def alert(message: str) -> None:
    """Fatal errors have nowhere to print under pythonw, so use a dialog."""
    log(f"ERROR: {message}")
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(
            None, message, "TTS by fakedevbrad", 0x10  # MB_ICONERROR
        )
    else:
        print(message, file=sys.stderr)


def python_exe() -> str:
    """The console interpreter matching whatever is running this script."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(console):
            return console
    return exe


def port_open(timeout: float = 0.4) -> bool:
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((HOST, PORT)) == 0


def ensure_dependencies(exe: str) -> bool:
    """Install missing packages once, rather than shelling out to pip every run."""
    probe = subprocess.run(
        [exe, "-c", "import " + ", ".join(DEPENDENCIES)],
        capture_output=True,
        creationflags=NO_WINDOW,
    )
    if probe.returncode == 0:
        return True

    log("dependencies missing, installing")
    splash = show_splash("Installing dependencies…\nThis happens only once.")
    try:
        result = subprocess.run(
            [exe, "-m", "pip", "install", "--quiet",
             "kokoro-onnx", "soundfile", "numpy", "huggingface_hub"],
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        )
    finally:
        close_splash(splash)

    if result.returncode != 0:
        alert(f"Couldn't install dependencies.\n\n{result.stderr[-600:]}")
        return False
    return True


def show_splash(message: str):
    """A tiny always-on-top window, so a long pip install isn't a silent hang."""
    try:
        import tkinter as tk
    except ImportError:
        return None
    try:
        root = tk.Tk()
        root.title("TTS by fakedevbrad")
        root.configure(bg="#150b28")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        tk.Label(
            root, text=message, bg="#150b28", fg="#ece8f5",
            font=("Segoe UI", 10), padx=34, pady=26, justify="center",
        ).pack()
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 2
        root.geometry(f"+{x}+{y}")
        root.update()
        return root
    except Exception:  # noqa: BLE001 - a splash is never worth failing over
        return None


def close_splash(splash) -> None:
    try:
        if splash is not None:
            splash.destroy()
    except Exception:  # noqa: BLE001
        pass


def start_server(exe: str) -> subprocess.Popen | None:
    handle = open(LOG, "a", encoding="utf-8", buffering=1)
    handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} starting server ===\n")
    # -u: without it the server's output sits in an 8 KB block buffer and never
    # reaches launcher.log, which makes a failed start look like total silence.
    process = subprocess.Popen(
        [exe, "-u", SERVER, "--host", HOST, "--port", str(PORT), "--auto-exit"],
        cwd=HERE,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )

    # The server binds before loading the model, so the port appears quickly.
    for _ in range(120):  # up to ~30s, generous for a cold disk
        if port_open():
            return process
        if process.poll() is not None:
            alert(f"The server exited during startup.\n\nSee {LOG}")
            return None
        time.sleep(0.25)

    alert(f"The server didn't start listening on port {PORT}.\n\nSee {LOG}")
    process.terminate()
    return None


def find_browser() -> str | None:
    """Edge and Chrome both support --app; Firefox has no equivalent."""
    program_files = [
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    relatives = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
    ]
    for base in program_files:
        for relative in relatives:
            candidate = os.path.join(base, relative)
            if base and os.path.exists(candidate):
                return candidate
    return None


def open_window() -> None:
    browser = find_browser()
    if not browser:
        log("no Chromium browser found; falling back to the default browser")
        import webbrowser

        webbrowser.open(URL)
        return

    os.makedirs(PROFILE, exist_ok=True)
    log(f"opening {os.path.basename(browser)} in app mode")
    subprocess.Popen(
        [
            browser,
            f"--app={URL}",
            f"--user-data-dir={PROFILE}",
            "--window-size=1180,860",
            "--no-first-run",
            "--no-default-browser-check",
            # Keeps Edge from parking itself in the tray after the last window
            # closes, so the profile isn't left running in the background.
            "--disable-background-mode",
            "--no-service-autorun",
            "--disable-features=Translate,AutofillServerCommunication,msStartupBoost",
        ],
        creationflags=NO_WINDOW,
    )


def main() -> None:
    exe = python_exe()
    log(f"launcher starting with {exe}")

    if port_open():
        log("server already listening; reusing it")
    else:
        if not ensure_dependencies(exe):
            return
        if start_server(exe) is None:
            return

    open_window()

    # The launcher deliberately does NOT wait on the browser process. When Edge
    # is already running, the msedge.exe we spawn just hands the URL to the
    # resident copy and exits about a second later -- watching that handle used
    # to read as "window closed" and killed the server while the real window was
    # still open, so every request failed with "Failed to fetch".
    #
    # The server owns its own lifetime instead: --auto-exit stops it on the
    # page's /bye beacon, or after the idle timeout if the browser dies hard.
    log("window opened; launcher done (server exits on its own)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never die silently under pythonw
        alert(f"Launcher failed:\n\n{exc}")
