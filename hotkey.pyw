"""
QuickRead — select text anywhere, press Alt+Z, hear it.

Runs under pythonw.exe: no console, no taskbar button, just a tray icon. It owns
a hidden window because that is what Windows requires of anything that wants
global hotkeys or a notification icon; both arrive as messages to it.

    pythonw hotkey.pyw          normal, silent
    python  hotkey.pyw          same, but exceptions reach the console

Nothing here touches server.py, launch.pyw, or the web UI. Deleting hotkey.pyw
and quickread/ removes the feature completely.
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from quickread import selection, session, tray as tray_module  # noqa: E402
from quickread.config import CONFIG_PATH, label, load, parse_hotkey, save  # noqa: E402
from quickread.speech import IDLE, PAUSED, SPEAKING, WARMING, Speaker  # noqa: E402

APP_NAME = "TTS QuickRead"
ICON = os.path.join(HERE, "icon.ico")
LAUNCHER = os.path.join(HERE, "launch.pyw")
MUTEX_NAME = "Local\\ttsbyfakedevbrad.quickread"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_HOTKEY = 0x0312
WM_APP = 0x8000
TRAY_MESSAGE = WM_APP + 1
NOTICE_MESSAGE = WM_APP + 2

WS_EX_TOOLWINDOW = 0x00000080
ERROR_ALREADY_EXISTS = 183
TICK_MS = 1000

HOTKEY_IDS = {"read": 1, "pause": 2, "stop": 3}

CMD_PAUSE, CMD_STOP, CMD_CLIPBOARD, CMD_WINDOW, CMD_SETTINGS, CMD_QUIT = 1, 2, 3, 4, 5, 6
CMD_VOICE_BASE, CMD_SPEED_BASE = 100, 200

SPEEDS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32.DefWindowProcW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.PostMessageW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
user32.RegisterHotKey.argtypes = (
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
)
user32.SetTimer.argtypes = (
    wintypes.HWND, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p
)
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Module-level, all of it: a WNDPROC that gets garbage collected leaves Windows
# calling into freed memory, and the mutex handle must outlive startup for the
# single-instance check to mean anything.
CONFIG = load()
HWND = None
TRAY = None
SPEAKER = None
WNDPROC_REF = None
MUTEX = None
NOTICES: queue.Queue = queue.Queue()
WORK: queue.Queue = queue.Queue()
LAST_TIP = ""


# ── talking to the user ───────────────────────────────────────────────────────

def notify(title: str, message: str, warning: bool = False) -> None:
    """Queue a balloon. Safe from any thread; the window thread shows it."""
    NOTICES.put((title, message, warning))
    if HWND:
        user32.PostMessageW(HWND, NOTICE_MESSAGE, 0, 0)


def drain_notices() -> None:
    while True:
        try:
            title, message, warning = NOTICES.get_nowait()
        except queue.Empty:
            return
        if TRAY:
            TRAY.balloon(title, message, warning)


# ── the work the hotkeys ask for ──────────────────────────────────────────────

def read_selection(clipboard_only: bool = False) -> None:
    if clipboard_only:
        text, source = selection.get_text(), "clipboard"
    else:
        text, source = selection.capture()

    if not text or not text.strip():
        notify(
            "Nothing to read",
            "There is no text on the clipboard."
            if clipboard_only
            else f"Select some text first, then press {label(CONFIG['hotkeys']['read'])}.",
        )
        return

    if source == "clipboard" and not clipboard_only:
        notify(
            "Read the clipboard instead",
            "Nothing came back from that window, so I read what was already copied.",
        )

    SPEAKER.speak(text)


def open_app_window() -> None:
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    subprocess.Popen(
        [pythonw if os.path.exists(pythonw) else exe, LAUNCHER],
        cwd=HERE,
        creationflags=session.NO_WINDOW,
    )


def open_settings() -> None:
    save(CONFIG)  # make sure there is a file to open
    os.startfile(CONFIG_PATH)  # noqa: S606 - opening our own JSON in the default editor


def worker() -> None:
    """One job at a time, off the window thread.

    A cold model load blocks for ten seconds or so. On the window thread that
    would freeze the tray and every other app's shell menu with it.
    """
    while True:
        job = WORK.get()
        if job is None:
            return
        try:
            job()
        except Exception as exc:  # noqa: BLE001 - a bad read must not kill the helper
            notify(APP_NAME, f"That didn't work: {exc}", warning=True)


# ── the menu ──────────────────────────────────────────────────────────────────

def voices() -> dict:
    """The server's voice table. Imported rather than copied, so it cannot drift."""
    try:
        from server import VOICES

        return VOICES
    except Exception:  # noqa: BLE001 - fall back to asking a running server
        return session.voices()


def menu_items():
    state, done, total = SPEAKER.status()
    speaking = state in (SPEAKING, PAUSED, WARMING)

    if state == SPEAKING:
        heading = f"Speaking — passage {done} of {total}"
    elif state == PAUSED:
        heading = f"Paused — passage {done} of {total}"
    elif state == WARMING:
        heading = "Warming up…"
    else:
        heading = f"Idle — {label(CONFIG['hotkeys']['read'])} reads your selection"

    item, separator, submenu = (
        tray_module.item, tray_module.separator, tray_module.submenu
    )

    voice_items = [
        item(CMD_VOICE_BASE + index, name, checked=(key == CONFIG["voice"]))
        for index, (key, name) in enumerate(sorted(voices().items()))
    ] or [item(0, "Available once the server has run", enabled=False)]

    speed_items = [
        item(
            CMD_SPEED_BASE + index,
            f"{value:g}×",
            checked=abs(value - CONFIG["speed"]) < 0.01,
        )
        for index, value in enumerate(SPEEDS)
    ]

    return [
        item(0, heading, enabled=False),
        separator(),
        item(
            CMD_PAUSE,
            f"Resume\t{label(CONFIG['hotkeys']['pause'])}"
            if state == PAUSED
            else f"Pause\t{label(CONFIG['hotkeys']['pause'])}",
            enabled=speaking,
        ),
        item(CMD_STOP, f"Stop\t{label(CONFIG['hotkeys']['stop'])}", enabled=speaking),
        separator(),
        submenu("Voice", voice_items),
        submenu("Speed", speed_items),
        separator(),
        item(CMD_CLIPBOARD, "Read the clipboard"),
        item(CMD_WINDOW, "Open the app window"),
        item(CMD_SETTINGS, "Edit settings…"),
        separator(),
        item(CMD_QUIT, "Quit"),
    ]


def on_command(command: int) -> None:
    if command == CMD_PAUSE:
        SPEAKER.pause_or_resume()
    elif command == CMD_STOP:
        SPEAKER.stop()
    elif command == CMD_CLIPBOARD:
        WORK.put(lambda: read_selection(clipboard_only=True))
    elif command == CMD_WINDOW:
        WORK.put(open_app_window)
    elif command == CMD_SETTINGS:
        WORK.put(open_settings)
    elif command == CMD_QUIT:
        shutdown()
    elif CMD_VOICE_BASE <= command < CMD_VOICE_BASE + 100:
        keys = [key for key, _ in sorted(voices().items())]
        index = command - CMD_VOICE_BASE
        if index < len(keys):
            CONFIG["voice"] = keys[index]
            save(CONFIG)
    elif CMD_SPEED_BASE <= command < CMD_SPEED_BASE + 100:
        index = command - CMD_SPEED_BASE
        if index < len(SPEEDS):
            CONFIG["speed"] = SPEEDS[index]
            save(CONFIG)


# ── the window ────────────────────────────────────────────────────────────────

def tick() -> None:
    """Refresh the tooltip. Polled rather than pushed, so the audio callback
    never has to make a Win32 call from a realtime thread."""
    global LAST_TIP
    state, done, total = SPEAKER.status()
    if state == SPEAKING:
        tip = f"{APP_NAME} — speaking, passage {done} of {total}"
    elif state == PAUSED:
        tip = f"{APP_NAME} — paused at passage {done} of {total}"
    elif state == WARMING:
        tip = f"{APP_NAME} — warming up"
    else:
        tip = f"{APP_NAME} — idle"

    if tip != LAST_TIP and TRAY:
        LAST_TIP = tip
        TRAY.tooltip(tip)


def wndproc(hwnd, message, wparam, lparam):
    if message == WM_HOTKEY:
        if wparam == HOTKEY_IDS["read"]:
            WORK.put(read_selection)
        elif wparam == HOTKEY_IDS["pause"]:
            SPEAKER.pause_or_resume()
        elif wparam == HOTKEY_IDS["stop"]:
            SPEAKER.stop()
        return 0

    if message == TRAY_MESSAGE:
        if (lparam & 0xFFFF) in (tray_module.WM_RBUTTONUP, tray_module.WM_LBUTTONUP):
            on_command(TRAY.popup(menu_items()))
        return 0

    if message == NOTICE_MESSAGE:
        drain_notices()
        return 0

    if message == WM_TIMER:
        tick()
        return 0

    if message == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0

    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def create_window() -> int:
    global WNDPROC_REF
    WNDPROC_REF = WNDPROC(wndproc)

    instance = kernel32.GetModuleHandleW(None)
    window_class = WNDCLASS()
    window_class.lpfnWndProc = WNDPROC_REF
    window_class.hInstance = instance
    window_class.lpszClassName = "TtsQuickReadWindow"
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        raise OSError(f"RegisterClassW failed: {ctypes.get_last_error()}")

    # A real top-level window that is simply never shown, rather than a
    # message-only one: TrackPopupMenu needs an owner that can take the
    # foreground, and message-only windows cannot.
    hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW, window_class.lpszClassName, APP_NAME,
        0, 0, 0, 0, 0, None, None, instance, None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
    return hwnd


def register_hotkeys() -> list[str]:
    """Returns the combos Windows refused, usually because something owns them."""
    failed = []
    for action, identifier in HOTKEY_IDS.items():
        combo = CONFIG["hotkeys"][action]
        try:
            modifiers, key = parse_hotkey(combo)
        except ValueError as exc:
            failed.append(f"{combo} ({exc})")
            continue
        if not user32.RegisterHotKey(HWND, identifier, modifiers, key):
            failed.append(label(combo))
    return failed


def shutdown() -> None:
    if SPEAKER:
        SPEAKER.stop()
    for identifier in HOTKEY_IDS.values():
        user32.UnregisterHotKey(HWND, identifier)
    if TRAY:
        TRAY.remove()
    user32.PostQuitMessage(0)


def single_instance() -> bool:
    global MUTEX
    MUTEX = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def main() -> None:
    global HWND, TRAY, SPEAKER

    if not single_instance():
        return  # already running; the tray icon is the one that matters

    if not os.path.exists(CONFIG_PATH):
        save(CONFIG)

    SPEAKER = Speaker(settings=lambda: CONFIG, on_notice=notify)
    HWND = create_window()
    TRAY = tray_module.Tray(HWND, TRAY_MESSAGE, ICON, f"{APP_NAME} — idle")

    threading.Thread(target=worker, daemon=True).start()
    user32.SetTimer(HWND, 1, TICK_MS, None)

    refused = register_hotkeys()
    if refused:
        notify(
            "Some hotkeys are taken",
            "Another app already owns " + ", ".join(refused)
            + ". Pick different ones in the tray menu's settings file.",
            warning=True,
        )
    else:
        notify(
            APP_NAME,
            f"Ready. Select text anywhere and press {label(CONFIG['hotkeys']['read'])}.",
        )

    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))

    if TRAY:
        TRAY.remove()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never die silently under pythonw
        ctypes.windll.user32.MessageBoxW(None, f"QuickRead failed:\n\n{exc}", APP_NAME, 0x10)
        raise
