"""
QuickRead's settings, and the hotkey strings that live in them.

The window's voice and speed live in browser localStorage, which a Python
process cannot read, so QuickRead keeps its own. That is not a workaround being
apologised for: a quick read of a paragraph and a two-hour book are different
jobs, and wanting a different voice for each is reasonable.
"""

from __future__ import annotations

import copy
import json
import os

APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ttsbyfakedevbrad"
)
CONFIG_PATH = os.path.join(APP_DIR, "quickread.json")

DEFAULTS = {
    "voice": "af_heart",
    "speed": 1.0,
    "words_per_chunk": 110,
    "first_chunk_words": 35,
    # One-handed on purpose: Alt sits under the thumb and Z X C under the
    # fingers, so a read can be started, paused, and killed without looking
    # down or letting go of the mouse.
    "hotkeys": {
        "read": "alt+z",
        "pause": "alt+x",
        "stop": "alt+c",
    },
}

# Server's own clamp, mirrored so the tray cannot offer a speed /speak refuses.
SPEED_MIN = 0.5
SPEED_MAX = 2.0

# ── hotkeys ───────────────────────────────────────────────────────────────────

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Without NOREPEAT, holding the combo down fires a fresh read per key repeat.
MOD_NOREPEAT = 0x4000

MODIFIERS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

NAMED_KEYS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}


def _key_code(name: str) -> int | None:
    """Virtual-key code for a single key name, or None if it isn't one."""
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name.upper())
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if name.startswith("f") and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 24:
            return 0x6F + number  # VK_F1 is 0x70
    return None


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn "alt+z" into the (modifiers, virtual-key) RegisterHotKey wants.

    Raises ValueError on anything malformed, including a bare key with no
    modifier -- registering "r" globally would fire on every r typed anywhere.
    """
    parts = [part.strip().lower() for part in str(spec).split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError(f"Malformed hotkey: {spec!r}")

    *modifier_names, key_name = parts

    modifiers = 0
    for name in modifier_names:
        if name not in MODIFIERS:
            raise ValueError(f"Unknown modifier {name!r} in {spec!r}")
        modifiers |= MODIFIERS[name]

    if not modifiers:
        raise ValueError(f"{spec!r} needs at least one modifier")
    if key_name in MODIFIERS:
        raise ValueError(f"{spec!r} is only modifiers, with no key")

    code = _key_code(key_name)
    if code is None:
        raise ValueError(f"Unknown key {key_name!r} in {spec!r}")

    return modifiers | MOD_NOREPEAT, code


def label(spec: str) -> str:
    """Human-readable form of a hotkey string: "alt+z" -> "Alt+Z"."""
    parts = [part.strip() for part in str(spec).split("+") if part.strip()]
    return "+".join(part.upper() if len(part) == 1 else part.title() for part in parts)


# ── the file ──────────────────────────────────────────────────────────────────

def _number(value, fallback, low=None, high=None, cast=float):
    """Coerce a config value, falling back rather than raising on nonsense."""
    if isinstance(value, bool) or value is None:
        return fallback
    try:
        number = cast(value)
    except (TypeError, ValueError):
        return fallback
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def load(path: str | None = None) -> dict:
    """Read the settings file, filling in defaults for anything missing or bad.

    A corrupt file is never fatal here: QuickRead runs with no console and no
    window, so an exception at startup would be an invisible failure.
    """
    path = path or CONFIG_PATH
    config = copy.deepcopy(DEFAULTS)

    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return config

    if not isinstance(stored, dict):
        return config

    if isinstance(stored.get("voice"), str) and stored["voice"].strip():
        config["voice"] = stored["voice"].strip()

    config["speed"] = _number(
        stored.get("speed"), DEFAULTS["speed"], SPEED_MIN, SPEED_MAX, float
    )
    config["words_per_chunk"] = _number(
        stored.get("words_per_chunk"), DEFAULTS["words_per_chunk"], 10, 500, int
    )
    config["first_chunk_words"] = _number(
        stored.get("first_chunk_words"), DEFAULTS["first_chunk_words"], 5, 500, int
    )

    hotkeys = stored.get("hotkeys")
    if isinstance(hotkeys, dict):
        for action, combo in hotkeys.items():
            if action in config["hotkeys"] and isinstance(combo, str) and combo.strip():
                config["hotkeys"][action] = combo.strip()

    return config


def save(config: dict, path: str | None = None) -> None:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
