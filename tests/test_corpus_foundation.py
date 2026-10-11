"""Corpus-foundation batch tests (Phase 29).

Real-corpus tests are read-only: they load committed records,
recompute hashes from stored bytes, rebuild passages, and serve the
real manifest through `create_app` without writing anything.
Adversarial cases use synthetic temporary fixtures only.
"""

import glob
import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.app.acquisition import AcquisitionError, AcquisitionReceipt, verify_stored_artifact
from backend.app.chunking import chunk_document
from backend.app.main import create_app
from backend.app.manifest import build_manifest, build_passages, entry_for, load_manifest_file
from backend.app.retrieval import retrieve_passages
from backend.app.source_verification import (
    ExpectedArtifact,
    ObservedArtifact,
    OfficialRecord,
    SourceVerificationError,
    VerificationPolicy,
    assess,
    authorize_status,
    canonical_policy,
    reconcile,
)
from backend.app.sources import DocumentRecord, SourceRecord

FIRST_DIR = os.path.join("corpus", "india-code", "income-tax-act-1961-first-schedule")
SECOND_DIR = os.path.join("corpus", "india-code", "income-tax-act-1961-second-schedule")
EXPECTED_SOURCES = (
    "india-code-it-act-1961-first-schedule",
    "india-code-it-act-1961-second-schedule",
)


def _load_record(directory: str, name: str) -> dict:
    with open(os.path.join(directory, name), encoding="utf-8") as handle:
        return json.load(handle)


def test_scope_inventory_matches_held_corpus() -> None:
    """Required inventory guard: exactly the two schedule fragments,
    both unverified, with the pinned snapshot."""
    manifest = load_manifest_file(os.path.join("corpus", "manifest.json"))
    assert tuple(e.source_id for e in manifest.entries) == EXPECTED_SOURCES
    assert {e.source_verification for e in manifest.entries} == {"unverified"}
    assert all(e.assessment_digest == "" for e in manifest.entries)
    assert manifest.snapshot_id == "77fdc5329096755a58d09d6c5e78d52a55685021969e3dd019116f4bb37c4d31"
    assert sum(e.passage_count for e in manifest.entries) == 91


def test_manifest_regeneration_deterministic_without_overwrite(tmp_path) -> None:
    """Rebuild from committed records only; compare the snapshot.
    The committed manifest is read, never written."""
    before = open(os.path.join("corpus", "manifest.json"), "rb").read()
    entries = []
    for directory in (FIRST_DIR, SECOND_DIR):
        record = DocumentRecord.from_dict(_load_record(directory, "document.json"))
        rel_doc = os.path.join(directory, "document.json").replace(os.sep, "/")
        rel_art = os.path.join(directory, record.filename).replace(os.sep, "/")
        entries.append(entry_for(record, artifact_path=rel_art, document_path=rel_doc))
    rebuilt = build_manifest(entries)
    committed = load_manifest_file(os.path.join("corpus", "manifest.json"))
    assert rebuilt.snapshot_id == committed.snapshot_id
    assert open(os.path.join("corpus", "manifest.json"), "rb").read() == before


def test_stored_bytes_match_receipts_read_only() -> None:
    """Integrity spot-check: recompute both PDF hashes from disk and
    compare with their immutable receipts. No bytes are modified."""
    for directory, pdf in (
        (FIRST_DIR, "sch_i_196143.pdf"),
        (SECOND_DIR, "sch_ii_196143.pdf"),
    ):
        receipts = glob.glob(os.path.join(directory, "*.receipt.json"))
        assert len(receipts) == 1
        receipt = AcquisitionReceipt.from_dict(_load_record(directory, os.path.basename(receipts[0])))
        assert receipt.verification == "unverified" and receipt.limitations
        verify_stored_artifact(os.path.join(directory, pdf), receipt.sha256)
        record = DocumentRecord.from_dict(_load_record(directory, "document.json"))
        assert record.content_hash == receipt.sha256


def test_version_and_effective_period_evidence_missing() -> None:
    """Version/currency metadata is explicitly absent: empty
    publication fields, no satisfied version verdict anywhere."""
    for directory in (FIRST_DIR, SECOND_DIR):
        source = SourceRecord.from_dict(_load_record(directory, "source.json"))
        assert source.publication == "" and source.verification == "unverified"
        payload = _load_record(directory, "verification.json")
        assert payload["assessment"]["status"] == "incomplete"
        verdicts = {v["category"]: v["verdict"] for v in payload["assessment"]["verdicts"]}
        assert verdicts.get("version") != "satisfied"
        assert verdicts.get("completeness") != "satisfied"
        assert verdicts.get("applicability") != "satisfied"


def test_chunking_deterministic_with_provenance() -> None:
    """Real documents chunk deterministically with recoverable offsets
    and stable passage identities."""
    for directory, expected in ((FIRST_DIR, 7), (SECOND_DIR, 84)):
        record = DocumentRecord.from_dict(_load_record(directory, "document.json"))
        first, second = chunk_document(record), chunk_document(record)
        assert first == second and len(first) == expected
        assert len({p.passage_id for p in first}) == len(first)
        for passage in first:
            assert passage.text == record.normalized_text[passage.char_start:passage.char_end]
            assert passage.document_id == record.document_id


def test_supported_and_unsupported_retrieval_on_real_corpus() -> None:
    """Supported schedule phrasing retrieves (flagged unverified);
    out-of-scope phrasing abstains. Read-only app over the real manifest."""
    app = TestClient(create_app(manifest_path=os.path.join("corpus", "manifest.json"), base_dir="."))
    assert app.get("/ready").status_code == 200
    hit = app.post("/ask", json={"question": "life insurance business profits computed separately"}).json()
    assert hit["status"] == "retrieved"
    assert hit["passages"]
    assert {p["source_verification"] for p in hit["passages"]} == {"unverified"}
    assert hit["snapshot_id"] == "77fdc5329096755a58d09d6c5e78d52a55685021969e3dd019116f4bb37c4d31"
    miss = app.post("/ask", json={"question": "xyzzy qwerty asdfgh depreciation annuity pension"}).json()
    assert miss["status"] == "abstained"


def test_malformed_or_incomplete_receipts_rejected() -> None:
    base = {
        "source_id": "syn-source-1",
        "artifact_id": "syn-source-1:" + "0" * 32,
        "title": "Synthetic artifact",
        "publisher": "",
        "source_page_url": "http://127.0.0.1/syn-page",
        "artifact_url": "http://127.0.0.1/syn.pdf",
        "final_url": "http://127.0.0.1/syn.pdf",
        "redirect_chain": [],
        "acquired_at": "2026-10-09T00:00:00+00:00",
        "http_status": 200,
        "content_type": "",
        "filename": "syn.pdf",
        "size_bytes": 4,
        "sha256": "0" * 64,
        "format": "pdf",
        "parsed": False,
        "document_id": "",
        "verification": "unverified",
        "limitations": ["synthetic test receipt"],
    }
    with pytest.raises(AcquisitionError):
        AcquisitionReceipt.from_dict({**base, "limitations": []})
    with pytest.raises(AcquisitionError):
        AcquisitionReceipt.from_dict({**base, "sha256": "not-a-digest"})
    with pytest.raises(AcquisitionError, match="never marks"):
        AcquisitionReceipt.from_dict({**base, "verification": "verified"})
    with pytest.raises(AcquisitionError):
        AcquisitionReceipt.from_dict({"source_id": "syn-source-1"})


def test_conflicting_publication_identity_fails_closed() -> None:
    """A title-conflicting official record fails record_identity, so
    the assessment can never authorize a stronger label."""
    source = SourceRecord(source_id="syn-conf-s1", title="Synthetic Source")
    doc = DocumentRecord(
        document_id="syn-conf-s1:" + "a" * 32,
        source_id="syn-conf-s1",
        filename="syn.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash="a" * 64,
        size_bytes=5,
        normalized_text="Alpha",
        extraction_status="ok",
        source_verification="verified",
    )
    size = 5
    checks = list(
        reconcile(
            source,
            observed=ObservedArtifact(sha256=doc.content_hash, size_bytes=size),
            expected=ExpectedArtifact(sha256=doc.content_hash, size_bytes=size),
            document=doc,
            official=OfficialRecord(
                record_url="https://example.invalid/other",
                title="A Different Publication",
                act_label="Synthetic Act",
            ),
            evidence_origin="local",
            checked_at="2026-01-01",
            verifier="test",
        )
    )
    assert [c for c in checks if c.check_type == "record_identity" and c.outcome == "failed"]
    policy = VerificationPolicy("test-policy", "v1", ("integrity", "record_identity"))
    assessment = assess(source.source_id, checks, policy)
    assert assessment.status != "verified"
    with pytest.raises(SourceVerificationError):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=policy,
        )
    with pytest.raises(SourceVerificationError, match="policy does not match"):
        authorize_status(
            source_id=source.source_id,
            content_digest=doc.content_hash,
            status="verified",
            assessment=assessment,
            checks=checks,
            policy=canonical_policy(),
        )
