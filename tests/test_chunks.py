"""
Tests for the QuickRead chunker.

This is the one piece of QuickRead where a silent bug corrupts every read, so
it is the one piece with real tests. The Win32 surface (hotkeys, clipboard,
tray, audio) is covered by the manual checklist in the design doc instead.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quickread.chunks import normalize, split  # noqa: E402


def words(text):
    return text.split()


class Normalize(unittest.TestCase):
    def test_collapses_horizontal_whitespace_but_keeps_lines(self):
        self.assertEqual(normalize("a   b\n\nc"), "a b\n\nc")

    def test_windows_line_endings(self):
        self.assertEqual(normalize("a\r\nb"), "a\nb")

    def test_caps_blank_lines_at_one(self):
        self.assertEqual(normalize("a\n\n\n\n\nb"), "a\n\nb")

    def test_strips_indentation_around_newlines(self):
        self.assertEqual(normalize("a  \n   b"), "a\nb")


class Empty(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(split(""), [])

    def test_only_whitespace(self):
        self.assertEqual(split("   \n\n  \t "), [])


class Boundaries(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(split("Hello there."), ["Hello there."])

    def test_does_not_break_on_an_abbreviation(self):
        text = "Dr. Smith went home. He slept."
        for chunk in split(text, words=3, first_words=3):
            self.assertNotEqual(chunk, "Dr.")
            self.assertFalse(chunk.endswith("Dr."))

    def test_does_not_break_on_single_letter_initials(self):
        text = "J. R. R. Tolkien wrote it. Then he rested."
        self.assertNotIn("J.", [c.strip() for c in split(text, words=2, first_words=2)])

    def test_keeps_a_closing_quote_with_its_sentence(self):
        chunks = split('He said "stop!" Then he left.', words=2, first_words=2)
        self.assertTrue(chunks[0].endswith('"'), chunks)

    def test_paragraph_break_forces_a_boundary(self):
        chunks = split("First line.\n\nSecond line.", words=500, first_words=500)
        self.assertEqual(chunks, ["First line.", "Second line."])

    def test_breaks_between_sentences_not_inside_them(self):
        text = " ".join(f"Sentence number {n} here." for n in range(1, 21))
        for chunk in split(text, words=8, first_words=8):
            self.assertTrue(chunk.endswith("."), chunk)


class Sizing(unittest.TestCase):
    def test_first_chunk_is_shorter_so_audio_starts_sooner(self):
        text = " ".join(f"Word{n} is a filler word here." for n in range(1, 61))
        chunks = split(text, words=110, first_words=20)
        self.assertGreater(len(chunks), 1)
        self.assertLess(len(words(chunks[0])), len(words(chunks[1])))

    def test_run_on_text_with_no_punctuation_still_splits(self):
        text = " ".join(f"word{n}" for n in range(300))
        chunks = split(text, words=50, first_words=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(words(chunk)), 80, chunk)

    def test_chunks_stay_near_the_target(self):
        text = " ".join(f"Sentence number {n} goes right here now." for n in range(1, 81))
        for chunk in split(text, words=40, first_words=40)[:-1]:
            self.assertGreaterEqual(len(words(chunk)), 20, chunk)


class Preservation(unittest.TestCase):
    def test_every_word_survives_in_order(self):
        text = (
            "Dr. Smith went home.\n\n"
            'He said "stop!" Then he left the building.\n'
            "J. R. R. Tolkien wrote it, apparently. The end."
        )
        chunks = split(text, words=6, first_words=4)
        self.assertEqual(words(" ".join(chunks)), words(normalize(text)))

    def test_no_empty_chunks(self):
        text = "One.\n\n\n\nTwo.\n\n   \n\nThree."
        self.assertTrue(all(c.strip() for c in split(text)))


if __name__ == "__main__":
    unittest.main()
