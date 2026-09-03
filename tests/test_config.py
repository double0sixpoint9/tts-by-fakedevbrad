"""Tests for QuickRead's settings file and hotkey parsing."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quickread.config import (  # noqa: E402
    DEFAULTS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    label,
    load,
    parse_hotkey,
    save,
)


class ParseHotkey(unittest.TestCase):
    def test_a_single_modifier_combo(self):
        self.assertEqual(parse_hotkey("alt+z"), (MOD_ALT | MOD_NOREPEAT, 0x5A))

    def test_a_multi_modifier_combo(self):
        self.assertEqual(
            parse_hotkey("ctrl+alt+r"),
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x52),
        )

    def test_every_shipped_default_parses_and_is_distinct(self):
        parsed = [parse_hotkey(combo) for combo in DEFAULTS["hotkeys"].values()]
        self.assertEqual(len(set(parsed)), len(parsed), "two defaults collide")

    def test_ignores_case_and_spacing(self):
        self.assertEqual(parse_hotkey("  Ctrl + Alt + R "), parse_hotkey("ctrl+alt+r"))

    def test_modifier_aliases(self):
        self.assertEqual(parse_hotkey("control+q")[0] & MOD_CONTROL, MOD_CONTROL)
        self.assertEqual(parse_hotkey("win+q")[0] & MOD_WIN, MOD_WIN)
        self.assertEqual(parse_hotkey("windows+q")[0] & MOD_WIN, MOD_WIN)
        self.assertEqual(parse_hotkey("super+q")[0] & MOD_WIN, MOD_WIN)
        self.assertEqual(parse_hotkey("shift+ctrl+q")[0] & MOD_SHIFT, MOD_SHIFT)

    def test_digits_and_function_keys(self):
        self.assertEqual(parse_hotkey("alt+1")[1], 0x31)
        self.assertEqual(parse_hotkey("ctrl+f9")[1], 0x78)
        self.assertEqual(parse_hotkey("ctrl+f12")[1], 0x7B)

    def test_named_keys(self):
        self.assertEqual(parse_hotkey("ctrl+space")[1], 0x20)
        self.assertEqual(parse_hotkey("ctrl+alt+pageup")[1], 0x21)

    def test_norepeat_is_always_set(self):
        # Without it, holding the combo fires a read per key repeat.
        self.assertTrue(parse_hotkey("ctrl+alt+r")[0] & MOD_NOREPEAT)

    def test_rejects_malformed(self):
        for bad in ["", "   ", "ctrl+", "+r", "ctrl+alt+notakey", "ctrl++r"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_hotkey(bad)

    def test_rejects_a_bare_key_with_no_modifier(self):
        # "r" would fire on every r typed anywhere on the machine.
        with self.assertRaises(ValueError):
            parse_hotkey("r")

    def test_rejects_modifiers_with_no_key(self):
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+shift")


class Label(unittest.TestCase):
    def test_reads_the_way_a_person_writes_it(self):
        self.assertEqual(label("ctrl+alt+r"), "Ctrl+Alt+R")

    def test_function_keys_stay_upper_case(self):
        self.assertEqual(label("ctrl+f9"), "Ctrl+F9")

    def test_survives_junk_without_raising(self):
        # It only ever decorates a balloon; it must not be the thing that breaks.
        self.assertEqual(label(""), "")


class Load(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "quickread.json")
        self.addCleanup(self.dir.cleanup)

    def write(self, payload):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load(self.path), DEFAULTS)

    def test_malformed_json_gives_defaults(self):
        self.write("{not json at all")
        self.assertEqual(load(self.path), DEFAULTS)

    def test_partial_file_is_merged_with_defaults(self):
        self.write({"voice": "bm_george"})
        config = load(self.path)
        self.assertEqual(config["voice"], "bm_george")
        self.assertEqual(config["words_per_chunk"], DEFAULTS["words_per_chunk"])
        self.assertEqual(config["hotkeys"], DEFAULTS["hotkeys"])

    def test_partial_hotkeys_keep_the_other_defaults(self):
        self.write({"hotkeys": {"stop": "ctrl+alt+z"}})
        hotkeys = load(self.path)["hotkeys"]
        self.assertEqual(hotkeys["stop"], "ctrl+alt+z")
        self.assertEqual(hotkeys["read"], DEFAULTS["hotkeys"]["read"])

    def test_speed_is_clamped_to_what_the_server_accepts(self):
        self.write({"speed": 9.0})
        self.assertEqual(load(self.path)["speed"], 2.0)
        self.write({"speed": 0.01})
        self.assertEqual(load(self.path)["speed"], 0.5)

    def test_nonsense_types_fall_back_rather_than_crash(self):
        self.write({"speed": "fast", "words_per_chunk": None, "hotkeys": "nope"})
        config = load(self.path)
        self.assertEqual(config["speed"], DEFAULTS["speed"])
        self.assertEqual(config["words_per_chunk"], DEFAULTS["words_per_chunk"])
        self.assertEqual(config["hotkeys"], DEFAULTS["hotkeys"])

    def test_save_then_load_round_trips(self):
        config = load(self.path)
        config["voice"] = "af_bella"
        config["speed"] = 1.4
        save(config, self.path)
        self.assertEqual(load(self.path)["voice"], "af_bella")
        self.assertAlmostEqual(load(self.path)["speed"], 1.4)

    def test_save_creates_the_directory(self):
        nested = os.path.join(self.dir.name, "deep", "quickread.json")
        save(DEFAULTS, nested)
        self.assertTrue(os.path.exists(nested))


if __name__ == "__main__":
    unittest.main()
