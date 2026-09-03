/**
 * Turn a dropped file into readable text.
 *
 * Every extractor emits real newlines. Chapter detection depends on line
 * structure surviving, so nothing here may flatten whitespace.
 */

import { normalize } from './text.js';

/** Block-level tags that should force a line break when flattening HTML. */
const BLOCKS = 'p,div,br,h1,h2,h3,h4,h5,h6,li,tr,blockquote,section,article,pre';

/**
 * @param {File} file
 * @returns {Promise<{title:string, text:string, kind:string}>}
 */
export async function extract(file) {
  const kind = file.name.split('.').pop().toLowerCase();
  const title = file.name.replace(/\.[^.]+$/, '');

  let raw;
  if (kind === 'txt') raw = await file.text();
  else if (kind === 'pdf') raw = await fromPDF(file);
  else if (kind === 'epub') raw = await fromEPUB(file);
  else throw new Error(`Can't read .${kind} files — try PDF, EPUB, or TXT.`);

  const text = normalize(raw);
  if (!text) {
    throw new Error(
      kind === 'pdf'
        ? 'No text found. This looks like a scanned PDF — the pages are images, not text.'
        : 'No text found in that file.'
    );
  }
  return { title, text, kind };
}

/**
 * pdf.js exposes `hasEOL` per text item; using it keeps the line structure the
 * original extractor threw away.
 */
async function fromPDF(file) {
  const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
  const pages = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const content = await pdf.getPage(i).then((p) => p.getTextContent());
    pages.push(content.items.map((it) => it.str + (it.hasEOL ? '\n' : '')).join(''));
  }
  return pages.join('\n\n');
}

/**
 * Read an EPUB in spine order.
 *
 * Sorting filenames alphabetically (as the original did) puts chapter 10 before
 * chapter 2 in a great many books, so the OPF spine is parsed instead, with an
 * alphabetical fallback for malformed archives.
 */
async function fromEPUB(file) {
  const zip = await JSZip.loadAsync(await file.arrayBuffer());
  const names = await spineOrder(zip);
  const parts = [];

  for (const name of names) {
    const entry = zip.files[name];
    if (!entry) continue;
    parts.push(htmlToText(await entry.async('string')));
  }
  return parts.join('\n\n');
}

/** Resolve the reading order from META-INF/container.xml -> OPF -> spine. */
async function spineOrder(zip) {
  const fallback = () =>
    Object.keys(zip.files)
      .filter((n) => /\.x?html?$/i.test(n))
      .sort();

  try {
    const container = await zip.files['META-INF/container.xml']?.async('string');
    if (!container) return fallback();

    const opfPath = new DOMParser()
      .parseFromString(container, 'application/xml')
      .querySelector('rootfile')
      ?.getAttribute('full-path');
    if (!opfPath) return fallback();

    const opf = new DOMParser().parseFromString(
      await zip.files[opfPath].async('string'),
      'application/xml'
    );

    // Manifest ids -> hrefs, resolved relative to the OPF's own directory.
    const base = opfPath.includes('/') ? opfPath.replace(/\/[^/]*$/, '/') : '';
    const hrefById = new Map(
      [...opf.querySelectorAll('manifest > item')].map((it) => [
        it.getAttribute('id'),
        resolvePath(base, it.getAttribute('href')),
      ])
    );

    const ordered = [...opf.querySelectorAll('spine > itemref')]
      .map((ref) => hrefById.get(ref.getAttribute('idref')))
      .filter((href) => href && zip.files[href]);

    return ordered.length ? ordered : fallback();
  } catch {
    return fallback();
  }
}

/** Collapse "OEBPS/../Text/ch1.xhtml" style relative hrefs. */
function resolvePath(base, href) {
  if (!href) return href;
  const stack = [];
  for (const part of (base + href).split('/')) {
    if (part === '..') stack.pop();
    else if (part !== '.' && part !== '') stack.push(part);
  }
  return stack.join('/');
}

/**
 * Flatten an XHTML chapter to text.
 *
 * `innerText` needs layout and returns nothing for a detached DOMParser
 * document, so block elements get an explicit newline and `textContent` does
 * the rest.
 */
function htmlToText(source) {
  const doc = new DOMParser().parseFromString(source, 'text/html');
  doc.querySelectorAll('script,style,nav,head').forEach((el) => el.remove());
  doc.querySelectorAll(BLOCKS).forEach((el) => el.append(document.createTextNode('\n')));
  return doc.body?.textContent ?? '';
}
