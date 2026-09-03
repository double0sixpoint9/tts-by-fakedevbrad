/**
 * Text normalisation, sentence-aware chunking, and chapter detection.
 *
 * Everything here is pure and offset-based: a chunk knows exactly which
 * characters of the source it covers, so the reader can render the book
 * verbatim and still know which span is currently being spoken.
 */

/** Words that end in a period without ending a sentence. */
const ABBREVIATIONS = new Set([
  'mr', 'mrs', 'ms', 'dr', 'prof', 'st', 'jr', 'sr', 'vs', 'etc', 'inc', 'ltd',
  'co', 'no', 'vol', 'ch', 'fig', 'p', 'pp', 'ave', 'blvd', 'rd', 'mt', 'ft',
  'sgt', 'capt', 'lt', 'gen', 'gov', 'sen', 'rep', 'messrs', 'al', 'ca', 'cf',
]);

const CHAPTER_RE =
  /^(chapter|part|book|section|prologue|epilogue|introduction|preface|afterword|foreword|interlude)\b/i;

/** Trailing quotes and brackets that may follow a sentence's final period. */
const CLOSERS = `"'”’)]}`;

const countWords = (s) => (s.trim() ? s.trim().split(/\s+/).length : 0);

/**
 * Tidy raw extracted text without destroying its line structure.
 *
 * The original app collapsed *all* whitespace including newlines, which is why
 * chapter detection could never find a heading. Horizontal whitespace collapses;
 * newlines survive, capped at one blank line.
 */
export function normalize(text) {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/[^\S\n]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * Decide whether the period at `i` genuinely ends a sentence.
 * Rejects abbreviations ("Dr.") and single-letter initials ("J. R. R.").
 */
function endsSentence(text, i) {
  let wordStart = i;
  while (wordStart > 0 && /[A-Za-z]/.test(text[wordStart - 1])) wordStart--;
  const word = text.slice(wordStart, i).toLowerCase();
  if (!word) return true;
  if (word.length === 1) return false;
  return !ABBREVIATIONS.has(word);
}

/**
 * Slice text into contiguous segments covering [0, text.length).
 *
 * A segment ends at a sentence terminator *or* at a paragraph break. The second
 * case matters: a heading like "Chapter 1" carries no terminator, so a purely
 * terminator-driven scan swallows it into the sentence that follows — which
 * both reads wrong and makes Kokoro speak the two as one breath.
 *
 * Each segment carries `breaksParagraph` so chunking can honour paragraph
 * boundaries even when a chunk is nowhere near its target size.
 */
function toSentences(text) {
  const segments = [];
  const push = (end, breaksParagraph) => {
    segments.push({ start, end, breaksParagraph });
    start = end;
  };

  let start = 0;
  let i = 0;

  while (i < text.length) {
    const ch = text[i];

    if (/\s/.test(ch)) {
      let k = i;
      while (k < text.length && /\s/.test(text[k])) k++;
      // A blank line closes the segment; ordinary spacing does not.
      if (text.slice(i, k).includes('\n\n')) { push(k, true); i = k; }
      else i = k;
      continue;
    }

    if (!'.!?…'.includes(ch) || (ch === '.' && !endsSentence(text, i))) {
      i++;
      continue;
    }

    // Absorb repeated terminators ("?!") and any closing quotes/brackets.
    let j = i + 1;
    while (j < text.length && '.!?…'.includes(text[j])) j++;
    while (j < text.length && CLOSERS.includes(text[j])) j++;

    // A sentence only ends if whitespace or the end of the text follows.
    if (j < text.length && !/\s/.test(text[j])) { i = j; continue; }

    // Trailing whitespace belongs to this segment, keeping coverage gapless.
    let k = j;
    while (k < text.length && /\s/.test(text[k])) k++;
    push(k, text.slice(j, k).includes('\n\n') || k >= text.length);
    i = k;
  }

  if (start < text.length) {
    segments.push({ start, end: text.length, breaksParagraph: true });
  }
  return segments;
}

/**
 * Break any segment far larger than the target into word-sized pieces, so
 * run-on text with no punctuation still chunks sensibly. Splits land on
 * whitespace, preserving gapless coverage.
 */
function capSegmentLength(text, segments, targetWords) {
  const limit = Math.max(1, Math.round(targetWords * 1.5));
  const out = [];

  for (const seg of segments) {
    const body = text.slice(seg.start, seg.end);
    if (countWords(body) <= limit) {
      out.push(seg);
      continue;
    }

    let cursor = seg.start;
    let seen = 0;
    for (let i = seg.start; i < seg.end; i++) {
      if (!/\s/.test(text[i])) continue;
      // Only count a word once, at its first trailing whitespace character.
      if (i > seg.start && /\s/.test(text[i - 1])) continue;
      seen++;
      if (seen < targetWords) continue;

      let k = i;
      while (k < seg.end && /\s/.test(text[k])) k++;
      out.push({ start: cursor, end: k, breaksParagraph: false });
      cursor = k;
      seen = 0;
      i = k - 1;
    }
    if (cursor < seg.end) {
      out.push({ start: cursor, end: seg.end, breaksParagraph: seg.breaksParagraph });
    }
  }
  return out;
}

/**
 * Split text into chunks of roughly `targetWords`, always breaking on a
 * sentence or paragraph boundary.
 *
 * @returns {{start:number,end:number,text:string}[]} contiguous chunks whose
 *   slices concatenate back to the exact source text.
 */
export function splitChunks(text, targetWords = 120) {
  if (!text || !text.trim()) return [];

  const segments = capSegmentLength(text, toSentences(text), targetWords);
  const chunks = [];
  let start = 0;
  let words = 0;

  for (const seg of segments) {
    words += countWords(text.slice(seg.start, seg.end));
    const isLast = seg.end >= text.length;
    if (words >= targetWords || seg.breaksParagraph || isLast) {
      chunks.push({ start, end: seg.end, text: text.slice(start, seg.end).trim() });
      start = seg.end;
      words = 0;
    }
  }

  if (start < text.length) {
    chunks.push({ start, end: text.length, text: text.slice(start).trim() });
  }

  // Drop chunks that are pure whitespace, folding their span into a neighbour
  // so the tiling stays gapless.
  return chunks.reduce((acc, c) => {
    if (!c.text && acc.length) {
      acc[acc.length - 1].end = c.end;
      return acc;
    }
    if (!c.text) return acc;
    return (acc.push(c), acc);
  }, []);
}

/** True when a chunk reads as a structural heading rather than prose. */
export function isHeading(text) {
  return text.length <= 80 && CHAPTER_RE.test(text.trim());
}

/** Index of the chunk containing `charPos`, clamped to the valid range. */
export function chunkAtChar(chunks, charPos) {
  if (!chunks.length) return 0;
  for (let i = chunks.length - 1; i >= 0; i--) {
    if (charPos >= chunks[i].start) return i;
  }
  return 0;
}

/**
 * Find chapter headings: short lines, on their own, opening with a structural
 * keyword. Depends on `normalize` having preserved newlines.
 *
 * @returns {{title:string,start:number,end:number}[]}
 */
export function detectChapters(text) {
  const found = [];
  let offset = 0;

  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (trimmed.length >= 3 && trimmed.length <= 80 && CHAPTER_RE.test(trimmed)) {
      found.push({ title: trimmed, start: offset + line.indexOf(trimmed), end: 0 });
    }
    offset += line.length + 1;
  }

  found.forEach((ch, i) => {
    ch.end = i + 1 < found.length ? found[i + 1].start : text.length;
  });
  return found;
}
