"""
The tray icon: Shell_NotifyIcon and a popup menu, through ctypes.

No pystray, and specifically no Pillow. make_icon.py exists precisely so this
project can draw its own icon out of the standard library, and pulling in a
30 MB imaging dependency to put that icon in the tray would undo the point.

This module stays policy-free: it knows how to show an icon, a tooltip, a
balloon, and a menu built from plain tuples. What goes in the menu is decided by
hotkey.pyw, which is the part that knows what is playing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

MF_STRING, MF_GRAYED, MF_SEPARATOR, MF_CHECKED, MF_POPUP = 0x0, 0x1, 0x800, 0x8, 0x10
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080

WM_NULL = 0x0000
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202

# Windows truncates these itself, but silently; better to know the limits.
TIP_MAX, INFO_MAX, TITLE_MAX = 127, 255, 63


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


user32.LoadImageW.argtypes = (
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = (
    wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
)
user32.TrackPopupMenu.argtypes = (
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
)
user32.TrackPopupMenu.restype = ctypes.c_int
shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA))
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


def separator():
    return ("sep",)


def item(command: int, text: str, checked: bool = False, enabled: bool = True):
    return ("item", command, text, checked, enabled)


def submenu(text: str, items):
    return ("menu", text, items)


class Tray:
    """One icon in the notification area, for the life of the process."""

    def __init__(self, hwnd: int, callback_message: int, icon_path: str, tip: str):
        self._hwnd = hwnd
        self._data = NOTIFYICONDATA()
        self._data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        self._data.hWnd = hwnd
        self._data.uID = 1
        self._data.uCallbackMessage = callback_message
        self._data.hIcon = user32.LoadImageW(
            None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        self._data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._data.szTip = tip[:TIP_MAX]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data))

    def tooltip(self, text: str) -> None:
        self._data.uFlags = NIF_TIP
        self._data.szTip = text[:TIP_MAX]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data))

    def balloon(self, title: str, message: str, warning: bool = False) -> None:
        self._data.uFlags = NIF_INFO
        self._data.szInfoTitle = title[:TITLE_MAX]
        self._data.szInfo = message[:INFO_MAX]
        self._data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data))

    def remove(self) -> None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data))

    # ── menu ──

    def _build(self, items) -> int:
        menu = user32.CreatePopupMenu()
        for entry in items:
            if entry[0] == "sep":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            elif entry[0] == "menu":
                _, text, children = entry
                user32.AppendMenuW(menu, MF_POPUP, self._build(children), text)
            else:
                _, command, text, checked, enabled = entry
                flags = MF_STRING
                if checked:
                    flags |= MF_CHECKED
                if not enabled:
                    flags |= MF_GRAYED
                user32.AppendMenuW(menu, flags, command, text)
        return menu

    def popup(self, items) -> int:
        """Show the menu at the cursor and return the chosen command id, or 0.

        TPM_RETURNCMD hands the choice straight back instead of posting
        WM_COMMAND, which keeps the whole menu round trip in one place.
        """
        menu = self._build(items)
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))

        # Required, and long documented: without it the menu refuses to close
        # when you click elsewhere, because the owning window is not foreground.
        user32.SetForegroundWindow(self._hwnd)
        chosen = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            point.x, point.y, 0, self._hwnd, None,
        )
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        return chosen
