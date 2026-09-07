"""Tests for the server's phoneme batching.

Kokoro's voice arrays are (510, 1, 256) and its `_create_audio` indexes
`voice[len(tokens)]`, so a batch of exactly 510 phonemes reads one past the end.
The library's own batcher only breaks at `.,!?;`, so a long unpunctuated run --
a URL, a chat line, an OCR'd column, a heading -- never gets split and 500s.
These tests pin the batching that has to happen before Kokoro sees the text.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import PHONEME_LIMIT, phoneme_batches  # noqa: E402


class TestPhonemeBatches(unittest.TestCase):
    def test_nothing_to_say(self):
        for empty in ("", "   ", "\n\n"):
            self.assertEqual(phoneme_batches(empty), [])

    def test_short_text_is_one_batch(self):
        self.assertEqual(phoneme_batches("hɛlˈoʊ wˈɜːld."), ["hɛlˈoʊ wˈɜːld."])

    def test_never_exceeds_the_limit(self):
        """The failure in launcher.log: 700 phonemes with no punctuation at all."""
        run = ("sɪlˈæbəl " * 100).strip()
        batches = phoneme_batches(run)
        self.assertTrue(batches)
        for batch in batches:
            self.assertLessEqual(len(batch), PHONEME_LIMIT)

    def test_never_exceeds_the_limit_with_punctuation(self):
        clause = "ðɪs ɪz ɐ klˈɔːz, "
        batches = phoneme_batches(clause * 80)
        for batch in batches:
            self.assertLessEqual(len(batch), PHONEME_LIMIT)

    def test_a_single_word_longer_than_the_limit(self):
        """Pathological, but a 600-char token must not sail through."""
        batches = phoneme_batches("ɐ" * 600)
        self.assertTrue(batches)
        for batch in batches:
            self.assertLessEqual(len(batch), PHONEME_LIMIT)

    def test_no_empty_batches(self):
        """Kokoro's own splitter emits a leading '' on a long run; that is one
        wasted inference on nothing, and a needless concatenate of silence."""
        for text in (("sɪlˈæbəl " * 100).strip(), "wˈʌn. tˈuː,, θrˈiː!", "ɐ" * 600):
            for batch in phoneme_batches(text):
                self.assertTrue(batch.strip(), f"empty batch from {text[:20]!r}")

    def test_keeps_every_phoneme(self):
        """Batching may re-space, but must not drop or reorder sound."""
        text = "wˈʌn tˈuː θrˈiː. fˈɔːr fˈaɪv sˈɪks? sˈɛvən " * 20
        rejoined = "".join(phoneme_batches(text))
        self.assertEqual(rejoined.replace(" ", ""), text.replace(" ", ""))

    def test_prefers_breaking_at_punctuation(self):
        """A batch should end on a clause boundary when one is in reach."""
        text = ("wˈʌn tˈuː θrˈiː, " * 40).strip()
        batches = phoneme_batches(text)
        self.assertGreater(len(batches), 1)
        for batch in batches[:-1]:
            self.assertTrue(batch.rstrip().endswith(","), f"broke mid-clause: {batch[-20:]!r}")


if __name__ == "__main__":
    unittest.main()
