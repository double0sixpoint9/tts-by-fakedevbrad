/**
 * Wiring: boots the server connection, ingests books, and keeps the player,
 * reading pane, contents drawer, and transport in agreement.
 */

import { health, sayGoodbye } from './tts.js';
import { extract } from './ingest.js';
import { normalize, splitChunks, chunkAtChar } from './text.js';
import { Player } from './player.js';
import { Reader } from './reader.js';
import { buildContents, Contents } from './nav.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdf.worker.min.js';

const $ = (id) => document.getElementById(id);

const STORE_SETTINGS = 'tts.settings';
const STORE_PLACE = 'tts.place';

// ── state ────────────────────────────────────────────────────────────────────

const settings = loadJSON(STORE_SETTINGS, { voice: 'af_heart', speed: 1, chunkWords: 120 });

const book = { title: '', text: '', chunks: [], key: '' };
let online = false;
let scrubbing = false;

// ── components ───────────────────────────────────────────────────────────────

const player = new Player({
  settings: () => ({ voice: settings.voice, speed: settings.speed }),
  on: {
    index: (i) => {
      reader.setActive(i);
      contents.setCurrentChunk(i);
      savePlace(i);
      syncTransport();
    },
    progress: (ratio) => {
      reader.setProgress(ratio);
      if (!scrubbing) paintTrack((player.index + ratio) / Math.max(1, player.count));
    },
    state: syncTransport,
    error: (err) => toast(err.message),
  },
});

const reader = new Reader($('pane'), {
  onSeek: (i) => player.seek(i, { autoplay: true }),
  onFollowChange: (on) => { $('followPill').hidden = on; },
});

const contents = new Contents($('toc'), {
  onPick: (chunk) => { closeDrawers(); reader.setFollow(true); player.seek(chunk, { autoplay: true }); },
});

// ── server connection ────────────────────────────────────────────────────────

let voicesBuilt = false;
let lastPhase = '';
let pollTimer;

/**
 * Poll /health, fast while the model is still warming up and slowly once it is
 * ready. The old version rebuilt the entire voice grid on every 15s tick.
 */
async function checkServer() {
  const status = $('status');
  const result = await health();
  online = result.ok;

  if (result.ok && !voicesBuilt) {
    buildVoices(result.voices);
    voicesBuilt = true;
  }

  if (result.ok) {
    status.dataset.state = 'online';
    $('statusText').textContent = 'Kokoro ready';
  } else if (result.phase === 'error') {
    status.dataset.state = 'offline';
    $('statusText').textContent = 'Startup failed';
    if (lastPhase !== 'error') toast(result.detail);
  } else if (result.phase === 'down') {
    status.dataset.state = 'offline';
    $('statusText').textContent = 'Server offline';
  } else {
    status.dataset.state = 'checking';
    $('statusText').textContent =
      result.percent == null ? result.detail : `${result.detail} · ${result.percent}%`;
  }

  lastPhase = result.phase;
  syncTransport();

  clearTimeout(pollTimer);
  pollTimer = setTimeout(checkServer, result.ok ? 15000 : 1000);
}

function buildVoices(voices) {
  const box = $('voices');
  box.textContent = '';
  if (!voices || !Object.keys(voices).length) return;
  if (!voices[settings.voice]) settings.voice = Object.keys(voices)[0];

  for (const [id, label] of Object.entries(voices)) {
    const [name, meta = ''] = label.split(' (');
    const btn = document.createElement('button');
    btn.className = 'voice' + (id === settings.voice ? ' voice--on' : '');
    btn.dataset.id = id;
    btn.innerHTML = '<b></b><span></span>';
    btn.querySelector('b').textContent = name;
    btn.querySelector('span').textContent = meta.replace(')', '');
    btn.onclick = () => {
      settings.voice = id;
      saveJSON(STORE_SETTINGS, settings);
      player.invalidateCache();
      box.querySelectorAll('.voice').forEach((b) => b.classList.toggle('voice--on', b === btn));
    };
    box.append(btn);
  }
}

// ── opening a book ───────────────────────────────────────────────────────────

async function openFile(file) {
  busy(true, 'Reading your book…');
  try {
    const { title, text } = await extract(file);
    openText(title, text);
  } catch (err) {
    toast(err.message);
  } finally {
    busy(false);
  }
}

function openText(title, text) {
  book.title = title;
  book.text = text;
  book.key = `${title}::${text.length}`;
  book.chunks = splitChunks(text, settings.chunkWords);

  reader.render(book.chunks);
  contents.render(buildContents(text, book.chunks));

  const resumeAt = loadJSON(STORE_PLACE, {})[book.key] ?? 0;
  const start = Math.min(resumeAt, book.chunks.length - 1);

  player.load(book.chunks, start);
  reader.setActive(start);
  reader.setFollow(true);

  const words = text.split(/\s+/).length;
  $('bookTitle').textContent = title;
  $('bookMeta').textContent =
    `${words.toLocaleString()} words · ~${Math.max(1, Math.round(words / 155))} min · ${book.chunks.length} passages`;

  $('screenStart').hidden = true;
  $('screenReader').hidden = false;
  $('transport').hidden = false;
  $('btnContents').hidden = false;
  $('btnNew').hidden = false;

  if (start > 0) toast(`Picked up where you left off — passage ${start + 1}.`);
  syncTransport();
}

function closeBook() {
  player.stop();
  book.chunks = [];
  book.key = '';
  $('screenReader').hidden = true;
  $('transport').hidden = true;
  $('btnContents').hidden = true;
  $('btnNew').hidden = true;
  $('screenStart').hidden = false;
  $('fileInput').value = '';

  // Land in the paste box with whatever was there already selected, so the
  // next Ctrl+V replaces it without clicking or clearing first.
  paste.focus();
  paste.select();
  paste.scrollTop = 0;
}

// ── transport ────────────────────────────────────────────────────────────────

function syncTransport() {
  const ready = book.chunks.length > 0;
  const active = player.isActive;

  $('btnPlay').textContent = active ? '⏸' : '▶';
  $('btnPlay').setAttribute('aria-label', active ? 'Pause' : 'Play');
  $('btnPlay').disabled = !ready || !online;
  $('btnPrev').disabled = !ready || player.index === 0;
  $('btnNext').disabled = !ready || player.index >= player.count - 1;
  $('genLabel').hidden = player.state !== 'generating';

  $('posLabel').textContent = ready
    ? `Passage ${player.index + 1} of ${player.count}`
    : '—';
}

function paintTrack(ratio) {
  const pct = Math.max(0, Math.min(1, ratio)) * 100;
  $('trackFill').style.width = `${pct}%`;
  $('trackThumb').style.left = `${pct}%`;
  $('track').setAttribute('aria-valuenow', String(Math.round(pct)));
}

function seekFromPointer(clientX) {
  const rect = $('track').getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  paintTrack(ratio);
  return Math.min(player.count - 1, Math.floor(ratio * player.count));
}

// ── events ───────────────────────────────────────────────────────────────────

$('btnPlay').onclick = () => { reader.setFollow(true); player.toggle(); };
$('btnPrev').onclick = () => { reader.setFollow(true); player.skip(-1); };
$('btnNext').onclick = () => { reader.setFollow(true); player.skip(1); };
$('btnNew').onclick = closeBook;
$('followPill').onclick = () => reader.setFollow(true);

// Scrub: paint while dragging, seek once on release, so one request is sent.
$('track').addEventListener('pointerdown', (e) => {
  if (!book.chunks.length) return;
  scrubbing = true;
  $('track').setPointerCapture(e.pointerId);
  seekFromPointer(e.clientX);
});
$('track').addEventListener('pointermove', (e) => { if (scrubbing) seekFromPointer(e.clientX); });
$('track').addEventListener('pointerup', (e) => {
  if (!scrubbing) return;
  scrubbing = false;
  reader.setFollow(true);
  player.seek(seekFromPointer(e.clientX), { autoplay: player.isActive });
});
$('track').addEventListener('keydown', (e) => {
  const step = { ArrowLeft: -1, ArrowRight: 1, PageDown: 5, PageUp: -5 }[e.key];
  if (!step) return;
  e.preventDefault();
  player.skip(step);
});

// Drop zone
const dz = $('dropzone');
dz.onclick = () => $('fileInput').click();
dz.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); } };
$('fileInput').onchange = (e) => e.target.files[0] && openFile(e.target.files[0]);

for (const type of ['dragenter', 'dragover']) {
  dz.addEventListener(type, (e) => { e.preventDefault(); dz.classList.add('over'); });
}
for (const type of ['dragleave', 'drop']) {
  dz.addEventListener(type, (e) => { e.preventDefault(); dz.classList.remove('over'); });
}
dz.addEventListener('drop', (e) => e.dataTransfer.files[0] && openFile(e.dataTransfer.files[0]));

// Paste to play
const paste = $('pasteInput');
paste.addEventListener('input', () => {
  const words = paste.value.trim() ? paste.value.trim().split(/\s+/).length : 0;
  $('pastePlay').disabled = !words;
  $('pasteHint').textContent = words
    ? `${words.toLocaleString()} word${words === 1 ? '' : 's'} — Ctrl + Enter to play`
    : 'Ctrl + Enter to play';
});
paste.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); playPasted(); }
});
$('pastePlay').onclick = playPasted;

function playPasted() {
  const text = normalize(paste.value);
  if (!text) return;
  openText('Pasted text', text);
  player.play();
}

// Sliders
$('speed').value = settings.speed;
$('speedVal').textContent = `${Number(settings.speed).toFixed(1)}×`;
$('speed').addEventListener('input', (e) => {
  settings.speed = parseFloat(e.target.value);
  $('speedVal').textContent = `${settings.speed.toFixed(1)}×`;
  saveJSON(STORE_SETTINGS, settings);
  player.invalidateCache();
});

$('chunk').value = settings.chunkWords;
$('chunkVal').textContent = `${settings.chunkWords} words`;
$('chunk').addEventListener('input', (e) => {
  settings.chunkWords = parseInt(e.target.value, 10);
  $('chunkVal').textContent = `${settings.chunkWords} words`;
});
$('chunk').addEventListener('change', () => {
  saveJSON(STORE_SETTINGS, settings);
  if (!book.text) return;

  // Keep the reader's place: remap through the character offset rather than
  // jumping back to the start the way the old slider did.
  const charPos = book.chunks[player.index]?.start ?? 0;
  const wasActive = player.isActive;

  book.chunks = splitChunks(book.text, settings.chunkWords);
  const index = chunkAtChar(book.chunks, charPos);

  reader.render(book.chunks);
  contents.render(buildContents(book.text, book.chunks));
  player.load(book.chunks, index);
  reader.setActive(index);
  if (wasActive) player.play();
  syncTransport();
});

// Drawers
$('btnContents').onclick = () => openDrawer('drawerContents');
$('btnSettings').onclick = () => openDrawer('drawerSettings');
$('scrim').onclick = closeDrawers;
document.querySelectorAll('[data-close]').forEach((b) => { b.onclick = closeDrawers; });

function openDrawer(id) {
  closeDrawers();
  $(id).hidden = false;
  $('scrim').hidden = false;
}
function closeDrawers() {
  $('drawerContents').hidden = true;
  $('drawerSettings').hidden = true;
  $('scrim').hidden = true;
}

// Keyboard shortcuts (ignored while typing)
document.addEventListener('keydown', (e) => {
  if (e.target.matches('textarea, input')) return;
  if (e.key === 'Escape') return closeDrawers();
  if (!book.chunks.length) return;

  if (e.key === ' ') { e.preventDefault(); reader.setFollow(true); player.toggle(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); reader.setFollow(true); player.skip(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); reader.setFollow(true); player.skip(-1); }
});

// ── helpers ──────────────────────────────────────────────────────────────────

function busy(on, label) {
  $('loading').hidden = !on;
  if (label) $('loadingText').textContent = label;
}

let toastTimer;
function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 5200);
}

function loadJSON(key, fallback) {
  try { return { ...fallback, ...JSON.parse(localStorage.getItem(key) || '{}') }; }
  catch { return fallback; }
}
function saveJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

let placeTimer;
function savePlace(index) {
  if (!book.key) return;
  clearTimeout(placeTimer);
  placeTimer = setTimeout(() => {
    const all = loadJSON(STORE_PLACE, {});
    all[book.key] = index;
    saveJSON(STORE_PLACE, all);
  }, 800);
}

// ── boot ─────────────────────────────────────────────────────────────────────

// Closing the window stops the server (see --auto-exit in server.py).
addEventListener('pagehide', sayGoodbye);

checkServer();
syncTransport();

