"""Deterministic passage chunking with page provenance. Standard library only.

Splits a `DocumentRecord`'s exact normalized text into ordered passages:
greedy paragraph packing within a character budget, hard character
windows as fallback for oversized paragraphs, paragraph-snapped overlap
between passages. PDF `--- page N ---` markers (full-line matches only)
map passages to page ranges; anything else yields unknown pages rather
than invented ones. No tokenizers, concurrency, caching, or stores.
See the chunking section of ``docs/source-ingestion-contract.md``.

Chunking measures text; it establishes nothing about authenticity,
currency, completeness, or applicability.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from backend.app.sources import DocumentRecord

__all__ = [
    "CHUNKING_VERSION",
    "ChunkConfig",
    "ChunkingError",
    "Passage",
    "chunk_document",
]

# Algorithm identity. Bump when the algorithm or its defaults change in
# a way that moves passage boundaries; the version is embedded in every
# passage id so mixed-version collections stay distinguishable.
CHUNKING_VERSION = "taxguard-chunks-v1"

# A line is a page marker only on exact full-line match, so ordinary
# legal text can never be mistaken for one.
_PAGE_MARKER_RE = re.compile(r"^--- page (\d+) ---$", re.MULTILINE)


class ChunkingError(ValueError):
    """Invalid chunking input or configuration. Distinct from valid
    inputs that simply yield no passages (empty text returns ())."""


def _require_int(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ChunkingError(f"{name} must be an integer, got {value!r}")
    return value


@dataclass(frozen=True)
class ChunkConfig:
    """Character-budget configuration (explicitly characters, not
    tokens — no tokenizer dependency). Defaults (~1000 characters with
    ~10% overlap) suit a screen of legal text while keeping clause
    continuity across boundaries."""

    max_chars: int = 1000
    overlap_chars: int = 100

    def __post_init__(self) -> None:
        _require_int("max_chars", self.max_chars)
        _require_int("overlap_chars", self.overlap_chars)
        if self.max_chars <= 0:
            raise ChunkingError("max_chars must be positive")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ChunkingError("overlap_chars must satisfy 0 <= overlap_chars < max_chars")


@dataclass(frozen=True)
class Passage:
    """One ordered passage. `text` is an exact slice of the source:
    ``text == source[char_start:char_end]`` with end-exclusive offsets,
    markers included. Page numbers are 1-based PDF pages; `(None, None)`
    means page provenance is unavailable, never guessed."""

    passage_id: str
    document_id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    parser_id: str
    chunking_version: str
    max_chars: int
    overlap_chars: int

    def __post_init__(self) -> None:
        for name in ("passage_id", "document_id", "text", "parser_id", "chunking_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ChunkingError(f"{name} must be a non-empty string")
        _require_int("chunk_index", self.chunk_index)
        _require_int("char_start", self.char_start)
        _require_int("char_end", self.char_end)
        _require_int("max_chars", self.max_chars)
        _require_int("overlap_chars", self.overlap_chars)
        if self.chunk_index < 0 or self.char_start < 0 or self.char_end <= self.char_start:
            raise ChunkingError("invalid chunk index or character offsets")
        if len(self.text) > self.max_chars:
            raise ChunkingError("passage exceeds max_chars")
        for name in ("page_start", "page_end"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise ChunkingError(f"{name} must be a positive page number or None")
        if self.page_start is None or self.page_end is None:
            if (self.page_start, self.page_end) != (None, None):
                raise ChunkingError("page numbers must both be set or both be unknown")
        elif self.page_end < self.page_start:
            raise ChunkingError("page_end must not precede page_start")


def _page_markers(text: str) -> list[tuple[int, int]] | None:
    """(offset, page) pairs in document order, or None when markers are
    absent or not strictly increasing (unreliable mapping)."""
    markers = [(m.start(), int(m.group(1))) for m in _PAGE_MARKER_RE.finditer(text)]
    if not markers:
        return None
    if any(first[1] >= second[1] for first, second in zip(markers, markers[1:])):
        return None
    return markers


def _page_range(markers: list[tuple[int, int]] | None, start: int, end: int) -> tuple[int | None, int | None]:
    if not markers:
        return (None, None)
    started = [page for offset, page in markers if offset <= start]
    if not started:
        return (None, None)
    inside = [page for offset, page in markers if start < offset < end]
    return (started[-1], inside[-1] if inside else started[-1])


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Non-blank paragraph spans; separators stay in the gaps so joined
    spans reproduce the source exactly."""
    return [(m.start(), m.end()) for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text)]


def chunk_document(
    document: DocumentRecord,
    config: ChunkConfig | None = None,
) -> tuple[Passage, ...]:
    """Split `document.normalized_text` into passages. Paragraphs that
    fit are never broken; oversized paragraphs fall back to hard
    character windows; consecutive passages overlap by paragraph-snapped
    `overlap_chars` (minimum progress of one paragraph guarantees
    termination). The document is never mutated. Identical inputs and
    configuration always yield identical passages."""
    if not isinstance(document, DocumentRecord):
        raise ChunkingError("document must be a DocumentRecord")
    if config is not None and not isinstance(config, ChunkConfig):
        raise ChunkingError("config must be a ChunkConfig or None")
    resolved = config or ChunkConfig()
    text = document.normalized_text
    if not text.strip():
        return ()

    markers = _page_markers(text)
    paragraphs = _paragraph_spans(text)
    if not paragraphs:
        return ()

    # Candidate spans: greedy paragraph packing, with hard character
    # windows for single oversized paragraphs. Starts are paragraph
    # starts (or hard-window offsets); every span respects max_chars.
    spans: list[tuple[int, int]] = []
    limit, overlap = resolved.max_chars, resolved.overlap_chars
    index = 0
    count = len(paragraphs)
    while index < count:
        start, first_end = paragraphs[index][0], paragraphs[index][1]
        if first_end - start > limit:
            step = limit - overlap  # >= 1: overlap < max_chars is enforced
            offset = start
            while offset < first_end:
                spans.append((offset, min(offset + limit, first_end)))
                offset += step
            end, cursor = first_end, index + 1
        else:
            end, cursor = first_end, index + 1
            while cursor < count and paragraphs[cursor][1] - start <= limit:
                end = paragraphs[cursor][1]
                cursor += 1
            spans.append((start, end))
        if overlap == 0 or cursor == count:
            # No overlap, or every remaining paragraph is packed: the
            # tail passage already covers the text end, so stop instead
            # of emitting subsumed duplicates.
            index = cursor
        else:
            # Next passage starts at the first packed paragraph starting
            # after the overlap point (effective overlap <= configured);
            # index always advances, so the loop terminates.
            overlap_at = end - overlap
            nxt = index + 1
            while nxt < cursor and paragraphs[nxt][0] <= overlap_at:
                nxt += 1
            index = nxt

    passages: list[Passage] = []
    for chunk_index, (start, end) in enumerate(spans):
        body = text[start:end]
        page_start, page_end = _page_range(markers, start, end)
        digest = hashlib.sha256(
            f"{document.document_id}\n{chunk_index}\n{CHUNKING_VERSION}\n{body}".encode("utf-8")
        ).hexdigest()[:12]
        passages.append(
            Passage(
                passage_id=f"{document.document_id}:c{chunk_index:04d}:{digest}",
                document_id=document.document_id,
                chunk_index=chunk_index,
                text=body,
                char_start=start,
                char_end=end,
                page_start=page_start,
                page_end=page_end,
                parser_id=document.parser_id,
                chunking_version=CHUNKING_VERSION,
                max_chars=resolved.max_chars,
                overlap_chars=resolved.overlap_chars,
            )
        )
    return tuple(passages)
