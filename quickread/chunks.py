"""
Sentence-aware splitting for headless reads.

A deliberately simplified port of static/js/text.js. The JS version tiles a book
gaplessly by character offset, because the reading pane has to highlight the
exact span being spoken. Nothing here is drawn, so this keeps only the part that
affects how the speech sounds -- where the breaths land -- and returns plain
strings.

The two chunkers are allowed to disagree. This one is not trying to reproduce
the reader's passage boundaries; it is trying to hand Kokoro sentences it can
say naturally.
"""

from __future__ import annotations

import re

# Words that end in a period without ending a sentence. Same list as text.js.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "inc", "ltd",
    "co", "no", "vol", "ch", "fig", "p", "pp", "ave", "blvd", "rd", "mt", "ft",
    "sgt", "capt", "lt", "gen", "gov", "sen", "rep", "messrs", "al", "ca", "cf",
})

TERMINATORS = ".!?…"
CLOSERS = "\"'”’)]}"

_HORIZONTAL = re.compile(r"[^\S\n]+")
_AROUND_NEWLINE = re.compile(r" *\n *")
_BLANK_RUN = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    """Tidy text without flattening its line structure.

    Horizontal whitespace collapses; newlines survive, capped at one blank line.
    Paragraph breaks have to survive because they are the strongest signal of
    where a passage should end.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HORIZONTAL.sub(" ", text)
    text = _AROUND_NEWLINE.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def _count(text: str) -> int:
    return len(text.split())


def _ends_sentence(text: str, i: int) -> bool:
    """Whether the period at `i` genuinely ends a sentence.

    Rejects abbreviations ("Dr.") and single-letter initials ("J. R. R.").
    """
    start = i
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    word = text[start:i].lower()
    if not word:
        return True
    if len(word) == 1:
        return False
    return word not in ABBREVIATIONS


def _segments(text: str) -> list[tuple[int, int, bool]]:
    """Slice into (start, end, breaks_paragraph) spans covering the whole text.

    A span ends at a sentence terminator *or* a blank line. The second case
    matters for pasted text as much as for books: a bare heading or a list item
    carries no terminator, and gluing it to the next sentence makes Kokoro say
    the two in one breath.
    """
    segments: list[tuple[int, int, bool]] = []
    n = len(text)
    start = 0
    i = 0

    while i < n:
        char = text[i]

        if char.isspace():
            k = i
            while k < n and text[k].isspace():
                k += 1
            # A blank line closes the span; ordinary spacing does not.
            if "\n\n" in text[i:k]:
                segments.append((start, k, True))
                start = k
            i = k
            continue

        if char not in TERMINATORS or (char == "." and not _ends_sentence(text, i)):
            i += 1
            continue

        # Absorb repeated terminators ("?!") and any closing quotes or brackets.
        j = i + 1
        while j < n and text[j] in TERMINATORS:
            j += 1
        while j < n and text[j] in CLOSERS:
            j += 1

        # A sentence only ends if whitespace or the end of the text follows,
        # so "3.5" and "example.com" stay in one piece.
        if j < n and not text[j].isspace():
            i = j
            continue

        k = j
        while k < n and text[k].isspace():
            k += 1
        segments.append((start, k, "\n\n" in text[j:k] or k >= n))
        start = k
        i = k

    if start < n:
        segments.append((start, n, True))
    return segments


def _cap(text: str, segments, target: int):
    """Break spans far larger than the target into word-sized pieces.

    Without this, text with no punctuation at all -- transcripts, chat logs,
    OCR'd PDFs -- becomes one enormous span, and the first thing you hear is
    however long that takes to synthesize.
    """
    limit = max(1, int(target * 1.5))
    out: list[tuple[int, int, bool]] = []

    for seg_start, seg_end, breaks in segments:
        if _count(text[seg_start:seg_end]) <= limit:
            out.append((seg_start, seg_end, breaks))
            continue

        cursor = seg_start
        seen = 0
        i = seg_start
        while i < seg_end:
            if not text[i].isspace():
                i += 1
                continue
            # Count a word once, at the first whitespace that follows it.
            if i > seg_start and text[i - 1].isspace():
                i += 1
                continue
            seen += 1
            if seen < target:
                i += 1
                continue
            k = i
            while k < seg_end and text[k].isspace():
                k += 1
            out.append((cursor, k, False))
            cursor = k
            seen = 0
            i = k

        if cursor < seg_end:
            out.append((cursor, seg_end, breaks))

    return out


def split(text: str, words: int = 110, first_words: int = 35) -> list[str]:
    """Split text into passages of roughly `words`, breaking on sentence ends.

    The first passage targets `first_words` instead, because it is the only one
    you wait on: everything after it synthesizes while the previous one plays.
    """
    text = normalize(text)
    if not text:
        return []

    first_target = max(1, first_words)
    rest_target = max(1, words)
    segments = _cap(text, _segments(text), min(first_target, rest_target))

    chunks: list[str] = []
    start = 0
    count = 0

    for seg_start, seg_end, breaks in segments:
        count += _count(text[seg_start:seg_end])
        target = first_target if not chunks else rest_target
        if count >= target or breaks or seg_end >= len(text):
            body = text[start:seg_end].strip()
            if body:
                chunks.append(body)
            start = seg_end
            count = 0

    tail = text[start:].strip()
    if tail:
        chunks.append(tail)

    return chunks
