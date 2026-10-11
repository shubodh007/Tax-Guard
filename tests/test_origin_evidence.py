"""Origin-evidence contract tests (Phase 28).

Every fixture here is synthetic and temporary. Nothing touches the
real corpus except one read-only load asserting it stays unverified.
Nothing here creates a trust anchor: these tests pin the fail-closed
contract in docs/source-origin-evidence-contract.md.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.app.main import CORPUS_UNAVAILABLE, create_app
from backend.app.manifest import (
    ManifestError,
    build_manifest,
    build_passages,
    entry_for_verified,
    load_manifest_file,
)
from backend.app.source_verification import (
    ExpectedArtifact,
    ObservedArtifact,
    OfficialRecord,
    SourceVerificationError,
    VerificationCheck,
    VerificationPolicy,
    assess,
    assessment_digest,
    authorize_status,
    canonical_policy,
    reconcile,
)
from backend.app.sources import DocumentRecord, SourceRecord

TEXT = "Alpha beta gamma delta epsilon provisions."


def _source(source_id: str = "syn-origin-s1", **overrides) -> SourceRecord:
    fields = {"source_id": source_id, "title": "Synthetic Source"}
    fields.update(overrides)
    return SourceRecord(**fields)  # type: ignore[arg-type]


def _doc(source_id: str = "syn-origin-s1", digest: str = "a" * 64) -> DocumentRecord:
    return DocumentRecord(
        document_id=f"{source_id}:{digest[:32]}",
        source_id=source_id,
        filename="syn.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash=digest,
        size_bytes=len(TEXT.encode()),
        normalized_text=TEXT,
        extraction_status="ok",
        source_verification="verified",
    )


def _fabricated_canonical(source: SourceRecord, doc: DocumentRecord):
    """Fully self-consistent manufactured evidence under the canonical
    policy: integrity + record_identity from reconcile plus a
    fabricated passed origin record. Must NEVER authorize."""
    size = len(TEXT.encode())
    checks = list(
        reconcile(
            source,
            observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
            expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
            document=doc,
            official=OfficialRecord(
                record_url="https://example.invalid/record",
                title=source.title,
                act_label="Synthetic Act",
            ),
            evidence_origin="local",
            checked_at="2026-01-01",
            verifier="test",
        )
    )
    fabricated = VerificationCheck(
        check_id=f"{source.source_id}:origin:synthetic",
        source_id=source.source_id,
        check_type="origin",
        outcome="passed",
        description="synthetic test origin evidence (not a real check)",
        observation="served over TLS in test fixture",
    )
    all_checks = checks + [fabricated]
    policy = canonical_policy()
    return assess(source.source_id, all_checks, policy), all_checks, policy


def test_fabricated_origin_passed_cannot_authorize_verified() -> None:
    source, doc = _source(), _doc()
    assessment, checks, policy = _fabricated_canonical(source, doc)
    assert assessment.status == "verified"
    with pytest.raises(SourceVerificationError, match="origin satisfaction"):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=policy,
        )
    with pytest.raises(ManifestError, match="not authorized"):
        entry_for_verified(
            doc,
            assessment=assessment,
            checks=checks,
            policy=policy,
            artifact_path="d1/a.pdf",
            document_path="d1/doc.json",
        )


def test_hash_and_digest_alone_cannot_establish_origin() -> None:
    """A recomputed digest binding without accepted origin evidence is
    still refused under the canonical policy."""
    source, doc = _source("syn-hash-s1"), _doc("syn-hash-s1")
    size = len(TEXT.encode())
    checks = list(
        reconcile(
            source,
            observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
            expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
            document=doc,
            evidence_origin="local",
            checked_at="2026-01-01",
            verifier="test",
        )
    )
    weak = VerificationPolicy("test-policy", "v1", ("integrity",))
    assessment = assess(source.source_id, checks, weak)
    assert assessment.status == "verified"
    # Digest is correctly bound — and still insufficient for origin.
    assert assessment_digest(assessment)
    with pytest.raises(SourceVerificationError):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=canonical_policy(),
        )


def test_official_domain_strings_alone_cannot_establish_origin() -> None:
    """A gov-looking origin string with no passed origin record leaves
    the assessment incomplete and unauthorized."""
    source = _source(
        "syn-gov-s1",
        origin="https://indiacode.gov.in/",
        publisher="India Code (Ministry of Finance, Department of Revenue)",
    )
    doc = _doc("syn-gov-s1")
    size = len(TEXT.encode())
    checks = list(
        reconcile(
            source,
            observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
            expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
            document=doc,
            evidence_origin="local",
            checked_at="2026-01-01",
            verifier="test",
        )
    )
    assert not [c for c in checks if c.check_type == "origin"]
    assessment = assess(source.source_id, checks, canonical_policy())
    assert assessment.status == "incomplete"
    with pytest.raises(SourceVerificationError):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=canonical_policy(),
        )


def test_missing_anchor_fails_closed_under_canonical_policy() -> None:
    """The anchor is absent: canonical policy requires origin and the
    gate refuses every verified promotion while it does."""
    policy = canonical_policy()
    assert policy.required_checks == ("origin", "integrity", "record_identity")
    source, doc = _source("syn-anchor-s1"), _doc("syn-anchor-s1")
    assessment, checks, _ = _fabricated_canonical(source, doc)
    with pytest.raises(SourceVerificationError, match="origin satisfaction"):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=policy,
        )


def test_machinery_never_yields_passed_origin() -> None:
    """reconcile() produces no origin record at all; the acquisition
    origin helper is inconclusive by construction."""
    from backend.app.acquisition import origin_observation

    source, doc = _source("syn-mach-s1"), _doc("syn-mach-s1", "b" * 64)
    doc_unverified = DocumentRecord(
        document_id=doc.document_id,
        source_id=doc.source_id,
        filename=doc.filename,
        format=doc.format,
        parser_id=doc.parser_id,
        content_hash=doc.content_hash,
        size_bytes=doc.size_bytes,
        normalized_text=doc.normalized_text,
        extraction_status=doc.extraction_status,
        source_verification="unverified",
    )
    size = len(TEXT.encode())
    checks = reconcile(
        source,
        observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
        expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
        document=doc_unverified,
        evidence_origin="local",
        checked_at="2026-01-01",
        verifier="test",
    )
    assert not [c for c in checks if c.check_type == "origin"]
    observed = origin_observation(source.source_id, "syn-check-1", "https://example.invalid/a.pdf", 200)
    assert observed.check_type == "origin" and observed.outcome == "inconclusive"


def test_unverified_corpus_loading_continues_to_work() -> None:
    """Read-only guard: the real corpus stays unverified and loadable."""
    manifest = load_manifest_file("corpus/manifest.json")
    assert len(manifest.entries) == 2
    assert {e.source_verification for e in manifest.entries} == {"unverified"}
    assert all(e.assessment_digest == "" for e in manifest.entries)
    assert len(build_passages(manifest, ".")) == 91


def test_manufactured_verified_entry_fails_closed_at_serve(tmp_path) -> None:
    """Contract-level serve check on a temporary corpus only: a
    digest-bound verified entry with fabricated origin fails closed
    with the generic 503 on both /ask and /ready."""
    from backend.app.manifest import ManifestEntry

    source, doc = _source("syn-serve-s1"), _doc("syn-serve-s1")
    assessment, checks, _ = _fabricated_canonical(source, doc)
    entry = ManifestEntry(
        source_id=doc.source_id,
        document_id=doc.document_id,
        source_verification="verified",
        artifact_path="d1/a.pdf",
        document_path="d1/doc.json",
        content_hash=doc.content_hash,
        chunking_version="taxguard-chunks-v1",
        max_chars=1000,
        overlap_chars=100,
        passage_count=1,
        assessment_digest=assessment_digest(assessment),
    )
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "d1"))
    with open(os.path.join(root, "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    manifest_path = os.path.join(root, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(build_manifest((entry,)).to_dict(), handle)
    with open(os.path.join(root, "d1", "verification.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"assessment": assessment.to_dict(), "checks": [c.to_dict() for c in checks]},
            handle,
        )
    app = TestClient(create_app(manifest_path=manifest_path, base_dir=root))
    response = app.post("/ask", json={"question": "alpha beta"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert app.get("/ready").status_code == 503
