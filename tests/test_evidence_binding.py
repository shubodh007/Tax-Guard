"""Evidence-backed verification invariant tests (Phase 27).

A non-unverified label must reference a valid assessment satisfying its
policy for the exact source and content digest. Synthetic fixtures only;
no network, no real corpus except the explicit compatibility test.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.app.main import CORPUS_UNAVAILABLE, create_app
from backend.app.manifest import (
    ManifestEntry,
    ManifestError,
    build_manifest,
    build_passages,
    entry_for,
    entry_for_verified,
    load_manifest_file,
    verify_entry_binding,
)
from backend.app.source_verification import (
    Assessment,
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


def _syn_source(source_id: str) -> SourceRecord:
    return SourceRecord(source_id=source_id, title="Synthetic Source")


def _syn_doc(source_id: str, digest: str, status: str = "unverified") -> DocumentRecord:
    text = TEXT
    return DocumentRecord(
        document_id=f"{source_id}:{digest[:32]}",
        source_id=source_id,
        filename="syn.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash=digest,
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification=status,
    )


def _evidence(source, doc, required=("integrity",), observed_digest=None):
    observed_digest = observed_digest or doc.content_hash
    size = doc.size_bytes
    observed = ObservedArtifact(sha256=observed_digest, size_bytes=size)
    expected = ExpectedArtifact(sha256=doc.content_hash, size_bytes=size)
    checks = reconcile(
        source, observed=observed, expected=expected, document=doc,
        evidence_origin="local", checked_at="2026-01-01", verifier="test",
    )
    policy = VerificationPolicy("test-policy", "v1", required)
    return assess(source.source_id, checks, policy), list(checks), policy


def test_free_text_promotion_rejected() -> None:
    doc = _syn_doc("syn-free-s1", "a" * 64, status="verified")
    with pytest.raises(ManifestError, match="entry_for_verified"):
        entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json")
    base = {
        "source_id": doc.source_id, "document_id": doc.document_id,
        "artifact_path": "d1/a.pdf", "document_path": "d1/doc.json",
        "content_hash": doc.content_hash, "chunking_version": "taxguard-chunks-v1",
        "max_chars": 1000, "overlap_chars": 100, "passage_count": 1,
    }
    with pytest.raises(ManifestError, match="assessment digest"):
        ManifestEntry(source_verification="verified", assessment_digest="", **base)
    with pytest.raises(ManifestError, match="must not carry"):
        ManifestEntry(source_verification="unverified",
                      assessment_digest="a" * 64, **base)


def test_valid_assessment_authorizes_only_permitted_status() -> None:
    source, doc = _syn_source("syn-ok-s1"), _syn_doc("syn-ok-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc)
    assert assessment.status == "verified"
    authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                     status="verified", assessment=assessment, checks=checks, policy=policy)
    entry = entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                               artifact_path="d1/a.pdf", document_path="d1/doc.json")
    assert entry.assessment_digest == assessment_digest(assessment)
    verify_entry_binding(entry, assessment, checks, policy=policy)
    with pytest.raises(SourceVerificationError, match="disputed"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="disputed", assessment=assessment, checks=checks, policy=policy)


def test_incomplete_assessment_rejected() -> None:
    source, doc = _syn_source("syn-inc-s1"), _syn_doc("syn-inc-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc, required=("origin", "integrity"))
    assert assessment.status == "incomplete"
    with pytest.raises(SourceVerificationError, match="only a verified assessment"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment, checks=checks, policy=policy)
    with pytest.raises(ManifestError, match="not authorized"):
        entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                           artifact_path="d1/a.pdf", document_path="d1/doc.json")


def test_foreign_source_assessment_rejected() -> None:
    source_a, doc_a = _syn_source("syn-a-s1"), _syn_doc("syn-a-s1", "a" * 64, "verified")
    source_b, doc_b = _syn_source("syn-b-s1"), _syn_doc("syn-b-s1", "a" * 64, "verified")
    assessment_b, checks_b, policy_b = _evidence(source_b, doc_b)
    with pytest.raises(SourceVerificationError, match="belongs to source"):
        authorize_status(source_id=doc_a.source_id, content_digest=doc_a.content_hash,
                         status="verified", assessment=assessment_b,
                         checks=checks_b, policy=policy_b)
    with pytest.raises(ManifestError, match="not authorized"):
        entry_for_verified(doc_a, assessment=assessment_b, checks=checks_b,
                           policy=policy_b, artifact_path="d1/a.pdf",
                           document_path="d1/doc.json")
    assessment_a, checks_a, policy_a = _evidence(source_a, doc_a)
    entry_a = entry_for_verified(
        doc_a, assessment=assessment_a,
        checks=checks_a, policy=policy_a,
        artifact_path="d1/a.pdf", document_path="d1/doc.json")
    with pytest.raises(ManifestError, match="another source"):
        verify_entry_binding(entry_a, assessment_b, checks_b, policy=policy_a)


def test_foreign_digest_rejected() -> None:
    source, doc = _syn_source("syn-dig-s1"), _syn_doc("syn-dig-s1", "a" * 64, "verified")
    size = doc.size_bytes
    other_checks = reconcile(
        source, observed=ObservedArtifact(sha256="b" * 64, size_bytes=size),
        expected=ExpectedArtifact(sha256="b" * 64, size_bytes=size),
        evidence_origin="local", checked_at="2026-01-01", verifier="test")
    policy = VerificationPolicy("test-policy", "v1", ("integrity",))
    other_assessment = assess(source.source_id, other_checks, policy)
    assert other_assessment.status == "verified"
    with pytest.raises(SourceVerificationError, match="binds this assessment"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=other_assessment,
                         checks=list(other_checks), policy=policy)


def test_policy_and_digest_mismatch_rejected() -> None:
    source, doc = _syn_source("syn-mis-s1"), _syn_doc("syn-mis-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc)
    other_policy = VerificationPolicy("test-policy", "v2", ("integrity",))
    with pytest.raises(SourceVerificationError, match="policy does not match"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment,
                         checks=checks, policy=other_policy)
    tampered = Assessment(
        source_id=assessment.source_id, status=assessment.status,
        policy_id=assessment.policy_id, policy_version=assessment.policy_version,
        reason_codes=("satisfied:origin",), check_ids=assessment.check_ids,
        verdicts=assessment.verdicts)
    with pytest.raises(SourceVerificationError, match="reason codes"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=tampered,
                         checks=checks, policy=policy)
    entry = entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                               artifact_path="d1/a.pdf", document_path="d1/doc.json")
    with pytest.raises(ManifestError, match="stale or mismatched"):
        verify_entry_binding(entry, tampered, checks, policy=policy)


def test_changed_assessment_invalidates_stale_reference() -> None:
    from backend.app.source_verification import VerificationCheck

    source, doc = _syn_source("syn-stale-s1"), _syn_doc("syn-stale-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc)
    entry = entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                               artifact_path="d1/a.pdf", document_path="d1/doc.json")
    extra = VerificationCheck(
        check_id=f"{source.source_id}:origin:manual", source_id=source.source_id,
        check_type="origin", outcome="inconclusive", description="manual origin note",
        observation="retrieved over HTTPS; official status not established")
    revised_checks = checks + [extra]
    revised = assess(source.source_id, revised_checks, policy)
    assert assessment_digest(revised) != entry.assessment_digest
    with pytest.raises(ManifestError, match="stale or mismatched"):
        verify_entry_binding(entry, revised, revised_checks, policy=policy)


def test_unverified_corpus_unaffected(tmp_path) -> None:
    manifest = load_manifest_file("corpus/manifest.json")
    assert all(e.source_verification == "unverified" and e.assessment_digest == ""
               for e in manifest.entries)
    assert len(build_passages(manifest, ".")) == 91
    doc = _syn_doc("syn-plain-s1", "a" * 64)
    entry = entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json")
    assert entry.assessment_digest == ""
    rebuilt = build_manifest((entry,))
    assert rebuilt.to_dict()["entries"][0]["assessment_digest"] == ""


def _canonical_evidence(source, doc):
    """Self-consistent canonical-shaped evidence for negative testing.
    Every record here is manufactured inside the test: the 'passed'
    origin check stands in for genuine origin evidence, which this
    system cannot produce — so this evidence must NEVER authorize
    'verified'. It exists only to prove the gate refuses it."""

    size = len(TEXT.encode())
    checks = reconcile(
        source,
        observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
        expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
        document=doc,
        official=OfficialRecord(record_url="https://example.invalid/record",
                                title=source.title, act_label="Synthetic Act"),
        evidence_origin="local", checked_at="2026-01-01", verifier="test",
    )
    origin = VerificationCheck(
        check_id=f"{source.source_id}:origin:synthetic",
        source_id=source.source_id, check_type="origin", outcome="passed",
        description="synthetic test origin evidence (not a real check)",
        observation="served over TLS in test fixture")
    all_checks = list(checks) + [origin]
    policy = canonical_policy()
    return assess(source.source_id, all_checks, policy), all_checks, policy


def _manufactured_app(tmp_path):
    """Attack corpus: a verified-labeled entry with fully self-consistent
    manufactured evidence (origin/integrity/record_identity passed,
    recomputed digests, canonical policy id/version). Built by direct
    construction — entry_for_verified rightly refuses it — to prove the
    load path rejects it."""
    from backend.app.manifest import ManifestEntry

    source = _syn_source("syn-ask-s1")
    doc = _syn_doc("syn-ask-s1", "a" * 64, status="verified")
    assessment, checks, _ = _canonical_evidence(source, doc)
    assert assessment.status == "verified"
    entry = ManifestEntry(
        source_id=doc.source_id, document_id=doc.document_id,
        source_verification="verified", artifact_path="d1/a.pdf",
        document_path="d1/doc.json", content_hash=doc.content_hash,
        chunking_version="taxguard-chunks-v1", max_chars=1000,
        overlap_chars=100, passage_count=1,
        assessment_digest=assessment_digest(assessment))
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "d1"))
    with open(os.path.join(root, "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    manifest_path = os.path.join(root, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(build_manifest((entry,)).to_dict(), handle)
    verification_path = os.path.join(root, "d1", "verification.json")
    with open(verification_path, "w", encoding="utf-8") as handle:
        json.dump({"assessment": assessment.to_dict(),
                   "checks": [c.to_dict() for c in checks]}, handle)
    return manifest_path, root, verification_path


def test_manufactured_evidence_rejected_at_load(tmp_path) -> None:
    """Primary attack test: origin, integrity, and record identity all
    'passed', hashes and digests recomputed, canonical policy id/version
    — yet the system must refuse to serve 'verified' for lack of
    independently trusted origin evidence. Fail closed, generically."""
    manifest_path, root, _ = _manufactured_app(tmp_path)
    app = TestClient(create_app(manifest_path=manifest_path, base_dir=root))
    response = app.post("/ask", json={"question": "alpha beta"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert app.get("/ready").status_code == 503


def test_ask_missing_or_stale_assessment_fails_closed(tmp_path) -> None:
    manifest_path, root, verification_path = _manufactured_app(tmp_path)
    os.remove(verification_path)
    broken = TestClient(create_app(manifest_path=manifest_path, base_dir=root))
    response = broken.post("/ask", json={"question": "alpha beta"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert broken.get("/ready").status_code == 503
    with open(verification_path, "w", encoding="utf-8") as handle:
        source = _syn_source("syn-other-s9")
        other_doc = _syn_doc("syn-other-s9", "b" * 64, status="verified")
        other_assessment, other_checks, _ = _evidence(source, other_doc)
        json.dump({"assessment": other_assessment.to_dict(),
                   "checks": [c.to_dict() for c in other_checks]}, handle)
    tampered = TestClient(create_app(manifest_path=manifest_path, base_dir=root))
    assert tampered.post("/ask", json={"question": "alpha beta"}).status_code == 503


def test_manifest_generation_deterministic(tmp_path) -> None:
    source = _syn_source("syn-det-s1")
    doc = _syn_doc("syn-det-s1", "a" * 64, status="verified")
    assessment, checks, policy = _evidence(source, doc)
    kwargs = {"assessment": assessment, "checks": checks, "policy": policy,
              "artifact_path": "d1/a.pdf", "document_path": "d1/doc.json"}
    first = build_manifest((entry_for_verified(doc, **kwargs),
                            entry_for(_syn_doc("syn-det-s2", "b" * 64),
                                      artifact_path="d2/a.pdf",
                                      document_path="d2/doc.json")))
    second = build_manifest((entry_for_verified(doc, **kwargs),
                             entry_for(_syn_doc("syn-det-s2", "b" * 64),
                                       artifact_path="d2/a.pdf",
                                       document_path="d2/doc.json")))
    assert first.snapshot_id == second.snapshot_id
    assert first.to_dict() == second.to_dict()
    assert Assessment.from_dict(assessment.to_dict()).to_dict() == assessment.to_dict()
    with pytest.raises(SourceVerificationError):
        Assessment.from_dict({"source_id": "x"})


def test_canonical_policy_pinned() -> None:
    """The load-time trust anchor itself must not drift silently: id,
    version, and required categories are asserted exactly."""
    policy = canonical_policy()
    assert policy.policy_id == "taxguard-source-policy"
    assert policy.version == "v1"
    assert policy.required_checks == ("origin", "integrity", "record_identity")


def test_weak_policy_assessment_rejected_by_canonical() -> None:
    """The self-authorization hole, pinned shut: an assessment verified
    under an integrity-only policy is internally consistent and digest-
    bound, yet must not authorize under the canonical policy — reason
    codes alone never establish sufficiency."""
    source, doc = _syn_source("syn-weak-s1"), _syn_doc("syn-weak-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc)
    assert assessment.status == "verified"
    entry = entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                               artifact_path="d1/a.pdf", document_path="d1/doc.json")
    with pytest.raises(ManifestError, match="not authorized"):
        verify_entry_binding(entry, assessment, checks, policy=canonical_policy())
    with pytest.raises(SourceVerificationError, match="policy does not match"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment,
                         checks=checks, policy=canonical_policy())


def test_same_id_version_weaker_requirements_rejected() -> None:
    """Policy identity and version matching is not enough: a policy with
    the canonical id/version but weaker requirements must still fail
    against an assessment recorded under stronger ones, and vice versa."""
    source, doc = _syn_source("syn-pol-s1"), _syn_doc("syn-pol-s1", "a" * 64, "verified")
    assessment, checks, _ = _canonical_evidence(source, doc)
    weak_same_id = VerificationPolicy("taxguard-source-policy", "v1", ("integrity",))
    with pytest.raises(SourceVerificationError, match="reason codes"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment,
                         checks=checks, policy=weak_same_id)
    with pytest.raises(ManifestError, match="not authorized"):
        verify_entry_binding(
            entry_for_verified(doc, assessment=assessment, checks=checks,
                               policy=canonical_policy(), artifact_path="d1/a.pdf",
                               document_path="d1/doc.json"),
            assessment, checks, policy=weak_same_id)


def test_missing_integrity_record_rejected() -> None:
    """Reason codes claiming a satisfied category prove nothing without
    the backing record: drop the integrity check and authorization must
    fail even though codes and verdicts still name it satisfied."""
    source, doc = _syn_source("syn-norec-s1"), _syn_doc("syn-norec-s1", "a" * 64, "verified")
    assessment, checks, policy = _evidence(source, doc)
    kept = [c for c in checks if c.check_type != "integrity"]
    assert kept != checks
    with pytest.raises(SourceVerificationError, match="binds this assessment"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment,
                         checks=kept, policy=policy)


def test_canonical_evidence_still_refused() -> None:
    """No fully file-supplied evidence set — however self-consistent —
    authorizes 'verified' while origin evidence is unanchored. The
    mechanism itself is proven by the weaker-policy tests; the trust
    boundary stays closed here by design, pending a genuine origin
    evidence mechanism (future work, not invented here)."""
    source, doc = _syn_source("syn-full-s1"), _syn_doc("syn-full-s1", "a" * 64, "verified")
    assessment, checks, policy = _canonical_evidence(source, doc)
    assert assessment.status == "verified"
    with pytest.raises(SourceVerificationError, match="origin satisfaction"):
        authorize_status(source_id=source.source_id, content_digest=doc.content_hash,
                         status="verified", assessment=assessment,
                         checks=checks, policy=policy)
    with pytest.raises(ManifestError, match="not authorized"):
        entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                           artifact_path="d1/a.pdf", document_path="d1/doc.json")


def test_load_rejects_weak_policy_assessment(tmp_path) -> None:
    """End-to-end load path: a digest-bound entry whose assessment meets
    only a weak policy fails closed under the canonical anchor."""
    source = _syn_source("syn-load-s1")
    doc = _syn_doc("syn-load-s1", "a" * 64, status="verified")
    assessment, checks, policy = _evidence(source, doc)
    entry = entry_for_verified(doc, assessment=assessment, checks=checks, policy=policy,
                               artifact_path="d1/a.pdf", document_path="d1/doc.json")
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "d1"))
    with open(os.path.join(root, "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    manifest_path = os.path.join(root, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(build_manifest((entry,)).to_dict(), handle)
    with open(os.path.join(root, "d1", "verification.json"), "w", encoding="utf-8") as handle:
        json.dump({"assessment": assessment.to_dict(),
                   "checks": [c.to_dict() for c in checks]}, handle)
    app = TestClient(create_app(manifest_path=manifest_path, base_dir=root))
    assert app.post("/ask", json={"question": "alpha beta"}).status_code == 503
    assert app.get("/ready").status_code == 503
