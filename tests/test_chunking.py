"""Chunking tests using SYNTHETIC fixtures, plus an opt-in smoke test
over the acquired First Schedule record (skipped where the artifact is
absent). Nothing here states tax law or verifies the real source.
"""

import json
import os

import pytest

from backend.app.chunking import (
    CHUNKING_VERSION,
    ChunkConfig,
    ChunkingError,
    chunk_document,
)
from backend.app.sources import DocumentRecord

DOC_ID = "syn-doc-1"


def syn_doc(text: str, doc_id: str = DOC_ID) -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        source_id="syn-source-1",
        filename="syn.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash="a" * 64,
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification="unverified",
    )


def test_empty_and_whitespace_text() -> None:
    assert chunk_document(syn_doc("")) == ()
    assert chunk_document(syn_doc("   \n\n  ")) == ()


def test_short_text_single_passage() -> None:
    text = "A short synthetic provision."
    (passage,) = chunk_document(syn_doc(text))
    assert (passage.char_start, passage.char_end) == (0, len(text))
    assert passage.text == text
    assert passage.chunk_index == 0
    assert (passage.page_start, passage.page_end) == (None, None)


def test_paragraph_boundaries_preferred() -> None:
    text = "First synthetic paragraph.\n\nSecond synthetic paragraph.\n\nThird."
    passages = chunk_document(syn_doc(text), ChunkConfig(max_chars=60, overlap_chars=0))
    assert [p.text for p in passages] == [
        "First synthetic paragraph.\n\nSecond synthetic paragraph.",
        "Third.",
    ]
    assert all(len(p.text) <= 60 for p in passages)


def test_oversized_paragraph_fallback() -> None:
    text = "x" * 250
    passages = chunk_document(syn_doc(text), ChunkConfig(max_chars=100, overlap_chars=10))
    assert [len(p.text) for p in passages] == [100, 100, 70]
    assert passages[0].text == text[0:100]
    assert passages[1].text == text[90:190]
    assert passages[2].text == text[180:250]


def test_overlap_bounded_progress() -> None:
    paras = "\n\n".join(f"synthetic paragraph number {i} with filler words" for i in range(20))
    passages = chunk_document(syn_doc(paras), ChunkConfig(max_chars=120, overlap_chars=30))
    assert len(passages) < 20  # terminates; no per-paragraph explosion
    assert all(len(p.text) <= 120 for p in passages)
    assert all(p.text for p in passages)  # no empty chunks
    assert "paragraph number 19" in passages[-1].text


def test_offset_recovery_and_ordering() -> None:
    text = "Alpha synthetic.\n\nBeta synthetic clause with details.\n\nGamma."
    passages = chunk_document(syn_doc(text), ChunkConfig(max_chars=40, overlap_chars=0))
    assert [p.chunk_index for p in passages] == list(range(len(passages)))
    for passage in passages:
        assert passage.text == text[passage.char_start:passage.char_end]
    starts = [p.char_start for p in passages]
    assert starts == sorted(starts)


def test_stable_ids_and_reruns() -> None:
    text = "Stable synthetic text.\n\nSecond block here."
    first = chunk_document(syn_doc(text))
    assert chunk_document(syn_doc(text)) == first
    assert len({p.passage_id for p in first}) == len(first)
    assert all(p.passage_id.startswith(f"{DOC_ID}:c") for p in first)
    assert all(p.chunking_version == CHUNKING_VERSION for p in first)

    other = chunk_document(syn_doc(text, doc_id="syn-doc-2"))
    assert {p.passage_id for p in other}.isdisjoint({p.passage_id for p in first})
    assert [p.text for p in other] == [p.text for p in first]


def test_pdf_markers_within_and_across_pages() -> None:
    text = "--- page 1 ---\n\nAlpha beta.\n\n--- page 2 ---\n\nGamma delta."
    (single,) = chunk_document(syn_doc(text), ChunkConfig(max_chars=500, overlap_chars=0))
    assert (single.page_start, single.page_end) == (1, 2)

    contained = chunk_document(
        syn_doc("--- page 3 ---\n\nOnly on three."), ChunkConfig(max_chars=500, overlap_chars=0)
    )
    assert [(p.page_start, p.page_end) for p in contained] == [(3, 3)]


def test_no_markers_and_malformed_markers() -> None:
    plain = chunk_document(syn_doc("Just synthetic text, no markers."))
    assert [(p.page_start, p.page_end) for p in plain] == [(None, None)]

    # Out-of-order page numbers: mapping unreliable, reported as unknown.
    odd = chunk_document(syn_doc("--- page 2 ---\n\nText.\n\n--- page 1 ---\n\nMore."))
    assert [(p.page_start, p.page_end) for p in odd] == [(None, None)]

    # A near-miss line is ordinary text, not a marker.
    near = chunk_document(syn_doc("See --- page 4 --- for details."))
    assert [(p.page_start, p.page_end) for p in near] == [(None, None)]
    assert "See --- page 4 --- for details." in near[0].text


def test_invalid_config_and_inputs() -> None:
    with pytest.raises(ChunkingError):
        ChunkConfig(max_chars=0)
    with pytest.raises(ChunkingError):
        ChunkConfig(max_chars=100, overlap_chars=100)
    with pytest.raises(ChunkingError):
        ChunkConfig(max_chars=100, overlap_chars=-1)
    with pytest.raises(ChunkingError):
        ChunkConfig(max_chars="100")  # type: ignore[arg-type]
    with pytest.raises(ChunkingError):
        chunk_document("not-a-record")  # type: ignore[arg-type]
    with pytest.raises(ChunkingError):
        chunk_document(syn_doc("text"), "not-a-config")  # type: ignore[arg-type]
    # Boundary lengths: exact-fit paragraph packs whole.
    text = "y" * 100
    (only,) = chunk_document(syn_doc(text), ChunkConfig(max_chars=100, overlap_chars=0))
    assert only.text == text


def test_no_mutation_and_max_respected() -> None:
    text = "Unchanged synthetic text.\n\nSecond block."
    document = syn_doc(text)
    before = document.to_dict()
    passages = chunk_document(document, ChunkConfig(max_chars=30, overlap_chars=5))
    assert document.to_dict() == before
    assert all(len(p.text) <= 30 for p in passages)
    assert all(p.parser_id == "taxguard-sources-v1:txt" for p in passages)
    assert all((p.max_chars, p.overlap_chars) == (30, 5) for p in passages)


REAL_DOCUMENT = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "india-code",
    "income-tax-act-1961-first-schedule", "document.json",
)


@pytest.mark.skipif(
    not os.path.exists(REAL_DOCUMENT),
    reason="acquired artifact absent from this checkout",
)
def test_first_schedule_smoke() -> None:
    with open(REAL_DOCUMENT, encoding="utf-8") as handle:
        document = DocumentRecord(**json.load(handle))
    passages = chunk_document(document)

    print(
        f"\nsmoke: {len(passages)} passages, "
        f"lengths {min(len(p.text) for p in passages)}-{max(len(p.text) for p in passages)}, "
        f"pages {sorted({(p.page_start, p.page_end) for p in passages})}"
    )
    assert passages
    assert all(len(p.text) <= 1000 for p in passages)
    assert all(p.text == document.normalized_text[p.char_start:p.char_end] for p in passages)
    assert len({p.passage_id for p in passages}) == len(passages)
    assert {p.page_start for p in passages} == {1, 2}  # both PDF pages represented
    assert all(p.parser_id == document.parser_id for p in passages)
    assert document.to_dict() == DocumentRecord(**json.load(
        open(REAL_DOCUMENT, encoding="utf-8"))).to_dict()  # unmutated
