"""PDF ingestion tests with hand-built SYNTHETIC PDFs only.

Fixtures are assembled byte-for-byte in this file (offsets computed,
never hand-counted), so no network, fixture files, or real documents
are involved. Nothing here states tax law.
"""

import hashlib
import io

import pytest

from backend.app.sources import (
    PDF_MAX_PAGES,
    DocumentRecord,
    IngestionError,
    SourceRecord,
    SourceRegistry,
    ingest_file,
)


def text_page(line: str) -> bytes:
    escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"BT /F1 12 Tf 72 720 Td (" + escaped.encode("latin-1") + b") Tj ET"


def image_page() -> tuple[bytes, bytes, bytes]:
    content = b"q 10 0 0 10 0 0 cm /Im1 Do Q"
    resources = b"<< /XObject << /Im1 6 0 R >> >>"
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream"
    )
    return content, resources, image


def build_pdf_full(page_defs: list[tuple[bytes, bytes]], extras: list[bytes] | None = None) -> bytes:
    """Assemble a PDF with per-page content and resource dicts."""
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    count = len(page_defs)
    first_page_obj = 4
    kids = " ".join(f"{first_page_obj + i * 2} 0 R" for i in range(count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for content, resources in page_defs:
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {len(objects) + 2} 0 R /Resources {resources.decode()} >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
        )
    for obj in extras or []:
        objects.append(obj)
    out = [b"%PDF-1.4"]
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) + 1 for chunk in out))
        out.append(f"{number} 0 obj".encode())
        out.append(body)
        out.append(b"endobj")
    xref_pos = sum(len(chunk) + 1 for chunk in out)
    out.append(f"xref\n0 {len(objects) + 1}".encode())
    out.append(b"0000000000 65535 f ")
    for offset in offsets:
        out.append(f"{offset:010d} 00000 n ".encode())
    out.append(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>".encode())
    out.append(f"startxref\n{xref_pos}\n%%EOF".encode())
    return b"\n".join(out)


FONT_RES = b"<< /Font << /F1 3 0 R >> >>"


def registry() -> SourceRegistry:
    reg = SourceRegistry()
    reg.register(SourceRecord(source_id="syn-source-1", title="Synthetic test source"))
    return reg


def ingest_pdf(data: bytes, name: str = "syn.pdf", **kwargs) -> DocumentRecord:
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return ingest_file(registry(), "syn-source-1", path, format="pdf", **kwargs)


def test_valid_pdf_ingestion() -> None:
    raw = build_pdf_full([(text_page("Hello synthetic world"), FONT_RES)])
    record = ingest_pdf(raw)

    assert record.format == "pdf"
    assert record.parser_id.startswith("taxguard-sources-v1:pdf:pypdf-")
    assert record.content_hash == hashlib.sha256(raw).hexdigest()
    assert record.document_id == f"syn-source-1:{record.content_hash[:32]}"
    assert record.size_bytes == len(raw)
    assert record.source_verification == "unverified"
    assert record.filename == "syn.pdf"
    assert record.extraction_status == "ok"
    assert "Hello synthetic world" in record.normalized_text
    assert "--- page 1 ---" in record.normalized_text


def test_deterministic_extraction() -> None:
    raw = build_pdf_full([(text_page("Deterministic synthetic line"), FONT_RES)])
    assert ingest_pdf(raw) == ingest_pdf(raw)


def test_page_boundaries_and_mixed_pages() -> None:
    raw = build_pdf_full([
        (text_page("First synthetic page"), FONT_RES),
        (b"", FONT_RES),
        (text_page("Third synthetic page"), FONT_RES),
    ])
    record = ingest_pdf(raw)
    text = record.normalized_text
    assert text.index("--- page 1 ---") < text.index("--- page 2 ---") < text.index("--- page 3 ---")
    assert "First synthetic page" in text and "Third synthetic page" in text
    assert record.extraction_status == "ok"


def test_empty_pdf_rejected() -> None:
    raw = build_pdf_full([])
    with pytest.raises(IngestionError, match="no pages"):
        ingest_pdf(raw)


def test_malformed_pdf_rejected() -> None:
    with pytest.raises(IngestionError, match="readable PDF"):
        ingest_pdf(b"%PDF-1.4 broken {{{ not objects", name="bad.pdf")


def test_encrypted_pdf_rejected() -> None:
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt("synthetic-secret")
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(IngestionError, match="encrypted"):
        ingest_pdf(buffer.getvalue(), name="enc.pdf")
    # Sanity: the fixture really is encrypted.
    assert PdfReader(BytesIO(buffer.getvalue())).is_encrypted


def test_image_only_pdf_rejected() -> None:
    content, resources, image = image_page()
    raw = build_pdf_full([(content, resources)], [image])
    with pytest.raises(IngestionError, match="no extractable text"):
        ingest_pdf(raw, name="scan.pdf")


def test_size_and_page_limits(tmp_path, monkeypatch) -> None:
    raw = build_pdf_full([(text_page("Bounded synthetic line"), FONT_RES)])
    with pytest.raises(IngestionError, match="size limit"):
        ingest_pdf(raw, max_bytes=10)
    monkeypatch.setattr("backend.app.sources.PDF_MAX_PAGES", 1)
    two = build_pdf_full([
        (text_page("Page one"), FONT_RES),
        (text_page("Page two"), FONT_RES),
    ])
    with pytest.raises(IngestionError, match="exceeding the limit"):
        ingest_pdf(two)
    assert PDF_MAX_PAGES == 2000


def test_original_byte_identity_and_provenance(tmp_path) -> None:
    raw = build_pdf_full([(text_page("Provenance synthetic line"), FONT_RES)])
    path = tmp_path / "prov.pdf"
    path.write_bytes(raw)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    record = ingest_file(registry(), "syn-source-1", path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert record.content_hash == hashlib.sha256(raw).hexdigest()
    assert record.source_id == "syn-source-1"
    assert record.source_verification == "unverified"


def test_txt_html_still_plain(tmp_path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("plain synthetic", encoding="utf-8")
    assert ingest_file(registry(), "syn-source-1", txt).parser_id == "taxguard-sources-v1:txt"
    html = tmp_path / "a.html"
    html.write_text("<p>plain <b>synthetic</b></p>", encoding="utf-8")
    assert ingest_file(registry(), "syn-source-1", html).parser_id == "taxguard-sources-v1:html"
