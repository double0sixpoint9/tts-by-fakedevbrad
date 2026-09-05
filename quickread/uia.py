"""Reading text straight out of the UI tree, for apps Ctrl+C cannot reach.

Some apps render text that is not a selectable control. Phone Link's message
list is the one that forced this module: its bubbles are XAML text blocks with
no selection and no copy handler, so Ctrl+C has nothing to act on. SendInput
still reports the keystroke as delivered, which makes the failure look like a
mystery rather than a missing feature.

UI Automation sidesteps the keyboard entirely -- it asks the application for its
text. That is slower than a clipboard copy and not available everywhere, so
selection.capture() only reaches for it once Ctrl+C has already come back
empty-handed.

Three sources are tried, in the order that best matches "read what I meant":

    1. a real UIA text selection, for apps that have one but no copy handler
    2. whatever is under the mouse pointer, which is where the user is looking
    3. the focused element, as a last resort

Import is deliberately lazy and failure is never fatal: comtypes may be absent,
COM may refuse to start, and the feature simply degrades to Ctrl+C alone.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

# Pattern and property ids from UIAutomationClient.h. Hard-coded rather than
# imported so a missing comtypes cannot break module import.
UIA_TEXT_PATTERN = 10014
UIA_VALUE_PATTERN = 10002
UIA_LEGACY_ACCESSIBLE_PATTERN = 10018

CONTROL_TYPE_EDIT = 50004
CONTROL_TYPE_TEXT = 50020
CONTROL_TYPE_DOCUMENT = 50030

TREE_SCOPE_CHILDREN = 0x2
TREE_SCOPE_DESCENDANTS = 0x4

# Longer than this and it is a whole window's worth of chrome, not a message.
MAX_GATHERED = 20000

_local = threading.local()

user32 = ctypes.WinDLL("user32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _automation():
    """The IUIAutomation for this thread, or None if UIA is unavailable.

    COM objects are apartment-bound, so this is cached per thread rather than
    globally -- the tray's worker thread and the window thread each get their own.
    """
    existing = getattr(_local, "automation", "missing")
    if existing != "missing":
        return existing

    automation = None
    try:
        import comtypes
        import comtypes.client

        # MTA, because the worker thread that calls this has no message pump and
        # an STA without one deadlocks on cross-apartment calls.
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialised in some apartment; use whatever it is

        module = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=module.IUIAutomation,
        )
    except Exception:  # noqa: BLE001 - no UIA is a degraded mode, not an error
        automation = None

    _local.automation = automation
    return automation


def _pattern(element, pattern_id, interface_name):
    """A control pattern off an element, or None if it does not support it."""
    try:
        import comtypes.client

        module = comtypes.client.GetModule("UIAutomationCore.dll")
        unknown = element.GetCurrentPattern(pattern_id)
        if not unknown:
            return None
        return unknown.QueryInterface(getattr(module, interface_name))
    except Exception:  # noqa: BLE001
        return None


def _own_text(element) -> str:
    """The text an element carries itself, ignoring its children."""
    for reader in (
        lambda: _pattern(element, UIA_VALUE_PATTERN, "IUIAutomationValuePattern")
        .CurrentValue,
        lambda: element.CurrentName,
    ):
        try:
            value = reader()
        except Exception:  # noqa: BLE001
            continue
        if value and value.strip():
            return value.strip()
    return ""


def _gather(element, depth: int = 0) -> str:
    """Concatenate the text of an element and everything inside it.

    Phone Link's bubbles nest the words one or two levels below the element the
    pointer actually lands on, so reading only the hit element returns nothing.
    """
    if element is None or depth > 4:
        return ""

    mine = _own_text(element)
    if depth == 0 and len(mine) > 1:
        # A leaf that already knows its own text needs no walking.
        try:
            control = element.CurrentControlType
        except Exception:  # noqa: BLE001
            control = None
        if control in (CONTROL_TYPE_TEXT, CONTROL_TYPE_EDIT, CONTROL_TYPE_DOCUMENT):
            return mine

    pieces = [mine] if mine else []
    try:
        automation = _automation()
        condition = automation.CreateTrueCondition()
        children = element.FindAll(TREE_SCOPE_CHILDREN, condition)
        for index in range(min(children.Length, 60)):
            piece = _gather(children.GetElement(index), depth + 1)
            if piece and piece not in pieces:
                pieces.append(piece)
    except Exception:  # noqa: BLE001
        pass

    return " ".join(pieces)[:MAX_GATHERED].strip()


def selected_text() -> str:
    """Text the focused control reports as selected, if it has a selection."""
    automation = _automation()
    if automation is None:
        return ""
    try:
        element = automation.GetFocusedElement()
        pattern = _pattern(element, UIA_TEXT_PATTERN, "IUIAutomationTextPattern")
        if pattern is None:
            return ""
        ranges = pattern.GetSelection()
        parts = []
        for index in range(ranges.Length):
            text = ranges.GetElement(index).GetText(-1)
            if text and text.strip():
                parts.append(text.strip())
        return " ".join(parts).strip()
    except Exception:  # noqa: BLE001
        return ""


def text_under_cursor() -> str:
    """Text of whatever the mouse is pointing at.

    This is the one that makes Phone Link work: there is no selection to read,
    but the pointer is sitting on the message the user wants read.
    """
    automation = _automation()
    if automation is None:
        return ""
    try:
        # ElementFromPoint wants the POINT the type library declares, not an
        # identically-shaped one of our own; ctypes rejects the impostor.
        import comtypes.client

        point = comtypes.client.GetModule("UIAutomationCore.dll").tagPOINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return ""
        element = automation.ElementFromPoint(point)
        return _gather(element)
    except Exception:  # noqa: BLE001
        return ""


def focused_text() -> str:
    """Text of the focused element, for keyboard-driven apps with no pointer."""
    automation = _automation()
    if automation is None:
        return ""
    try:
        return _gather(automation.GetFocusedElement())
    except Exception:  # noqa: BLE001
        return ""


def capture() -> tuple[str, str]:
    """Best available text and where it came from, or ("", "none")."""
    for source, reader in (
        ("uia-selection", selected_text),
        ("uia-pointer", text_under_cursor),
        ("uia-focus", focused_text),
    ):
        try:
            text = reader()
        except Exception:  # noqa: BLE001
            continue
        if text and text.strip():
            return text.strip(), source
    return "", "none"


def available() -> bool:
    return _automation() is not None
