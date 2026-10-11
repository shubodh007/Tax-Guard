"""Pilot controls batch tests (Phase 30, Workstreams 3 + 4).

Real-corpus tests are read-only. Adversarial cases use synthetic
temporary fixtures only. No network access is required by any test
here: live probes from the pilot are recorded in
docs/acquisition-pilot-30.md, not re-executed.
"""

import hashlib
import json
import os

import pytest

from backend.app.acquisition import (
    AcquisitionError,
    AcquisitionReceipt,
    store_artifact,
    verify_stored_artifact,
    write_receipt_file,
)
from backend.app.manifest import build_passages, load_manifest_file
from backend.app.provenance import evidence_ref_for, resolve_provenance
from backend.app.retrieval import retrieve_passages
from backend.app.sources import DocumentRecord, SourceRecord

FIRST_DIR = os.path.join("corpus", "india-code", "income-tax-act-1961-first-schedule")
SECOND_DIR = os.path.join("corpus", "india-code", "income-tax-act-1961-second-schedule")


def _real_maps():
    manifest = load_manifest_file(os.path.join("corpus", "manifest.json"))
    passages = build_passages(manifest, ".")
    documents, sources = {}, {}
    for directory in (FIRST_DIR, SECOND_DIR):
        with open(os.path.join(directory, "document.json"), encoding="utf-8") as handle:
            doc = DocumentRecord.from_dict(json.load(handle))
        with open(os.path.join(directory, "source.json"), encoding="utf-8") as handle:
            src = SourceRecord.from_dict(json.load(handle))
        documents[doc.document_id] = doc
        sources[src.source_id] = src
    return passages, documents, sources


def test_provenance_chain_intact_assessment_named_missing() -> None:
    """Every real passage resolves to its document and source by exact
    id; the absent assessment link is named, never fabricated; the
    downstream ref defaults to unverified trust."""
    passages, documents, sources = _real_maps()
    assert len(passages) == 91
    for passage in passages:
        resolved = resolve_provenance(passage, documents, sources)
        assert resolved.document_id in documents
        assert resolved.source_id in sources
        assert "assessment" in resolved.unresolved
        ref = evidence_ref_for(resolved)
        assert ref.trust == "unverified"
        assert ref.document_id == passage.document_id


def test_repeat_store_deterministic_and_tamper_refused(tmp_path) -> None:
    """Repeat acquisition output is deterministic (idempotent store);
    changed bytes and mismatched hashes are refused. Temporary dir only."""
    data = b"synthetic pilot bytes"
    first = store_artifact(data, tmp_path, "pilot.pdf")
    assert store_artifact(data, tmp_path, "pilot.pdf") == first
    verify_stored_artifact(first, hashlib.sha256(data).hexdigest())
    with open(first, "wb") as handle:
        handle.write(b"tampered")
    with pytest.raises(AcquisitionError, match="hash mismatch"):
        verify_stored_artifact(first, hashlib.sha256(data).hexdigest())
    with pytest.raises(AcquisitionError, match="refusing to overwrite"):
        store_artifact(b"different bytes", tmp_path, "pilot.pdf")


def test_receipt_metadata_edges_rejected_and_immutable(tmp_path) -> None:
    """Missing limitations metadata is rejected; receipts cannot be
    silently replaced. Temporary dir only."""
    receipt = AcquisitionReceipt(
        source_id="syn-pilot-s1",
        artifact_id="syn-pilot-s1:" + "0" * 32,
        title="Synthetic pilot artifact",
        publisher="",
        source_page_url="http://127.0.0.1/syn-page",
        artifact_url="http://127.0.0.1/syn.pdf",
        final_url="http://127.0.0.1/syn.pdf",
        redirect_chain=(),
        acquired_at="2026-10-09T00:00:00+00:00",
        http_status=200,
        content_type="",
        filename="syn.pdf",
        size_bytes=4,
        sha256="0" * 64,
        format="pdf",
        parsed=False,
        document_id="",
        verification="unverified",
        limitations=("synthetic pilot receipt",),
    )
    path = write_receipt_file(receipt, tmp_path)
    assert write_receipt_file(receipt, tmp_path) == path
    payload = receipt.to_dict()
    del payload["limitations"]
    with pytest.raises(AcquisitionError):
        AcquisitionReceipt.from_dict(payload)
    altered = AcquisitionReceipt(**{**receipt.to_dict(), "size_bytes": 5})
    with pytest.raises(AcquisitionError, match="refusing to overwrite"):
        write_receipt_file(altered, tmp_path)


def test_empty_retrieval_layer_abstains_cleanly() -> None:
    """Passages matching nothing return an empty tuple (the layer the
    answer path maps to abstention). Real passages, nonsense query."""
    passages, _, _ = _real_maps()
    assert retrieve_passages("xyzzy qwerty asdfgh", passages, limit=3) == ()
