# TTS by fakedevbrad

A local AI audiobook reader. Drop in a PDF, EPUB, or TXT (or paste text) and it
reads aloud with Kokoro-ONNX while highlighting the passage being spoken.

Everything runs on this machine. Nothing is uploaded, and after the one-time
model download the app works with no internet at all.

## Running it

Double-click **TTS by fakedevbrad** on the desktop. That starts the server with
no console window, waits for it, and opens a chromeless app window.

Closing the window stops the server (the page tells it to on the way out).

To recreate or move the shortcut:

```
powershell -ExecutionPolicy Bypass -File install_shortcut.ps1
```

To run it by hand instead:

```
python server.py            # then open http://127.0.0.1:5050
python launch.pyw --debug   # launcher with a console and verbose logging
```

## Using it

| | |
|---|---|
| `Space` | play / pause |
| `←` `→` | previous / next passage |
| `Esc` | close a drawer |
| Click any paragraph | jump playback there |
| `Ctrl+Enter` | play pasted text |

**Contents** lists chapters when the book has them, pages otherwise.
**Voice** holds the ten voices, playback speed, and passage length.

Your place in each book is remembered, so reopening one picks up where you
stopped. Shorter passages start playing sooner and track your position more
tightly; longer ones sound slightly more natural across sentence boundaries.

## Reading anything, from anywhere

Select text in any app — Obsidian, Discord, a PDF, a browser, Word, a
terminal — and press **Alt+Z**. It reads it aloud. No window opens and
nothing steals focus.

Some apps draw text that cannot be highlighted at all; Phone Link's messages
are the usual example. There is nothing for Ctrl+C to copy in those, so
QuickRead asks the app for its text instead — **point the mouse at what you
want and press Alt+Z**. That path uses UI Automation and needs `comtypes`:

```
pip install comtypes
```

Without it every other app still works exactly as before, and only these
unhighlightable ones stay silent.

| | |
|---|---|
| `Alt+Z` | read the selection, replacing anything already playing |
| `Alt+X` | pause / resume, on the same word |
| `Alt+C` | stop |

Alt under the thumb, Z X C under the fingers: one hand, no looking down, no
letting go of the mouse. These are registered globally, so they take precedence
over any app that uses the same combo — if one clashes with something you use,
change it in the settings file below.

Start it by hand with `pythonw hotkey.pyw`, or install it to run at login:

```
powershell -ExecutionPolicy Bypass -File install_shortcut.ps1 -Startup
```

It sits in the tray. Right-click for the voice, the speed, what is currently
playing, and Quit. Windows 11 hides new tray icons in the `^` overflow by
default, so you may want to drag it out once.

Its settings are separate from the window's, in
`%LOCALAPPDATA%\ttsbyfakedevbrad\quickread.json` — voice, speed, passage
length, and the three key combos, all editable from the tray's *Edit settings*.
A different voice for quick reads than for books is the point, not an oversight.

The first press after a while costs about ten seconds while Kokoro loads; after
that it starts talking in a second or two. The server releases the model half an
hour after your last read, and reloads it on the next one.

**How it gets the text.** Windows has no way to ask an arbitrary app what is
selected, so QuickRead copies the selection (a simulated Ctrl+C), reads the
clipboard, and puts your clipboard back. Two consequences worth knowing. In
windows running as administrator — Task Manager, regedit — Windows blocks
the simulated keystroke, and QuickRead falls back to reading whatever is already
on the clipboard, saying so as it does. And if your clipboard held an image or
files rather than text, it cannot be put back afterwards; the copied text stays
there instead.

There is no right-click "read this" menu item, and there cannot be a universal
one: Windows shell context menus attach to files and folders, while the menu
over selected *text* belongs to whatever app you are in. The hotkey is the
version of that idea that works everywhere.

## The read-along marker

The passage being spoken is outlined and glows, and a word marker glides across
it. Kokoro returns no word timestamps, so the marker interpolates from the
passage's audio position weighted by word length. It is approximate mid-passage
but resyncs exactly at every boundary, so drift never accumulates.

## Layout

```
server.py             TTS + static file server (port 5050)
launch.pyw            desktop launcher: starts server, opens the app window
install_shortcut.ps1  creates the desktop shortcut (-Startup adds QuickRead)
make_icon.py          regenerates icon.ico (pure stdlib, no Pillow)
hotkey.pyw            QuickRead: global hotkey + tray icon, no window
quickread/            what QuickRead is made of
  config.py           settings file, hotkey string parsing (pure, tested)
  chunks.py           sentence-aware splitting for headless reads (pure, tested)
  selection.py        lifts the current selection via the clipboard
  uia.py              reads text from apps the clipboard cannot reach
  session.py          starts the server, keeps it alive while speaking
  speech.py           synthesize-ahead queue, gapless playback, pause
  tray.py             Shell_NotifyIcon through ctypes: icon, menu, balloons
static/               the UI
  index.html  app.css
  js/text.js          normalisation, chunking, chapter detection (pure, tested)
  js/ingest.js        PDF / EPUB / TXT extraction
  js/player.js        playback queue, preloading, seek invalidation
  js/reader.js        reading pane, highlight, word marker, autoscroll
  js/nav.js           contents drawer
  js/tts.js           server client
  js/app.js           wiring
  vendor/             pdf.js, JSZip, and fonts, all self-hosted for offline use
tests/                node --test for the UI logic, unittest for QuickRead
kokoro-v1.0.onnx      model (~310 MB, downloaded on first run)
voices-v1.0.bin       voices (~20 MB, downloaded on first run)
```

## Tests

```
node --test "tests/*.test.js"                 the reader's text logic
python -m unittest discover -s tests -t .     QuickRead's chunker and config
```

Both cover the same kind of thing: pure logic where a silent regression
corrupts every book, or every read, without ever raising an error. QuickRead's
Win32 half — hotkeys, clipboard, tray, audio — is checked by hand against
the checklist in `docs/superpowers/specs/2026-09-03-quickread-hotkey-design.md`.

## Notes

- The server listens on `127.0.0.1` only, so it is not reachable from the
  network.
- The app window uses Edge (or Chrome) in `--app` mode with its own profile,
  because Firefox has no equivalent chromeless-window mode. This does not touch
  your normal browser or its session.
- `launcher.log` records what the launcher did, which is the first place to look
  if double-clicking the shortcut does nothing.
- `_replaced/` holds the previous version's files and can be deleted.
