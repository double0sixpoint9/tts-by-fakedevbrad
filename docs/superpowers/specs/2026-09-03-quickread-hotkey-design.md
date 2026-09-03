# QuickRead: read the current selection from a global hotkey

**Date:** 2026-09-03
**Status:** built and unit-tested; items 1-6 and 8-9 of the manual checklist still to walk

## Problem

Reading something with this app currently costs a fixed toll: switch to the TTS
window, click into the paste box, clear it, paste, click Read. For the main use
— "I don't feel like reading this, say it out loud" — the toll is most of the
work.

Goal: select text anywhere on the machine, press one key — one hand, one No window, no
mouse, no focus change.

## Non-goals

- **A right-click "read this" entry that works everywhere.** Not achievable.
  Windows shell context menus attach to files, folders, drives, and the desktop;
  the menu over selected *text* belongs to the host application, and there is no
  OS-level way to inject an entry into all of them. A browser extension could do
  it inside Edge/Chrome only. Rejected as narrow relative to a hotkey that works
  in every app.
- Read-along highlighting, seeking, contents. Those are what the window is for;
  QuickRead is the no-window path.
- Any change to `server.py`, `launch.pyw`, or the web UI. `POST /speak` already
  does everything needed, so this feature is purely additive and can be removed
  by deleting its files.

## Shape

A resident background helper, started under `pythonw.exe` so it has no console
and no taskbar presence. It owns a hidden top-level window (WS_EX_TOOLWINDOW,
never shown) that receives both the hotkeys and the tray icon's callbacks.

Not a message-only window, which was the first plan: TrackPopupMenu needs an
owner that can take the foreground, and message-only windows cannot. The menu
would then refuse to close when you clicked away from it.

```
files/
  hotkey.pyw           entry point: config, tray, message loop, wiring
  quickread/
    config.py          settings file + "alt+z" -> (mods, vk) parsing
    chunks.py          sentence-aware splitting                    [pure, tested]
    selection.py       capture the current selection via the clipboard
    session.py         ensure the TTS server is up; keep it alive while speaking
    speech.py          synthesize-ahead queue, gapless playback, pause/stop
    tray.py            Shell_NotifyIcon: icon, menu, balloon tips
  tests/
    test_chunks.py     python -m unittest
    test_config.py     python -m unittest
```

## Flow

```
Alt+Z
  |
  +-> selection.capture()
  |     record GetClipboardSequenceNumber()
  |     force-release the modifiers the user is physically holding
  |     SendInput Ctrl+C
  |     poll up to 400ms for the sequence number to change
  |     read CF_UNICODETEXT; restore the previous text afterwards
  |
  +-> session.ensure()          port probe -> spawn server.py --auto-exit if down
  |                             poll /health until phase == "ready"
  |
  +-> chunks.split(text)        first chunk short, the rest ~110 words
  |
  +-> speech.play(chunks)       synth thread runs one chunk ahead of playback
                                POST /speak {text, voice, speed} -> WAV -> stream
```

`Alt+X` pauses/resumes. `Alt+C` stops and clears. Pressing `Alt+Z` while
something is playing replaces it.

## Components

### chunks.py

A deliberately simplified port of `static/js/text.js`. Keeps: whitespace
normalisation, sentence detection with the abbreviation guard ("Dr." is not a
sentence end), paragraph breaks as hard boundaries, and word-capping of run-on
segments. Drops: character offsets and chapter/heading detection, which exist to
drive the reading pane and mean nothing to a headless reader.

The divergence is intentional and worth stating plainly: two chunkers now exist.
They do not have to agree — the JS one tiles a book gaplessly for highlighting,
this one only has to produce natural-sounding breaths — but a bug here silently
mangles every read, so it gets real unit tests.

Chunk sizing: the first chunk is ~35 words so audio starts almost immediately;
the rest are ~110, which sounds better across sentence boundaries.

### selection.py

The clipboard round-trip, with two details that make it reliable:

- **Modifier release.** The hotkey fires while its modifier is physically held.
  Sending Ctrl+C then would land as Alt+Ctrl+C in the target app. So synthesize
  key-up for Alt, Shift, and Win first, then send a clean Ctrl+C.
- **Sequence number, not content comparison.** `GetClipboardSequenceNumber()`
  tells us whether the copy actually produced anything, without guessing by
  diffing text. If it never changes, nothing was selected or the app does not
  support copy.

Fallback: when the copy produces nothing but the clipboard already holds text,
read that instead and say so in the balloon. That is the escape hatch for
elevated windows (Task Manager, regedit), which Windows forbids a non-elevated
process from sending input to.

Restore is text-only. If the clipboard held an image or files, we cannot put it
back; the copied selection stays there instead. Documented, not fixed — full
multi-format clipboard preservation is far more machinery than this earns.

### session.py

`ensure()` probes port 5050; if nothing answers it spawns
`server.py --auto-exit` with `CREATE_NO_WINDOW`, appending to the existing
`launcher.log`, and waits for `/health` to report `phase == "ready"` (the model
loads after the socket binds, and `/speak` 503s until then).

`--auto-exit` is deliberate: the server's 30-minute idle timeout then releases
the ~700 MB model on its own after you stop using it, and the next hotkey press
reloads it.

`keepalive()` pings `/health` every 5s while audio is queued. Without it, closing
the app window mid-read fires its `/bye` beacon and the server would shut down
under us during the 10s grace period.

`python_exe()` duplicates ten lines from `launch.pyw` (a `.pyw` is not
importable). Duplicating beats restructuring a launcher that works.

### speech.py

- **Synth thread:** for each chunk, `POST /speak`, decode the WAV with
  `soundfile` into float32, hand it to a bounded queue (depth 2). Runs one chunk
  ahead of playback, mirroring what `static/js/player.js` does.
- **Playback:** a single `sounddevice.OutputStream` whose callback pulls frames
  from the current array. Position lives in the callback's index, which is what
  makes true pause/resume possible — `stream.stop()` then `stream.start()`
  continues where it left off. It is also gapless, unlike `sd.play`/`sd.wait`
  per chunk.
- **Cancellation:** a generation counter, incremented on stop or replace. Audio
  produced by a superseded synth thread is discarded rather than played, the
  same trick `player.js` uses for seek invalidation.
- Sample rate comes from the WAV header rather than assuming Kokoro's 24 kHz.

### tray.py

Raw `Shell_NotifyIcon` through `ctypes`, using the existing `icon.ico`. No
`pystray`, and specifically no Pillow — `make_icon.py` exists precisely to keep
Pillow out of this project.

Menu: current state, voice picker (the ten from `VOICES`), speed, Stop, Quit.
State shows in the tooltip ("Speaking — passage 3 of 12"); the icon itself does
not change, since a second .ico is not worth generating.

Balloon tips carry the things that would otherwise be silent failures: warming
up, nothing selected, reading the clipboard instead, synthesis failed, hotkey
already taken.

Note for the README: Windows 11 hides new tray icons in the overflow flyout by
default, so it may need dragging out to the visible tray once.

### config.py

`%LOCALAPPDATA%\ttsbyfakedevbrad\quickread.json`, created with defaults on first
run:

```json
{
  "voice": "af_heart",
  "speed": 1.0,
  "words_per_chunk": 110,
  "first_chunk_words": 35,
  "hotkeys": { "read": "alt+z", "pause": "alt+x", "stop": "alt+c" }
}
```

Separate from the window's settings, which live in browser localStorage and are
not reachable from Python. A different voice for quick reads than for books is a
feature, not a compromise.

`parse_hotkey("alt+z")` -> `(MOD_ALT | MOD_NOREPEAT, 0x5A)`.
Pure and unit-tested, including the malformed cases.

### hotkey.pyw

Registers a window class, creates a message-only window, registers the three
hotkeys against it, adds the tray icon, and pumps messages. `WM_HOTKEY` and the
tray callback both arrive at the same WndProc. The WNDPROC callback object is
held in a module global — if it is garbage collected, Windows calls into freed
memory.

Hotkey work happens on a worker thread. Anything slow in the WndProc (a cold
model load is ~10s) would freeze the message loop and hang the tray.

## Errors

| Situation | Behaviour |
|---|---|
| Nothing selected, clipboard empty | Balloon "Nothing selected", no-op |
| Copy failed, clipboard has text | Read it; balloon says it used the clipboard |
| Hotkey owned by another app | Balloon at startup naming the combo; helper keeps running |
| Server will not start | Balloon with the reason, pointing at `launcher.log` |
| `/speak` fails mid-queue | Stop, balloon with the error |
| Selection over ~20k words | Read it, but balloon the size and the stop key first |

## Testing

Unit tests (`python -m unittest discover tests`) cover the two pure modules:
`chunks.py` (sentence splitting, abbreviations, paragraph breaks, run-on capping,
empty input) and `config.py` (hotkey parsing, defaults, malformed files).

The Win32 surface — hotkey registration, clipboard round-trip, tray, audio —
cannot be meaningfully unit-tested. It gets a manual smoke checklist, walked
through for real before this is called done:

1. Select a paragraph in Obsidian, press Alt+Z, hear it.
2. Same in Discord, in a PDF, and in a browser.
3. Alt+X mid-sentence pauses; again resumes from the same word.
4. Alt+C stops.
5. Select new text while it is speaking, press Alt+Z: it switches.
6. Press Alt+Z with nothing selected: balloon, no crash.
7. Clipboard holding unrelated text is intact afterwards.
8. Open the app window, start a read, close the window: audio survives.
9. Tray menu changes voice; the next read uses it.

## Install

`install_shortcut.ps1` gains a `-Startup` switch that drops a shortcut to
`hotkey.pyw` in the Startup folder. Off by default; running it is the user's
call.
