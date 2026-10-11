"""Official-source registry and local ingestion foundation.

Registers source *metadata* (never proof of authenticity) and ingests
explicitly supplied local files (UTF-8 text, HTML, text-based PDF) into
normalized, provenance-preserving document records. Standard library
plus pypdf for PDF text extraction. No network access, no database, no
FastAPI, no LLM, no retrieval, no embeddings. See
``docs/source-ingestion-contract.md`` for the full contract.

Trust boundary: registration ≠ authenticity, parse success ≠ legal
correctness, content hash ≠ authenticity, presence ≠ completeness.
Unknown metadata stays unknown (empty string), never fabricated.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

__all__ = [
    "DEFAULT_MAX_BYTES",
    "PARSER_ID",
    "PDF_MAX_PAGES",
    "DocumentRecord",
    "IngestionError",
    "SourceError",
    "SourceRecord",
    "SourceRegistry",
    "ingest_file",
]

# Parser/version identifier stamped on every ingested record.
PARSER_ID = "taxguard-sources-v1"

# Default bound on input size. Prevents unbounded memory use; override
# per call when a legitimate larger file is expected.
DEFAULT_MAX_BYTES = 5_000_000

# Default bound on PDF page count. Far above plausible single-document
# legal PDFs (hundreds of pages) while bounding CPU and memory; override
# is not exposed per call — unusually large documents need explicit
# handling in a later phase.
PDF_MAX_PAGES = 2000

_SUPPORTED_FORMATS = ("txt", "html", "pdf")
_SUFFIX_FORMATS = {".txt": "txt", ".html": "html", ".htm": "html", ".pdf": "pdf"}

_SOURCE_VERIFICATION_STATES = ("unverified", "verified", "disputed")
_EXTRACTION_STATUSES = ("ok", "empty")


class SourceError(ValueError):
    """Malformed source record or registry violation (duplicates,
    unknown ids, wrong types)."""


class IngestionError(ValueError):
    """Local file ingestion failure: missing/unreadable file, empty
    input, oversize input, invalid encoding, or unsupported format.
    Messages name the file, never its contents."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _optional_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{name} must be a string")
    return value


@dataclass(frozen=True)
class SourceRecord:
    """Recorded source metadata. Every descriptive field except
    `source_id`/`title` may be empty, meaning unknown — never guessed.
    `verification` is a recorded claim, not proof: only "unverified" is
    the default, and any other state must carry a note stating its basis.
    A government-domain URL or a local file never auto-verifies."""

    source_id: str
    title: str
    publisher: str = ""
    origin: str = ""
    source_type: str = ""
    jurisdiction: str = ""
    publication: str = ""
    acquired_at: str = ""
    verification: str = "unverified"
    note: str = ""

    def __post_init__(self) -> None:
        _require_label("source_id", self.source_id)
        _require_label("title", self.title)
        for name in (
            "publisher",
            "origin",
            "source_type",
            "jurisdiction",
            "publication",
            "acquired_at",
            "note",
        ):
            _optional_text(name, getattr(self, name))
        if self.verification not in _SOURCE_VERIFICATION_STATES:
            raise SourceError(f"unknown verification state {self.verification!r}")
        if self.verification != "unverified" and not self.note.strip():
            raise SourceError("a verified or disputed source must state its basis in note")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping preserving unknown (empty) fields."""
        return {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "origin": self.origin,
            "source_type": self.source_type,
            "jurisdiction": self.jurisdiction,
            "publication": self.publication,
            "acquired_at": self.acquired_at,
            "verification": self.verification,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRecord:
        """Rebuild a record, rejecting malformed input instead of coercing."""
        if not isinstance(data, dict):
            raise SourceError("source must be a mapping")
        values: dict[str, Any] = {}
        for key in (
            "source_id",
            "title",
            "publisher",
            "origin",
            "source_type",
            "jurisdiction",
            "publication",
            "acquired_at",
            "verification",
            "note",
        ):
            value = data.get(key, "" if key not in ("source_id", "title", "verification") else None)
            if key in ("source_id", "title") and not isinstance(value, str):
                raise SourceError(f"source.{key} must be a string")
            if key == "verification" and value is None:
                value = "unverified"
            if not isinstance(value, str):
                raise SourceError(f"source.{key} must be a string")
            values[key] = value
        return cls(**values)  # type: ignore[arg-type]


class SourceRegistry:
    """Explicit registry of source metadata. One instance per use; never
    shared implicitly. Duplicate ids raise instead of overwriting."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceRecord] = {}

    def register(self, source: SourceRecord) -> None:
        if not isinstance(source, SourceRecord):
            raise SourceError("source must be a SourceRecord")
        if source.source_id in self._sources:
            raise SourceError(f"source {source.source_id!r} is already registered")
        self._sources[source.source_id] = source

    def get(self, source_id: str) -> SourceRecord | None:
        """Exact-id lookup. None means unregistered — never a placeholder."""
        return self._sources.get(source_id)


class _TextExtractor(HTMLParser):
    """Deterministic readable-text extraction. Skips script/style content,
    never executes scripts or fetches resources. Block tags start a new
    paragraph so the document cannot collapse into one block."""

    _BLOCK_TAGS = frozenset(
        {"p", "div", "section", "article", "header", "footer", "main", "aside",
         "nav", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
         "table", "tr", "blockquote", "pre", "hr", "title"}
    )
    _SKIP_TAGS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[str] = []
        self._current: list[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        if text:
            self._blocks.append(text)
        self._current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "br":
            self._flush()
        elif tag in self._BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._current.append(data)

    def text(self) -> str:
        self._flush()
        return "\n\n".join(self._blocks)


def _normalize_text(raw: str) -> str:
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def _extract_pdf_text(data: bytes, display: str) -> tuple[str, int, int, str]:
    """Extract text from PDF bytes in deterministic page order.

    Returns ``(normalized_text, page_count, text_pages, lib_version)``.
    Page bodies are joined with ``--- page N ---`` markers so boundaries
    survive; per-page text is stripped, and the whole is normalized as
    usual. Rejects encrypted, empty, oversize-page-count, unreadable,
    and text-free PDFs with typed errors. Never attempts passwords.
    """
    try:
        import pypdf
        from pypdf import PdfReader
    except ImportError:
        raise IngestionError(f"PDF support requires the 'pypdf' package to parse {display!r}") from None
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise IngestionError(f"file {display!r} is not a readable PDF ({type(exc).__name__})") from None
    if reader.is_encrypted:
        raise IngestionError(f"file {display!r} is encrypted and requires a password")
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise IngestionError(f"file {display!r} has unreadable pages ({type(exc).__name__})") from None
    if page_count == 0:
        raise IngestionError(f"file {display!r} is an empty PDF with no pages")
    if page_count > PDF_MAX_PAGES:
        raise IngestionError(
            f"file {display!r} has {page_count} pages, exceeding the limit of {PDF_MAX_PAGES}"
        )
    parts: list[str] = []
    text_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            body = (page.extract_text() or "").strip()
        except Exception as exc:
            raise IngestionError(
                f"file {display!r} has unreadable text on page {index} ({type(exc).__name__})"
            ) from None
        if body:
            text_pages += 1
        parts.append(f"--- page {index} ---\n{body}")
    if text_pages == 0:
        raise IngestionError(
            f"file {display!r} contains no extractable text; "
            "OCR or an alternative source may be needed"
        )
    return _normalize_text("\n\n".join(parts)), page_count, text_pages, pypdf.__version__


@dataclass(frozen=True)
class DocumentRecord:
    """Normalized ingested document. Identity (`document_id`) derives from
    the registered source id plus the SHA-256 of the actual input bytes —
    never from the filename, which is metadata only. `source_verification`
    snapshots the source's verification state at ingest time."""

    document_id: str
    source_id: str
    filename: str
    format: str
    parser_id: str
    content_hash: str
    size_bytes: int
    normalized_text: str
    extraction_status: str
    source_verification: str

    def __post_init__(self) -> None:
        _require_label("document_id", self.document_id)
        _require_label("source_id", self.source_id)
        _require_label("filename", self.filename)
        if self.format not in _SUPPORTED_FORMATS:
            raise SourceError(f"unsupported format {self.format!r}")
        _require_label("parser_id", self.parser_id)
        _require_label("content_hash", self.content_hash)
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise SourceError("size_bytes must be a non-negative integer")
        if not isinstance(self.normalized_text, str):
            raise SourceError("normalized_text must be a string")
        if self.extraction_status not in _EXTRACTION_STATUSES:
            raise SourceError(f"unknown extraction status {self.extraction_status!r}")
        if self.source_verification not in _SOURCE_VERIFICATION_STATES:
            raise SourceError(f"unknown verification state {self.source_verification!r}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping. Round-trips through from_dict."""
        return {
            "document_id": self.document_id,
            "source_id": self.source_id,
            "filename": self.filename,
            "format": self.format,
            "parser_id": self.parser_id,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "normalized_text": self.normalized_text,
            "extraction_status": self.extraction_status,
            "source_verification": self.source_verification,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentRecord:
        """Rebuild a record. Preserves recorded identity; re-ingestion of
        the original bytes (not deserialization) is what re-authenticates
        content, so hashes are carried, never recomputed here."""
        if not isinstance(data, dict):
            raise SourceError("document must be a mapping")
        text = data.get("normalized_text")
        size = data.get("size_bytes")
        if not isinstance(text, str):
            raise SourceError("document.normalized_text must be a string")
        if not isinstance(size, int) or isinstance(size, bool):
            raise SourceError("document.size_bytes must be an integer")
        values: dict[str, Any] = {"normalized_text": text, "size_bytes": size}
        for key in (
            "document_id",
            "source_id",
            "filename",
            "format",
            "parser_id",
            "content_hash",
            "extraction_status",
            "source_verification",
        ):
            value = data.get(key)
            if not isinstance(value, str):
                raise SourceError(f"document.{key} must be a string")
            values[key] = value
        return cls(**values)  # type: ignore[arg-type]


def _read_input_bytes(path: str | os.PathLike[str], max_bytes: int, display: str) -> bytes:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise IngestionError("max_bytes must be a positive integer")
    try:
        size = os.path.getsize(path)
    except OSError:
        raise IngestionError(f"cannot read file {display!r}") from None
    if size > max_bytes:
        raise IngestionError(f"file {display!r} exceeds size limit of {max_bytes} bytes")
    if size == 0:
        raise IngestionError(f"file {display!r} is empty")
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        raise IngestionError(f"cannot read file {display!r}") from None
    if len(data) > max_bytes:
        raise IngestionError(f"file {display!r} exceeds size limit of {max_bytes} bytes")
    if not data:
        raise IngestionError(f"file {display!r} is empty")
    return data


def ingest_file(
    sources: SourceRegistry,
    source_id: str,
    path: str | os.PathLike[str],
    *,
    format: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> DocumentRecord:
    """Ingest one explicitly supplied local file for a registered source.

    Pure local file I/O: reads only `path`, writes nothing, fetches
    nothing. `format` ("txt"/"html"/"pdf") overrides suffix inference; an
    unknown suffix or format raises instead of guessing. PDF text comes
    from the original bytes via pypdf while the hash, identity, and
    provenance still describe those bytes — never the extracted text.
    Returns a normalized record; the original file is never modified.
    """
    if not isinstance(sources, SourceRegistry):
        raise IngestionError("sources must be a SourceRegistry")
    if not isinstance(source_id, str) or not source_id.strip():
        raise IngestionError("source_id must be a non-empty string")
    source = sources.get(source_id)
    if source is None:
        raise IngestionError(f"unknown source {source_id!r}")
    if not isinstance(path, (str, os.PathLike)):
        raise IngestionError("path must be a string or path-like object")
    display = os.path.basename(os.fspath(path)) or os.fspath(path)
    if not os.path.isfile(os.fspath(path)):
        raise IngestionError(f"cannot read file {display!r}")

    if format is None:
        _, suffix = os.path.splitext(os.fspath(path))
        detected = _SUFFIX_FORMATS.get(suffix.lower())
        if detected is None:
            raise IngestionError(f"unsupported file format for {display!r}")
        resolved = detected
    else:
        if format not in _SUPPORTED_FORMATS:
            raise IngestionError(f"unsupported format {format!r}")
        resolved = format

    raw = _read_input_bytes(path, max_bytes, display)
    parser_id = f"{PARSER_ID}:{resolved}"
    if resolved == "pdf":
        normalized, _, _, lib_version = _extract_pdf_text(raw, display)
        parser_id = f"{parser_id}:pypdf-{lib_version}"
    else:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestionError(f"file {display!r} is not valid UTF-8 ({exc.reason})") from None
        if resolved == "html":
            extractor = _TextExtractor()
            extractor.feed(decoded)
            normalized = extractor.text()
        else:
            normalized = _normalize_text(decoded)

    content_hash = hashlib.sha256(raw).hexdigest()
    return DocumentRecord(
        document_id=f"{source.source_id}:{content_hash[:32]}",
        source_id=source.source_id,
        filename=display,
        format=resolved,
        parser_id=parser_id,
        content_hash=content_hash,
        size_bytes=len(raw),
        normalized_text=normalized,
        extraction_status="ok" if normalized else "empty",
        source_verification=source.verification,
    )
