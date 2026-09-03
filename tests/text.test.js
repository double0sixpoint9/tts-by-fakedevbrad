import test from 'node:test';
import assert from 'node:assert/strict';

import { normalize, splitChunks, detectChapters, chunkAtChar, isHeading } from '../static/js/text.js';

// ── normalize ────────────────────────────────────────────────────────────────
// The old app ran text.replace(/\s+/g,' ') on ingest, which flattened every
// newline. Chapter detection then split on '\n', got one giant line, and never
// matched anything. These tests pin the newline-preserving behaviour.

test('normalize collapses runs of spaces and tabs', () => {
  assert.equal(normalize('a    b\t\tc'), 'a b c');
});

test('normalize preserves newlines', () => {
  assert.equal(normalize('line one\nline two'), 'line one\nline two');
});

test('normalize collapses 3+ blank lines to a paragraph break', () => {
  assert.equal(normalize('a\n\n\n\n\nb'), 'a\n\nb');
});

test('normalize strips trailing spaces before a newline', () => {
  assert.equal(normalize('a   \n   b'), 'a\nb');
});

test('normalize turns CRLF into LF', () => {
  assert.equal(normalize('a\r\nb'), 'a\nb');
});

// ── splitChunks ──────────────────────────────────────────────────────────────
// Chunks carry char offsets into the source text so the reader can render the
// book verbatim and the player can map a chunk back to a position.

const PROSE = [
  'The sun set over the ridge. Birds went quiet in the pines.',
  'Nobody spoke for a while. It was the kind of silence that earns itself.',
  'Later, someone laughed. The spell broke, and the evening started properly.',
].join(' ');

test('chunks tile the source text exactly, with no gaps or overlap', () => {
  const chunks = splitChunks(PROSE, 12);
  assert.ok(chunks.length > 1, 'expected the sample to split into several chunks');
  assert.equal(chunks[0].start, 0);
  assert.equal(chunks.at(-1).end, PROSE.length);
  for (let i = 1; i < chunks.length; i++) {
    assert.equal(chunks[i].start, chunks[i - 1].end, `chunk ${i} must start where ${i - 1} ended`);
  }
  assert.equal(chunks.map(c => PROSE.slice(c.start, c.end)).join(''), PROSE);
});

test('chunk.text matches its own slice of the source', () => {
  for (const c of splitChunks(PROSE, 12)) {
    assert.equal(c.text, PROSE.slice(c.start, c.end).trim());
  }
});

test('chunks break after sentence terminators, never mid-sentence', () => {
  for (const c of splitChunks(PROSE, 12)) {
    assert.match(c.text, /[.!?"'”’]$/, `chunk ended mid-sentence: ${JSON.stringify(c.text)}`);
  }
});

test('a chunk keeps growing until it reaches the target word count', () => {
  // Target 20 words: no chunk should be far under it except the last one.
  const chunks = splitChunks(PROSE, 20);
  for (const c of chunks.slice(0, -1)) {
    assert.ok(c.text.split(/\s+/).length >= 10, `chunk suspiciously short: ${c.text}`);
  }
});

test('an abbreviation does not end a chunk', () => {
  const text = 'We met Dr. Silva at the docks. She had already gone.';
  const chunks = splitChunks(text, 6);
  assert.ok(!chunks.some(c => c.text.endsWith('Dr.')), 'split on the abbreviation "Dr."');
});

test('text with no sentence terminators still splits by word count', () => {
  const runOn = Array.from({ length: 90 }, (_, i) => `word${i}`).join(' ');
  const chunks = splitChunks(runOn, 20);
  assert.ok(chunks.length >= 4, 'run-on text should still be broken up');
  assert.equal(chunks.map(c => runOn.slice(c.start, c.end)).join(''), runOn);
});

test('one short sentence yields exactly one chunk', () => {
  const chunks = splitChunks('Just this.', 120);
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].text, 'Just this.');
});

test('empty or whitespace-only text yields no chunks', () => {
  assert.deepEqual(splitChunks('', 120), []);
  assert.deepEqual(splitChunks('   \n\n  ', 120), []);
});

test('a paragraph break always closes a chunk', () => {
  const text = 'First para sentence one. Second one here.\n\nNew paragraph begins now.';
  const chunks = splitChunks(text, 500); // target far larger than the whole text
  assert.equal(chunks.length, 2, 'paragraph break should force a split despite the huge target');
});

// ── chunkAtChar ──────────────────────────────────────────────────────────────

test('chunkAtChar finds the chunk containing a character offset', () => {
  const chunks = splitChunks(PROSE, 12);
  assert.equal(chunkAtChar(chunks, 0), 0);
  const mid = chunks[2];
  assert.equal(chunkAtChar(chunks, mid.start + 1), 2);
  assert.equal(chunkAtChar(chunks, PROSE.length + 999), chunks.length - 1);
});

// ── detectChapters ───────────────────────────────────────────────────────────

const BOOK = [
  'Front matter that nobody reads.',
  '',
  'Prologue',
  '',
  'It began with a phone call.',
  '',
  'Chapter 1',
  '',
  'The first thing she noticed was the smell.',
  '',
  'CHAPTER TWO',
  '',
  'Rain, again.',
  '',
  'Epilogue',
  '',
  'And that was that.',
].join('\n');

test('detectChapters finds headings on their own lines', () => {
  const chapters = detectChapters(BOOK);
  assert.deepEqual(chapters.map(c => c.title), ['Prologue', 'Chapter 1', 'CHAPTER TWO', 'Epilogue']);
});

test('detectChapters reports char offsets that point at the heading', () => {
  for (const ch of detectChapters(BOOK)) {
    assert.equal(BOOK.slice(ch.start, ch.start + ch.title.length), ch.title);
  }
});

test('detectChapters ignores a long prose line that merely starts with a keyword', () => {
  const text = 'Chapter and verse were the only things he had ever really cared about, '
             + 'and he said so at every opportunity to anyone unlucky enough to be nearby.';
  assert.deepEqual(detectChapters(text), []);
});

test('detectChapters returns nothing for text with no headings', () => {
  assert.deepEqual(detectChapters('Just some prose.\n\nMore prose.'), []);
});

test('detectChapters regression: flattened text yields no false positives', () => {
  // Guards the original bug from the other direction - if newlines were ever
  // stripped again, this must not silently "find" one giant chapter.
  const flattened = BOOK.replace(/\s+/g, ' ');
  assert.deepEqual(detectChapters(flattened), []);
});

// ── headings ─────────────────────────────────────────────────────────────────
// A heading has no sentence terminator, so a purely terminator-driven splitter
// glues it onto the sentence that follows: "Chapter 1 The sun set over..." both
// reads wrong on screen and is spoken as a single breath.

test('a heading is its own chunk, not glued to the next sentence', () => {
  const text = 'Chapter 1\n\nThe sun set over the ridge.';
  const chunks = splitChunks(text, 500);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].text, 'Chapter 1');
  assert.equal(chunks[1].text, 'The sun set over the ridge.');
});

test('paragraph breaks split even when no sentence ends at them', () => {
  const text = 'Prologue\n\nIt began with a call.\n\nChapter 1\n\nRain, again.';
  const chunks = splitChunks(text, 500);
  assert.deepEqual(chunks.map((c) => c.text),
    ['Prologue', 'It began with a call.', 'Chapter 1', 'Rain, again.']);
});

test('heading splitting keeps the tiling gapless', () => {
  const text = 'Prologue\n\nIt began with a call.\n\nChapter 1\n\nRain, again.';
  const chunks = splitChunks(text, 500);
  assert.equal(chunks[0].start, 0);
  assert.equal(chunks.at(-1).end, text.length);
  assert.equal(chunks.map((c) => text.slice(c.start, c.end)).join(''), text);
});

test('isHeading recognises structural lines only', () => {
  assert.equal(isHeading('Chapter 1'), true);
  assert.equal(isHeading('PROLOGUE'), true);
  assert.equal(isHeading('The sun set over the ridge.'), false);
});
