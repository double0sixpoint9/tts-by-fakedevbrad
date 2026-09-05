"""
Capturing whatever text is selected, anywhere on the machine.

Windows has no API that asks the foreground app "what is selected right now" and
gets a useful answer everywhere. UI Automation manages it in some apps and not
others. So this does what every read-aloud utility does: sends Ctrl+C, reads the
clipboard, and puts the clipboard back. It works wherever Ctrl+C works, which is
effectively everywhere -- Obsidian, Discord, browsers, Word, PDF readers,
Notepad, editors.

Two details make it reliable rather than flaky:

  * The hotkey fires while its own modifiers are physically held down. With the
    default Alt+Z, sending Ctrl+C at that moment lands in the target app as
    Alt+Ctrl+C, which usually does nothing. So the held modifiers are released
    first, whatever the user has bound.

  * Whether the copy worked is decided by GetClipboardSequenceNumber, not by
    comparing text. Re-copying the same paragraph twice still counts as a copy,
    and an app that silently ignores Ctrl+C is correctly detected as "nothing
    happened" instead of appearing to return stale text.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from . import uia

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_CONTROL = 0x11
VK_C = 0x43
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

# Everything except Ctrl, which we want held for the copy we are about to send.
HELD_MODIFIERS = (VK_LMENU, VK_RMENU, VK_LSHIFT, VK_RSHIFT, VK_LWIN, VK_RWIN)

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.restype = wintypes.HGLOBAL


# ── keyboard ──────────────────────────────────────────────────────────────────

def _send(events) -> None:
    """events: [(virtual_key, is_key_up), ...] sent as one atomic batch."""
    batch = (INPUT * len(events))()
    for index, (key, is_up) in enumerate(events):
        batch[index].type = INPUT_KEYBOARD
        batch[index].ki = KEYBDINPUT(
            wVk=key, wScan=0,
            dwFlags=KEYEVENTF_KEYUP if is_up else 0,
            time=0, dwExtraInfo=0,
        )
    user32.SendInput(len(batch), batch, ctypes.sizeof(INPUT))


def _release_held_modifiers() -> None:
    down = [key for key in HELD_MODIFIERS if user32.GetAsyncKeyState(key) & 0x8000]
    if down:
        _send([(key, True) for key in down])
        time.sleep(0.03)


def _send_copy() -> None:
    _send([(VK_CONTROL, False), (VK_C, False), (VK_C, True), (VK_CONTROL, True)])


# ── clipboard ─────────────────────────────────────────────────────────────────

def _open(retries: int = 12, delay: float = 0.02) -> bool:
    """The clipboard is a single global lock; whoever we interrupted may hold it."""
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_text() -> str | None:
    """The clipboard's text, or None if it holds something else (or nothing)."""
    if not _open():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> bool:
    if not _open():
        return False
    try:
        user32.EmptyClipboard()
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, buffer, size)
        kernel32.GlobalUnlock(handle)
        # On success the clipboard owns the handle and must not be freed here.
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ── the thing this module exists for ──────────────────────────────────────────

def capture(settle: float = 0.45) -> tuple[str | None, str]:
    """Return (text, source): where the words came from, in order of preference.

    "selection" means Ctrl+C worked, which is the common case. A "uia-" source
    means it did not and the text was read out of the UI tree instead -- some
    apps, Phone Link's message list among them, draw text that no copy handler
    can reach, and SendInput cheerfully reports success while nothing happens.
    "clipboard" is the last resort: both failed, so whatever was already copied
    gets read. That also covers elevated windows -- Task Manager, regedit --
    which Windows forbids an ordinary process from sending input to.
    """
    previous = get_text()
    before = user32.GetClipboardSequenceNumber()

    _release_held_modifiers()
    _send_copy()

    copied = None
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        if user32.GetClipboardSequenceNumber() != before:
            copied = get_text()
            break
        time.sleep(0.02)

    # Put the user's clipboard back. Text only: if it held an image or files we
    # cannot restore it, and the copied text stays instead. Preserving every
    # format is far more machinery than this feature earns.
    if copied is not None and previous is not None:
        set_text(previous)

    if copied and copied.strip():
        return copied, "selection"

    # Nothing to copy. Ask the app for its text before giving up and reading
    # back whatever happened to be on the clipboard, which is rarely what the
    # user meant and is the reason this used to feel like it worked at random.
    spoken, source = uia.capture()
    if spoken:
        return spoken, source

    if previous and previous.strip():
        return previous, "clipboard"
    return None, "empty"
