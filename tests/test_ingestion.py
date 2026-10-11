"""Ingestion tests using SYNTHETIC local files only.

All inputs are written to pytest tmp_path; no network, real corpus, or
external service is involved. Originals must remain unmodified.
"""

import hashlib
from pathlib import Path

import pytest

from backend.app.sources import (
    DocumentRecord,
    IngestionError,
    SourceError,
    SourceRecord,
    SourceRegistry,
    ingest_file,
)


def registry_with(source_id: str = "syn-source-1") -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(SourceRecord(
        source_id=source_id,
        title="Synthetic test source",
        publisher="Synthetic Test Board",
        note="synthetic fixture; authenticity not established",
    ))
    return registry


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_unknown_source_rejected(tmp_path: Path) -> None:
    target = write(tmp_path / "note.txt", b"synthetic text")
    with pytest.raises(IngestionError, match="unknown source"):
        ingest_file(SourceRegistry(), "syn-absent", target)
    with pytest.raises(IngestionError):
        ingest_file("not-a-registry", "syn-source-1", target)  # type: ignore[arg-type]
    with pytest.raises(IngestionError):
        ingest_file(registry_with(), "  ", target)
    with pytest.raises(IngestionError):
        ingest_file(registry_with(), "syn-source-1", 42)  # type: ignore[arg-type]


def test_plain_text_ingestion(tmp_path: Path) -> None:
    raw = b"Section 1: synthetic provision.\r\n\r\nSection 2: more synthetic text.\r\n"
    target = write(tmp_path / "provision.txt", raw)
    record = ingest_file(registry_with(), "syn-source-1", target)

    assert record.format == "txt"
    assert record.parser_id == "taxguard-sources-v1:txt"
    assert record.filename == "provision.txt"
    assert record.source_id == "syn-source-1"
    assert record.source_verification == "unverified"
    assert record.size_bytes == len(raw)
    assert record.content_hash == hashlib.sha256(raw).hexdigest()
    assert record.document_id == f"syn-source-1:{record.content_hash[:32]}"
    assert record.extraction_status == "ok"
    assert record.normalized_text == (
        "Section 1: synthetic provision.\n\nSection 2: more synthetic text."
    )


def test_html_extraction(tmp_path: Path) -> None:
    raw = (
        "<html><head><title>Synthetic doc</title><style>p{color:red}</style></head>"
        "<body><script>alert('x')</script>"
        "<h1>Heading</h1><p>First &amp; second.</p><p>Third.</p></body></html>"
    ).encode()
    record = ingest_file(registry_with(), "syn-source-1", write(tmp_path / "doc.html", raw))

    assert record.format == "html"
    assert record.extraction_status == "ok"
    assert record.normalized_text == "Synthetic doc\n\nHeading\n\nFirst & second.\n\nThird."
    assert "alert" not in record.normalized_text
    assert "color" not in record.normalized_text


def test_empty_input_rejected(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="empty"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "empty.txt", b""))


def test_invalid_utf8_rejected(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="UTF-8"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "bad.txt", b"\xff\xfe\x00x"))


def test_unsupported_format_rejected(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="unsupported"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "scan.dat", b"%PDF-1.4 synthetic"))
    with pytest.raises(IngestionError, match="unsupported format"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "note.txt", b"x"),
                    format="pdff")
    with pytest.raises(IngestionError, match="unsupported"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "noext", b"synthetic"))
    # .pdf is now a supported suffix: non-PDF bytes fail at parse time instead.
    with pytest.raises(IngestionError, match="readable PDF"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "scan.pdf", b"%PDF-1.4 synthetic"))
    with pytest.raises(IngestionError, match="readable PDF"):
        ingest_file(registry_with(), "syn-source-1", write(tmp_path / "note.txt", b"x"),
                    format="pdf")


def test_missing_and_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="cannot read"):
        ingest_file(registry_with(), "syn-source-1", tmp_path / "absent.txt")
    with pytest.raises(IngestionError, match="cannot read"):
        ingest_file(registry_with(), "syn-source-1", tmp_path)


def test_size_limit(tmp_path: Path) -> None:
    raw = b"abcdefghij"  # 10 bytes
    target = write(tmp_path / "sized.txt", raw)
    with pytest.raises(IngestionError, match="size limit"):
        ingest_file(registry_with(), "syn-source-1", target, max_bytes=9)
    assert ingest_file(registry_with(), "syn-source-1", target, max_bytes=10).size_bytes == 10
    with pytest.raises(IngestionError):
        ingest_file(registry_with(), "syn-source-1", target, max_bytes=0)


def test_hash_and_determinism(tmp_path: Path) -> None:
    first = ingest_file(registry_with(), "syn-source-1", write(tmp_path / "a.txt", b"synthetic v1"))
    second = ingest_file(registry_with(), "syn-source-1", write(tmp_path / "b.txt", b"synthetic v2"))
    assert first.content_hash != second.content_hash
    assert first.document_id != second.document_id

    repeat = ingest_file(registry_with(), "syn-source-1", tmp_path / "a.txt")
    assert repeat == first
    assert repeat.document_id == first.document_id


def test_provenance_survives_serialization(tmp_path: Path) -> None:
    record = ingest_file(registry_with(), "syn-source-1", write(tmp_path / "p.txt", b"synthetic"))
    restored = DocumentRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.source_id == "syn-source-1"
    assert restored.source_verification == "unverified"
    with pytest.raises(SourceError, match="mapping"):
        DocumentRecord.from_dict([])  # type: ignore[arg-type]


def test_original_file_unmodified(tmp_path: Path) -> None:
    raw = b"<p>synthetic</p>"
    target = write(tmp_path / "orig.html", raw)
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    ingest_file(registry_with(), "syn-source-1", target)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_malformed_html_and_empty_text(tmp_path: Path) -> None:
    soup = ingest_file(registry_with(), "syn-source-1",
                       write(tmp_path / "soup.html", b"<div><p>Unclosed synthetic"))
    assert soup.extraction_status == "ok"
    assert soup.normalized_text == "Unclosed synthetic"
    assert ingest_file(registry_with(), "syn-source-1", tmp_path / "soup.html") == soup

    shell = ingest_file(registry_with(), "syn-source-1",
                        write(tmp_path / "shell.html", b"<html><head></head><body>   </body></html>"))
    assert shell.extraction_status == "empty"
    assert shell.normalized_text == ""


def test_explicit_format_override(tmp_path: Path) -> None:
    raw = b"<p>Override synthetic</p>"
    as_html = ingest_file(registry_with(), "syn-source-1",
                          write(tmp_path / "note.txt", raw), format="html")
    assert as_html.normalized_text == "Override synthetic"
    as_txt = ingest_file(registry_with(), "syn-source-1",
                         write(tmp_path / "page.html", raw), format="txt")
    assert as_txt.normalized_text == "<p>Override synthetic</p>"
