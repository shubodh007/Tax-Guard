"""Provenance bridge tests using SYNTHETIC fixtures, plus an opt-in
smoke test over the acquired First Schedule records (skipped where the
artifacts are absent). Resolution is by exact ids only; nothing here
invents sources, pages, or verification states.
"""

import json
import os

import pytest

from backend.app.chunking import ChunkConfig, chunk_document
from backend.app.evidence import EvidenceRef
from backend.app.provenance import (
    ProvenanceError,
    evidence_ref_for,
    resolve_provenance,
)
from backend.app.retrieval import retrieve_passages
from backend.app.source_verification import (
    Assessment,
    CategoryVerdict,
    VerificationPolicy,
    assess,
)
from backend.app.source_verification import (
    VerificationCheck as Check,
)
from backend.app.sources import DocumentRecord, SourceRecord


def syn_doc(text: str = "Alpha beta gamma.\n\nDelta epsilon.") -> DocumentRecord:
    return DocumentRecord(
        document_id="syn-doc-1",
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


def syn_source() -> SourceRecord:
    return SourceRecord(
        source_id="syn-source-1",
        title="Synthetic Test Schedule",
        publisher="Synthetic Test Board",
        origin="https://example.invalid/syn",
        source_type="schedule",
        jurisdiction="IN",
        note="synthetic fixture",
    )


def syn_assessment() -> Assessment:
    checks = (
        Check("syn-c1", "syn-source-1", "integrity", "passed", "d", "o"),
        Check("syn-c2", "syn-source-1", "origin", "inconclusive", "d", "o"),
    )
    return assess("syn-source-1", checks, VerificationPolicy("p", "v", ("integrity", "origin")))


def first_passage(text: str = "Alpha beta gamma.\n\nDelta epsilon."):
    passages = chunk_document(syn_doc(text), ChunkConfig(max_chars=30, overlap_chars=0))
    assert len(passages) == 2
    return passages[0]


def test_full_resolution() -> None:
    document = syn_doc()
    passage = first_passage()
    resolved = resolve_provenance(
        passage, {"syn-doc-1": document}, {"syn-source-1": syn_source()},
        {"syn-source-1": syn_assessment()},
    )

    assert resolved.unresolved == ()
    assert (resolved.passage_id, resolved.document_id, resolved.source_id) == (
        passage.passage_id, "syn-doc-1", "syn-source-1")
    assert resolved.text == passage.text
    assert (resolved.char_start, resolved.char_end) == (passage.char_start, passage.char_end)
    assert (resolved.page_start, resolved.page_end) == (None, None)
    assert (resolved.parser_id, resolved.chunking_version) == (
        passage.parser_id, passage.chunking_version)
    assert resolved.content_hash == "a" * 64
    assert (resolved.source_title, resolved.source_publisher, resolved.source_origin) == (
        "Synthetic Test Schedule", "Synthetic Test Board", "https://example.invalid/syn")
    assert resolved.assessment_status == "incomplete"
    assert [(v.category, v.verdict) for v in resolved.verdicts] == [
        ("origin", "uncertain"), ("integrity", "satisfied"), ("version", "not_required"),
        ("completeness", "not_required"), ("applicability", "not_required"),
        ("record_identity", "not_required"),
    ]
    assert resolved.score is None and resolved.rank is None


def test_score_rank_echoed_from_hit() -> None:
    document = syn_doc()
    passages = chunk_document(document, ChunkConfig(max_chars=30, overlap_chars=0))
    (hit,) = retrieve_passages("alpha", passages)
    resolved = resolve_provenance(
        hit, {"syn-doc-1": document}, {"syn-source-1": syn_source()})
    assert (resolved.score, resolved.rank) == (hit.score, 1)
    assert resolved.unresolved == ("assessment",)
    assert resolved.assessment_status == "" and resolved.verdicts == ()


def test_missing_links_named_explicitly() -> None:
    document = syn_doc()
    passage = first_passage()

    assert resolve_provenance(passage, {}, {}, {}).unresolved == (
        "document", "source", "assessment")
    missing_source = resolve_provenance(passage, {"syn-doc-1": document}, {}, {})
    assert missing_source.unresolved == ("source", "assessment")
    assert missing_source.source_id == "" and missing_source.source_title == ""
    assert missing_source.content_hash == "a" * 64  # document link still resolved


def test_evidence_ref_mapping() -> None:
    document = syn_doc()
    passage = first_passage()
    resolved = resolve_provenance(
        passage, {"syn-doc-1": document}, {"syn-source-1": syn_source()})

    ref = evidence_ref_for(resolved)
    assert isinstance(ref, EvidenceRef)
    assert ref.evidence_id == passage.passage_id
    assert ref.document_id == "syn-doc-1"
    assert ref.passage_id == passage.passage_id
    assert ref.digest == "a" * 64
    assert ref.uri == "https://example.invalid/syn"
    assert ref.trust == "unverified"

    assert evidence_ref_for(resolved, trust="synthetic").trust == "synthetic"
    with pytest.raises(ProvenanceError, match="trust"):
        evidence_ref_for(resolved, trust="authoritative")

    bare = resolve_provenance(passage, {}, {})
    bare_ref = evidence_ref_for(bare)
    assert bare_ref.document_id == "syn-doc-1"  # from the passage itself
    assert bare_ref.digest == "" and bare_ref.uri == ""  # unknown stays unknown


def test_malformed_inputs_rejected() -> None:
    document = syn_doc()
    passage = first_passage()
    with pytest.raises(ProvenanceError):
        resolve_provenance("not-a-passage", {}, {})  # type: ignore[arg-type]
    with pytest.raises(ProvenanceError, match="mapping"):
        resolve_provenance(passage, [], {})  # type: ignore[arg-type]
    with pytest.raises(ProvenanceError, match="DocumentRecord"):
        resolve_provenance(passage, {"syn-doc-1": "nope"}, {})  # type: ignore[dict-item]
    with pytest.raises(ProvenanceError, match="SourceRecord"):
        resolve_provenance(passage, {"syn-doc-1": document}, {"syn-source-1": "nope"})  # type: ignore[dict-item]
    with pytest.raises(ProvenanceError, match="Assessment"):
        resolve_provenance(passage, {"syn-doc-1": document}, {"syn-source-1": syn_source()},
                           {"syn-source-1": "nope"})  # type: ignore[dict-item]
    with pytest.raises(ProvenanceError):
        evidence_ref_for("not-resolved")  # type: ignore[arg-type]


def test_deterministic_and_immutable() -> None:
    document = syn_doc()
    passage = first_passage()
    args = ({"syn-doc-1": document}, {"syn-source-1": syn_source()},
            {"syn-source-1": syn_assessment()})
    assert resolve_provenance(passage, *args) == resolve_provenance(passage, *args)
    assert document.to_dict() == syn_doc().to_dict()
    assert syn_source().to_dict() == syn_source().to_dict()


CORPUS = os.path.join(os.path.dirname(__file__), "..", "corpus", "india-code",
                       "income-tax-act-1961-first-schedule")


@pytest.mark.skipif(
    not os.path.exists(os.path.join(CORPUS, "document.json")),
    reason="acquired artifact absent from this checkout",
)
def test_first_schedule_provenance_smoke() -> None:
    with open(os.path.join(CORPUS, "document.json"), encoding="utf-8") as handle:
        document = DocumentRecord(**json.load(handle))
    with open(os.path.join(CORPUS, "source.json"), encoding="utf-8") as handle:
        source = SourceRecord(**json.load(handle))
    with open(os.path.join(CORPUS, "reverification-2026-10-09.json"), encoding="utf-8") as handle:
        stored = json.load(handle)
    assessment = Assessment(
        source_id=stored["assessment"]["source_id"],
        status=stored["assessment"]["status"],
        policy_id=stored["assessment"]["policy_id"],
        policy_version=stored["assessment"]["policy_version"],
        reason_codes=tuple(stored["assessment"]["reason_codes"]),
        check_ids=tuple(stored["assessment"]["check_ids"]),
        verdicts=tuple(
            CategoryVerdict(v["category"], v["verdict"]) for v in stored["assessment"]["verdicts"]
        ),
    )
    from backend.app.chunking import chunk_document as chunk

    passages = chunk(document)
    (hit,) = retrieve_passages("insurance", passages, limit=1)
    resolved = evidence_ref_for(resolve_provenance(
        hit, {document.document_id: document}, {source.source_id: source},
        {source.source_id: assessment}))

    print(f"\nsmoke: {resolved.evidence_id} doc={resolved.document_id} "
          f"digest_match={resolved.digest == document.content_hash} "
          f"assessment={assessment.status}")
    assert resolved.document_id == document.document_id
    assert resolved.digest == document.content_hash
    assert assessment.status == "incomplete"
    assert source.verification == "unverified"
