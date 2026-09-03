/**
 * The reading pane: renders the whole book, marks the chunk being spoken, and
 * glides a word marker across it.
 *
 * Kokoro returns no timestamps, so word position is interpolated from the
 * chunk's audio progress weighted by character length. It resyncs exactly at
 * every chunk boundary, so drift never accumulates.
 */

import { isHeading } from './text.js';

export class Reader {
  #root;
  #onSeek;
  #paras = [];
  #active = -1;
  #words = []; // spans of the active chunk
  #weights = []; // cumulative char fraction per word
  #lit = -1;
  #follow = true;
  #onFollowChange;

  /**
   * @param {HTMLElement} root scrolling container for the text
   * @param {object} opts
   * @param {(index:number) => void} opts.onSeek user clicked a paragraph
   * @param {(following:boolean) => void} opts.onFollowChange
   */
  constructor(root, { onSeek, onFollowChange } = {}) {
    this.#root = root;
    this.#onSeek = onSeek;
    this.#onFollowChange = onFollowChange;

    root.addEventListener('click', (e) => {
      const para = e.target.closest('.chunk');
      if (!para) return;
      this.setFollow(true);
      this.#onSeek?.(Number(para.dataset.i));
    });

    // Any deliberate scroll hands control back to the reader.
    const release = () => this.setFollow(false);
    root.addEventListener('wheel', release, { passive: true });
    root.addEventListener('touchmove', release, { passive: true });
  }

  get following() { return this.#follow; }

  setFollow(on) {
    if (this.#follow === on) return;
    this.#follow = on;
    this.#onFollowChange?.(on);
    if (on) this.#scrollToActive('smooth');
  }

  /** Paint the whole book, one paragraph per chunk. */
  render(chunks) {
    this.#root.textContent = '';
    this.#paras = [];
    this.#active = -1;
    this.#words = [];

    const frag = document.createDocumentFragment();
    chunks.forEach((chunk, i) => {
      const p = document.createElement('p');
      p.className = 'chunk' + (isHeading(chunk.text) ? ' chunk--heading' : '');
      p.dataset.i = String(i);
      p.textContent = chunk.text.replace(/\n/g, ' ');
      frag.append(p);
      this.#paras.push(p);
    });
    this.#root.append(frag);
  }

  /** Mark chunk `i` as the one being spoken. */
  setActive(i) {
    if (i === this.#active) return;

    const previous = this.#paras[this.#active];
    if (previous) {
      previous.classList.remove('chunk--active');
      previous.textContent = previous.dataset.plain ?? previous.textContent;
    }

    this.#active = i;
    const para = this.#paras[i];
    this.#words = [];
    this.#weights = [];
    this.#lit = -1;
    if (!para) return;

    para.classList.add('chunk--active');
    this.#buildWords(para);
    this.#scrollToActive('smooth');
  }

  /**
   * Advance the word marker. `ratio` is the active chunk's audio position.
   */
  setProgress(ratio) {
    if (!this.#words.length) return;
    let target = -1;
    while (target + 1 < this.#weights.length && this.#weights[target + 1] <= ratio) target++;
    if (target === this.#lit) return;

    const from = Math.min(this.#lit, target);
    for (let i = Math.max(0, from); i < this.#words.length; i++) {
      this.#words[i].classList.toggle('w--lit', i <= target);
    }
    this.#lit = target;
  }

  /** Split the active paragraph into word spans and weight them by length. */
  #buildWords(para) {
    const text = para.textContent;
    para.dataset.plain = text;

    const tokens = text.split(/(\s+)/).filter(Boolean);
    const total = text.replace(/\s/g, '').length || 1;
    const frag = document.createDocumentFragment();
    let seen = 0;

    for (const token of tokens) {
      if (/^\s+$/.test(token)) {
        frag.append(token);
        continue;
      }
      const span = document.createElement('span');
      span.className = 'w';
      span.textContent = token;
      frag.append(span);
      this.#words.push(span);
      // Fraction of the chunk that has been spoken once this word is done.
      this.#weights.push(seen / total);
      seen += token.replace(/\s/g, '').length;
    }

    para.textContent = '';
    para.append(frag);
  }

  #scrollToActive(behavior) {
    if (!this.#follow) return;
    const para = this.#paras[this.#active];
    if (!para) return;
    const box = this.#root.getBoundingClientRect();
    const target = para.getBoundingClientRect();
    const delta = target.top - box.top - box.height * 0.38;
    this.#root.scrollBy({ top: delta, behavior });
  }
}
