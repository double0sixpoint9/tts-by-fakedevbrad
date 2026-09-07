"""Tests for how QuickRead reads the server's answer to /speak.

The distinction these pin down is the whole point: a passage the server can
make no sound for must not end the read, while a server that has actually
broken or gone away must.
"""

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quickread import session  # noqa: E402


def _http_error(code: str | int, body: bytes):
    return urllib.error.HTTPError(
        "http://127.0.0.1:5050/speak", code, "err", {}, io.BytesIO(body)
    )


class TestSpeak(unittest.TestCase):
    def _speak(self, error):
        with mock.patch.object(session.urllib.request, "urlopen", side_effect=error):
            return session.speak("hello", "af_heart", 1.0)

    def test_unpronounceable_passage_is_not_a_failure(self):
        with self.assertRaises(session.PassageRejected) as caught:
            self._speak(_http_error(400, b'{"error": "Nothing pronounceable in that passage."}'))
        self.assertIn("pronounceable", str(caught.exception))

    def test_synthesis_failure_stays_a_failure(self):
        with self.assertRaises(RuntimeError) as caught:
            self._speak(_http_error(500, b'{"error": "index 510 is out of bounds"}'))
        self.assertNotIsInstance(caught.exception, session.PassageRejected)

    def test_still_warming_up_is_not_a_failure_of_the_passage(self):
        """503 is the server saying "not yet", which must stop the read, not
        silently drop the passage and read on without it."""
        with self.assertRaises(RuntimeError) as caught:
            self._speak(_http_error(503, b'{"error": "Still warming up"}'))
        self.assertNotIsInstance(caught.exception, session.PassageRejected)

    def test_lost_server_stays_a_failure(self):
        with self.assertRaises(RuntimeError) as caught:
            self._speak(urllib.error.URLError("connection refused"))
        self.assertNotIsInstance(caught.exception, session.PassageRejected)
        self.assertIn("Lost the server", str(caught.exception))

    def test_a_rejection_is_still_a_runtime_error(self):
        """Callers that only care that it failed keep working unchanged."""
        self.assertTrue(issubclass(session.PassageRejected, RuntimeError))


if __name__ == "__main__":
    unittest.main()
