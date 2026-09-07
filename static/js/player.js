/**
 * Playback engine: walks the chunk list, keeps one chunk of audio preloaded,
 * and reports position so the reader can follow along.
 *
 * Any seek, stop, or settings change bumps a session counter; in-flight fetches
 * compare against it and drop their results rather than hijacking playback.
 */

import { speak } from './tts.js';

export class Player {
  #chunks = [];
  #index = 0;
  #session = 0;
  #audio = null;
  #url = null;
  #preload = null; // {index, promise, session}
  #inflight = null; // AbortController for the chunk being generated now
  #state = 'idle'; // idle | generating | playing | paused | done
  #on;
  #settings;

  /**
   * @param {object} opts
   * @param {() => {voice:string, speed:number}} opts.settings current voice/speed
   * @param {object} opts.on  callbacks: index, progress, state, error
   */
  constructor({ settings, on }) {
    this.#settings = settings;
    this.#on = on;
  }

  get index() { return this.#index; }
  get state() { return this.#state; }
  get count() { return this.#chunks.length; }
  get isActive() { return this.#state === 'playing' || this.#state === 'generating'; }

  /** Replace the chunk list, optionally keeping the current position. */
  load(chunks, startIndex = 0) {
    this.stop();
    this.#chunks = chunks;
    this.#setIndex(Math.min(startIndex, Math.max(0, chunks.length - 1)));
  }

  /** Discard preloaded audio — call when voice or speed changes. */
  invalidateCache() {
    this.#preload = null;
  }

  play() {
    if (!this.#chunks.length) return;
    if (this.#state === 'paused' && this.#audio) {
      this.#audio.play();
      this.#setState('playing');
      return;
    }
    if (this.isActive) return;
    this.#run(this.#index);
  }

  pause() {
    if (this.#state === 'generating') {
      // Nothing to pause yet — abandon the generation instead.
      this.#invalidate();
      this.#setState('paused');
      return;
    }
    if (!this.#audio || this.#state !== 'playing') return;
    this.#audio.pause();
    this.#setState('paused');
  }

  toggle() {
    this.isActive ? this.pause() : this.play();
  }

  stop() {
    this.#invalidate();
    this.#setState('idle');
    this.#on.progress?.(0);
  }

  /** Jump to a chunk, resuming playback if it was already running. */
  seek(index, { autoplay = this.isActive } = {}) {
    if (!this.#chunks.length) return;
    const wasActive = this.isActive;
    this.#invalidate();
    this.#setIndex(Math.max(0, Math.min(index, this.#chunks.length - 1)));
    if (autoplay || wasActive) this.#run(this.#index);
    else this.#setState('paused');
  }

  skip(delta) {
    this.seek(this.#index + delta);
  }

  // ── internals ──────────────────────────────────────────────────────────────

  /** Tear down current audio and invalidate anything still in flight. */
  #invalidate() {
    this.#session++;
    this.#inflight?.abort();
    this.#inflight = null;
    this.#preload = null;
    if (this.#audio) {
      this.#audio.pause();
      this.#audio.src = '';
      this.#audio = null;
    }
    this.#releaseUrl();
  }

  #releaseUrl() {
    if (this.#url) {
      URL.revokeObjectURL(this.#url);
      this.#url = null;
    }
  }

  #setState(state) {
    this.#state = state;
    this.#on.state?.(state);
  }

  #setIndex(index) {
    this.#index = index;
    this.#on.index?.(index);
    this.#on.progress?.(0);
  }

  #fetchChunk(index, session) {
    const { voice, speed } = this.#settings();
    const controller = new AbortController();
    const promise = speak(this.#chunks[index].text, { voice, speed, signal: controller.signal });
    return { controller, promise, session };
  }

  async #run(index) {
    if (index >= this.#chunks.length) {
      this.#setState('done');
      this.#on.progress?.(1);
      return;
    }

    const session = this.#session;
    this.#setIndex(index);

    let blob;
    try {
      if (this.#preload?.index === index && this.#preload.session === session) {
        this.#setState('generating');
        blob = await this.#preload.promise;
      } else {
        this.#setState('generating');
        const job = this.#fetchChunk(index, session);
        this.#inflight = job.controller;
        blob = await job.promise;
      }
    } catch (err) {
      if (err.name === 'AbortError' || session !== this.#session) return;
      if (err.skippable) {
        // Nothing to say for this passage. Read on: losing the rest of a book
        // to a horizontal rule is much the worse failure.
        this.#preload = null;
        this.#inflight = null;
        this.#on.progress?.(1);
        this.#run(index + 1);
        return;
      }
      this.#setState('idle');
      this.#on.error?.(err);
      return;
    }
    this.#preload = null;
    this.#inflight = null;
    if (session !== this.#session) return;

    this.#releaseUrl();
    this.#url = URL.createObjectURL(blob);
    const audio = new Audio(this.#url);
    this.#audio = audio;

    audio.addEventListener('timeupdate', () => {
      if (session !== this.#session || !audio.duration) return;
      this.#on.progress?.(audio.currentTime / audio.duration);
    });

    audio.addEventListener('ended', () => {
      if (session !== this.#session) return;
      this.#on.progress?.(1);
      this.#run(index + 1);
    });

    audio.addEventListener('error', () => {
      if (session !== this.#session) return;
      this.#setState('idle');
      this.#on.error?.(new Error('That audio failed to play.'));
    });

    try {
      await audio.play();
    } catch (err) {
      if (session !== this.#session) return;
      this.#setState('idle');
      this.#on.error?.(err);
      return;
    }
    if (session !== this.#session) return;
    this.#setState('playing');

    // Warm the next chunk while this one plays.
    if (index + 1 < this.#chunks.length) {
      const job = this.#fetchChunk(index + 1, session);
      job.promise.catch(() => {});
      this.#preload = { index: index + 1, promise: job.promise, session };
    }
  }
}
