/**
 * Contents drawer: chapters when the book has them, evenly spaced pages
 * otherwise.
 *
 * Chunks carry character offsets, so a heading maps to its chunk exactly
 * rather than by the old chars-per-chunk estimate.
 */

import { detectChapters, chunkAtChar } from './text.js';

const WORDS_PER_PAGE = 300;

/**
 * @returns {{kind:'chapters'|'pages', entries:{label:string, chunk:number}[]}}
 */
export function buildContents(fullText, chunks) {
  const chapters = detectChapters(fullText);
  if (chapters.length > 1) {
    return {
      kind: 'chapters',
      entries: chapters.map((ch) => ({ label: ch.title, chunk: chunkAtChar(chunks, ch.start) })),
    };
  }

  const entries = [];
  let words = 0;
  let page = 1;
  chunks.forEach((chunk, i) => {
    if (words === 0) entries.push({ label: `Page ${page++}`, chunk: i });
    words += chunk.text.split(/\s+/).length;
    if (words >= WORDS_PER_PAGE) words = 0;
  });
  return { kind: 'pages', entries };
}

export class Contents {
  #list;
  #onPick;
  #entries = [];
  #current = -1;

  constructor(list, { onPick } = {}) {
    this.#list = list;
    this.#onPick = onPick;
    list.addEventListener('click', (e) => {
      const item = e.target.closest('.toc-item');
      if (item) this.#onPick?.(Number(item.dataset.chunk));
    });
  }

  render({ kind, entries }) {
    this.#entries = entries;
    this.#current = -1;
    this.#list.textContent = '';

    if (!entries.length) {
      this.#list.innerHTML = '<p class="toc-empty">Nothing to navigate yet.</p>';
      return;
    }

    const frag = document.createDocumentFragment();
    entries.forEach((entry, i) => {
      const el = document.createElement('button');
      el.className = 'toc-item';
      el.dataset.chunk = String(entry.chunk);
      el.innerHTML =
        `<span class="toc-num">${kind === 'chapters' ? i + 1 : ''}</span>` +
        `<span class="toc-label"></span>`;
      el.querySelector('.toc-label').textContent = entry.label;
      frag.append(el);
    });
    this.#list.append(frag);
  }

  /** Highlight whichever entry contains the playing chunk. */
  setCurrentChunk(chunkIndex) {
    let found = 0;
    for (let i = this.#entries.length - 1; i >= 0; i--) {
      if (chunkIndex >= this.#entries[i].chunk) { found = i; break; }
    }
    if (found === this.#current) return;

    this.#list.children[this.#current]?.classList.remove('toc-item--active');
    this.#current = found;
    const el = this.#list.children[found];
    el?.classList.add('toc-item--active');
    el?.scrollIntoView({ block: 'nearest' });
  }
}
