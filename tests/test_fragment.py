"""Fragment-pilot tests using SYNTHETIC local fixtures only.

These prove the fragment workflow mechanics (scoped records, PDF
preservation, supported-format ingestion, honest checks). They are not
evidence that any real official fragment was acquired — the real pilot
is reported in docs/source-fragment-acquisition.md. No live network.
"""

import hashlib

import pytest

from backend.app.acquisition import (
    AcquisitionReceipt,
    integrity_check,
    origin_observation,
)
from backend.app.source_verification import VerificationPolicy, assess
from backend.app.sources import IngestionError, SourceRecord, SourceRegistry, ingest_file

FRAGMENT_ID = "syn-source-fragment-1"
SYN_PDF = b"%PDF-1.4 synthetic fragment bytes for tests only"
SYN_TXT = b"Synthetic schedule provision one.\n\nSynthetic schedule provision two.\n"


def fragment_source(**overrides) -> SourceRecord:
    fields = {
        "source_id": FRAGMENT_ID,
        "title": "SYNTHETIC FIRST SCHEDULE",
        "publisher": "Synthetic Test Board",
        "origin": "https://example.invalid/syn-act",
        "source_type": "schedule",
        "jurisdiction": "IN",
        "note": (
            "Schedule of the Synthetic Test Act (Test No. 43 of 1961) per test record; "
            "fragment only, not the complete Act. Authenticity not established."
        ),
    }
    fields.update(overrides)
    return SourceRecord(**fields)  # type: ignore[arg-type]


def fragment_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(fragment_source())
    return registry


def fragment_receipt(**overrides) -> AcquisitionReceipt:
    sha = hashlib.sha256(SYN_PDF).hexdigest()
    fields = {
        "source_id": FRAGMENT_ID,
        "artifact_id": f"{FRAGMENT_ID}:{sha[:32]}",
        "title": "SYNTHETIC FIRST SCHEDULE",
        "publisher": "Synthetic Test Board",
        "source_page_url": "https://example.invalid/syn-record",
        "artifact_url": "https://example.invalid/syn.pdf",
        "final_url": "https://example.invalid/syn.pdf",
        "redirect_chain": (),
        "acquired_at": "2026-10-09T00:00:00+00:00",
        "http_status": 200,
        "content_type": "application/pdf",
        "filename": "syn-schedule.pdf",
        "size_bytes": len(SYN_PDF),
        "sha256": sha,
        "format": "pdf",
        "parsed": False,
        "document_id": "",
        "verification": "unverified",
        "limitations": ("synthetic test receipt; fragment scope only",),
    }
    fields.update(overrides)
    return AcquisitionReceipt(**fields)  # type: ignore[arg-type]


def test_fragment_scope_explicit() -> None:
    source = fragment_source()
    assert source.source_id == FRAGMENT_ID
    assert source.title == "SYNTHETIC FIRST SCHEDULE"
    assert "not the complete Act" in source.note
    assert source.verification == "unverified"

    receipt = fragment_receipt()
    assert receipt.source_id == FRAGMENT_ID
    assert receipt.parsed is False and receipt.document_id == ""
    assert receipt.limitations != ()


def test_pdf_fragment_preserved_unparsed(tmp_path) -> None:
    target = tmp_path / "syn-schedule.pdf"
    target.write_bytes(SYN_PDF)
    with pytest.raises(IngestionError, match="readable PDF"):
        ingest_file(fragment_registry(), FRAGMENT_ID, target)
    assert target.read_bytes() == SYN_PDF
    foreign = tmp_path / "syn-schedule.xyz"
    foreign.write_bytes(SYN_PDF)
    with pytest.raises(IngestionError, match="unsupported"):
        ingest_file(fragment_registry(), FRAGMENT_ID, foreign)
    assert foreign.read_bytes() == SYN_PDF


def test_supported_fragment_ingested_with_provenance(tmp_path) -> None:
    target = tmp_path / "syn-schedule.txt"
    target.write_bytes(SYN_TXT)
    record = ingest_file(fragment_registry(), FRAGMENT_ID, target)

    assert record.source_id == FRAGMENT_ID
    assert record.document_id == f"{FRAGMENT_ID}:{hashlib.sha256(SYN_TXT).hexdigest()[:32]}"
    assert record.content_hash == hashlib.sha256(SYN_TXT).hexdigest()
    assert record.source_verification == "unverified"
    assert record.filename == "syn-schedule.txt"
    assert "provision two" in record.normalized_text


def test_no_fabricated_verification_evidence() -> None:
    sha = "0" * 64
    integrity = integrity_check(FRAGMENT_ID, "syn-check-1", sha, 4)
    assert integrity.outcome == "passed" and integrity.check_type == "integrity"
    origin = origin_observation(FRAGMENT_ID, "syn-check-2", "https://example.invalid/syn.pdf", 200)
    assert origin.outcome == "inconclusive"  # construction cannot auto-pass origin

    assessment = assess(
        FRAGMENT_ID,
        (integrity, origin),
        VerificationPolicy("synthetic-policy", "v1", ("origin", "integrity")),
    )
    assert assessment.status == "incomplete"
    assert assessment.reason_codes == ("uncertain:origin", "satisfied:integrity")
    assert not any("applicability" in code for code in assessment.reason_codes)


def test_registry_and_contracts_intact() -> None:
    registry = fragment_registry()
    source = registry.get(FRAGMENT_ID)
    assert source is not None and source.verification == "unverified"
    snapshot = source.to_dict()
    assess(
        FRAGMENT_ID,
        (integrity_check(FRAGMENT_ID, "syn-check-1", "0" * 64, 4),),
        VerificationPolicy("synthetic-policy", "v1", ("integrity",)),
    )
    current = registry.get(FRAGMENT_ID)
    assert current is not None
    assert current.to_dict() == snapshot
    assert fragment_receipt().verification == "unverified"
