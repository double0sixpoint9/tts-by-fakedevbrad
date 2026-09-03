/** Thin client for the local Kokoro server. */

export const SERVER = location.origin.startsWith('http')
  ? location.origin
  : 'http://localhost:5050';

/**
 * Ask the server how far along startup is.
 *
 * The socket binds before the model loads, so this reports a phase
 * (starting / downloading / loading / ready / error) rather than a bare
 * up-or-down, letting the UI show warm-up and download progress.
 *
 * @returns {Promise<{ok:boolean, phase:string, detail:string,
 *                    percent:?number, voices?:Object}>}
 */
export async function health(timeoutMs = 4000) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  try {
    const res = await fetch(`${SERVER}/health`, { signal: abort.signal });
    if (!res.ok) return { ok: false, phase: 'down', detail: `HTTP ${res.status}`, percent: null };
    const body = await res.json();
    return {
      ok: Boolean(body.ok),
      phase: body.phase ?? 'ready',
      detail: body.detail ?? '',
      percent: body.percent ?? null,
      voices: body.voices,
    };
  } catch (err) {
    return {
      ok: false,
      phase: 'down',
      detail: err.name === 'AbortError' ? 'Server not responding' : 'Server offline',
      percent: null,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Tell the server this window is going away, so it can stop instead of
 * lingering with the model loaded. Fired on pagehide; the server waits out a
 * grace period first, so a reload doesn't kill it.
 */
export function sayGoodbye() {
  try {
    navigator.sendBeacon(`${SERVER}/bye`);
  } catch {
    /* sendBeacon is unavailable or blocked — the idle timeout still applies */
  }
}

/**
 * Synthesise one chunk of text.
 * @returns {Promise<Blob>} a WAV blob
 */
export async function speak(text, { voice, speed, signal } = {}) {
  const res = await fetch(`${SERVER}/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice, speed }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`Speech failed (HTTP ${res.status}) — check the server console.`);
  }
  return res.blob();
}
