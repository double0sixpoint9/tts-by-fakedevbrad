"""
The playback engine: text in, speech out, no window.

Same shape as static/js/player.js, for the same reasons. Synthesis of the next
passage overlaps playback of the current one, so you wait only for the first
passage rather than the whole selection. And a generation counter invalidates
audio belonging to a read that has since been replaced -- the headless twin of
the seek invalidation the web player does.

Playback is one sounddevice OutputStream with a callback rather than a series of
blocking plays. That buys two things worth having: no gap between passages, and
a pause that resumes on the same word, because the position lives in an index
the callback owns rather than in whatever a blocking call was part way through.
"""

from __future__ import annotations

import io
import queue
import threading

import sounddevice as sd
import soundfile as sf

from . import session
from .chunks import split

IDLE = "idle"
WARMING = "warming"
SPEAKING = "speaking"
PAUSED = "paused"

# Past this, say how much was picked up before starting: a stray Ctrl+A somewhere
# should not silently commit you to forty minutes of audio.
LONG_READ_WORDS = 4000


class Speaker:
    """Owns everything audible. One instance, driven by the hotkeys."""

    def __init__(self, settings, on_notice=None):
        self._settings = settings                      # callable -> config dict
        self._notice = on_notice or (lambda *_: None)

        self._lock = threading.RLock()
        self._generation = 0
        self._audio: queue.Queue = queue.Queue(maxsize=2)
        self._stream: sd.OutputStream | None = None
        self._current = None
        self._offset = 0
        self._synth_done = True
        self._state = IDLE
        self._done = 0
        self._total = 0
        self._keepalive = session.Keepalive()

    # ── what the tray asks ──

    def status(self) -> tuple[str, int, int]:
        return self._state, self._done, self._total

    # ── what the hotkeys ask ──

    def speak(self, text: str) -> None:
        """Read `text`, replacing anything already playing."""
        config = self._settings()
        pieces = split(
            text,
            words=config["words_per_chunk"],
            first_words=config["first_chunk_words"],
        )
        if not pieces:
            self._notice("Nothing to read", "That selection had no words in it.")
            return

        self.stop()

        with self._lock:
            self._generation += 1
            generation = self._generation
            self._total = len(pieces)
            self._done = 0
            self._synth_done = False
            self._state = WARMING

        count = len(text.split())
        if count >= LONG_READ_WORDS:
            from .config import label

            self._notice(
                f"Reading {count:,} words",
                f"That is a while. {label(config['hotkeys']['stop'])} stops it.",
            )

        threading.Thread(
            target=self._run, args=(generation, pieces, config), daemon=True
        ).start()

    def pause_or_resume(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            if self._state == SPEAKING:
                # State first: stopping the stream fires the finished callback,
                # which must not read this as "playback ended".
                self._state = PAUSED
                self._stream.stop()
            elif self._state == PAUSED:
                self._state = SPEAKING
                self._stream.start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._state = IDLE
            self._synth_done = True
            stream, self._stream = self._stream, None
            self._current = None
            self._offset = 0
            self._done = self._total = 0

        self._keepalive.stop()
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                break

        # Outside the lock: closing waits for the callback thread, which would
        # deadlock against anything holding it.
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - a device going away is not fatal
                pass

    # ── synthesis ──

    def _run(self, generation: int, pieces: list[str], config: dict) -> None:
        try:
            session.ensure(lambda message: self._notice("TTS by fakedevbrad", message))
        except RuntimeError as exc:
            if generation == self._generation:
                self.stop()
                self._notice("Could not start the server", str(exc))
            return

        self._keepalive.start()

        for piece in pieces:
            if generation != self._generation:
                return
            try:
                wav = session.speak(piece, config["voice"], config["speed"])
                samples, rate = sf.read(io.BytesIO(wav), dtype="float32")
            except session.PassageRejected:
                # Nothing pronounceable in this one. Losing the rest of the
                # selection over a row of emoji is the worse failure by far.
                with self._lock:
                    if generation != self._generation:
                        return
                    self._total -= 1
                continue
            except Exception as exc:  # noqa: BLE001 - report anything, stay alive
                if generation == self._generation:
                    self.stop()
                    self._notice("Synthesis failed", str(exc))
                return

            if samples.ndim > 1:
                samples = samples[:, 0]

            self._open_stream(rate, generation)

            # Block until there is room, so synthesis stays one passage ahead
            # instead of racing to buffer the whole selection into memory.
            while generation == self._generation:
                try:
                    self._audio.put((generation, samples), timeout=0.2)
                    break
                except queue.Full:
                    continue
            else:
                return

        with self._lock:
            if generation != self._generation:
                return
            self._synth_done = True
            silent = self._stream is None

        if silent:
            # Every passage was skipped, so no stream ever opened and nothing
            # will fire _finished to bring us back out of WARMING.
            self.stop()
            self._notice("Nothing to read", "That selection had no words in it.")

    def _open_stream(self, rate: int, generation: int) -> None:
        with self._lock:
            if self._stream is not None or generation != self._generation:
                return
            self._stream = sd.OutputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=self._callback,
                finished_callback=self._finished,
            )
            self._state = SPEAKING
            self._stream.start()

    # ── the audio thread ──

    def _callback(self, outdata, frames, _time, _status) -> None:
        """Runs on PortAudio's thread. Touches plain state only, never Win32."""
        written = 0
        while written < frames:
            if self._current is None or self._offset >= len(self._current):
                try:
                    generation, samples = self._audio.get_nowait()
                except queue.Empty:
                    outdata[written:] = 0
                    if self._synth_done:
                        raise sd.CallbackStop
                    return  # still synthesizing: a moment of silence, not an end
                if generation != self._generation:
                    continue  # audio from a read that has since been replaced
                self._current = samples
                self._offset = 0
                self._done += 1

            take = min(frames - written, len(self._current) - self._offset)
            outdata[written:written + take, 0] = self._current[
                self._offset:self._offset + take
            ]
            self._offset += take
            written += take

    def _finished(self) -> None:
        """Stream ended. Deliberately lock-free: it can fire while stop() holds
        the lock and waits on this very thread."""
        if self._state == SPEAKING:
            self._state = IDLE
            self._keepalive.stop()
