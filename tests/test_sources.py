"""Source registry tests using SYNTHETIC records only.

Nothing here is a real legal source. No external services required.
"""

import pytest

from backend.app.sources import SourceError, SourceRecord, SourceRegistry


def test_valid_registration() -> None:
    registry = SourceRegistry()
    source = SourceRecord(
        source_id="syn-source-1",
        title="Synthetic test circular",
        publisher="Synthetic Test Board",
        origin="https://example.invalid/syn-1",
        source_type="circular",
        jurisdiction="IN",
        publication="synthetic series 1/2026",
        acquired_at="2026-01-01",
        note="recorded for tests; authenticity not established",
    )
    registry.register(source)
    assert registry.get("syn-source-1") is source


def test_unknowns_stay_unknown() -> None:
    source = SourceRecord(source_id="syn-source-1", title="Synthetic test note")
    assert source.publisher == "" and source.origin == ""
    assert source.verification == "unverified"
    assert SourceRecord.from_dict(source.to_dict()) == source


def test_invalid_metadata_rejected() -> None:
    with pytest.raises(SourceError):
        SourceRecord(source_id="  ", title="Synthetic")
    with pytest.raises(SourceError):
        SourceRecord(source_id="syn-1", title="  ")
    with pytest.raises(SourceError):
        SourceRecord(source_id="syn-1", title="Synthetic", verification="official")
    with pytest.raises(SourceError):
        SourceRecord(source_id="syn-1", title="Synthetic", verification="verified")
    with pytest.raises(SourceError):
        SourceRecord(source_id="syn-1", title="Synthetic", publisher=5)  # type: ignore[arg-type]
    SourceRecord(source_id="syn-1", title="Synthetic", verification="verified",
                 note="synthetic basis recorded for tests")


def test_duplicate_id_rejected_without_overwrite() -> None:
    registry = SourceRegistry()
    first = SourceRecord(source_id="syn-1", title="First synthetic record")
    registry.register(first)
    with pytest.raises(SourceError, match="already registered"):
        registry.register(SourceRecord(source_id="syn-1", title="Second synthetic record"))
    assert registry.get("syn-1") is first


def test_lookup_and_types() -> None:
    registry = SourceRegistry()
    assert registry.get("syn-absent") is None
    with pytest.raises(SourceError):
        registry.register("not-a-record")  # type: ignore[arg-type]


def test_serialization_round_trip_and_malformed() -> None:
    source = SourceRecord(source_id="syn-1", title="Synthetic", origin="https://example.invalid/x")
    assert SourceRecord.from_dict(source.to_dict()) == source
    with pytest.raises(SourceError):
        SourceRecord.from_dict([])  # type: ignore[arg-type]
    with pytest.raises(SourceError):
        SourceRecord.from_dict({"source_id": "syn-1"})
    with pytest.raises(SourceError):
        SourceRecord.from_dict({"source_id": "syn-1", "title": "S", "verification": "official"})
