"""Source-verification tests using SYNTHETIC records only.

Nothing here verifies a real source, and no check output is official.
No network, downloads, or external services are involved.
"""

import json

import pytest

from backend.app.source_verification import (
    Assessment,
    VerificationCheck,
    VerificationPolicy,
    assess,
)
from backend.app.source_verification import SourceVerificationError as CheckError
from backend.app.sources import SourceRecord, SourceRegistry


def syn_check(check_id: str, check_type: str, outcome: str, **overrides) -> VerificationCheck:
    fields = {
        "check_id": check_id,
        "source_id": "syn-source-1",
        "check_type": check_type,
        "outcome": outcome,
        "description": f"synthetic {check_type} check scope",
        "observation": f"synthetic {outcome} observation",
    }
    fields.update(overrides)
    return VerificationCheck(**fields)  # type: ignore[arg-type]


def policy(*required: str) -> VerificationPolicy:
    return VerificationPolicy(
        policy_id="synthetic-policy", version="v1", required_checks=required or ("origin", "integrity")
    )


def test_valid_record_all_categories() -> None:
    for category in ("origin", "integrity", "version", "completeness", "applicability"):
        for outcome in ("passed", "failed", "inconclusive", "not_checked"):
            record = syn_check(f"syn-{category}-{outcome}", category, outcome)
            assert record.check_type == category and record.outcome == outcome
            assert record.checked_at == "" and record.content_hash == ""


def test_malformed_records_rejected() -> None:
    with pytest.raises(CheckError):
        syn_check("  ", "origin", "passed")
    with pytest.raises(CheckError):
        syn_check("syn-1", "seal-of-approval", "passed")
    with pytest.raises(CheckError):
        syn_check("syn-1", "origin", "probably-fine")
    with pytest.raises(CheckError):
        syn_check("syn-1", "origin", "passed", description="  ")
    with pytest.raises(CheckError):
        syn_check("syn-1", "origin", "passed", observation="  ")
    with pytest.raises(CheckError):
        syn_check("syn-1", "integrity", "passed", content_hash="not-a-hash")
    with pytest.raises(CheckError):
        syn_check("syn-1", "integrity", "passed", observation="x" * 4001)
    with pytest.raises(CheckError):
        VerificationPolicy("p", "v", ())
    with pytest.raises(CheckError):
        VerificationPolicy("p", "v", ("origin", "origin"))
    with pytest.raises(CheckError):
        VerificationPolicy("p", "v", ("astrology",))


def test_missing_required_checks_incomplete() -> None:
    assessment = assess("syn-source-1", (), policy())
    assert assessment.status == "incomplete"
    assert assessment.reason_codes == ("missing:origin", "missing:integrity")
    assert assessment.check_ids == ()


def test_integrity_alone_never_verifies() -> None:
    assessment = assess(
        "syn-source-1", (syn_check("syn-1", "integrity", "passed"),), policy()
    )
    assert assessment.status == "incomplete"
    assert "missing:origin" in assessment.reason_codes
    assert "satisfied:integrity" in assessment.reason_codes


def test_verified_authenticity_without_applicability() -> None:
    assessment = assess(
        "syn-source-1",
        (
            syn_check("syn-1", "origin", "passed"),
            syn_check("syn-2", "integrity", "passed", content_hash="a" * 64),
        ),
        policy(),
    )
    assert assessment.status == "verified"
    assert not any("applicability" in code for code in assessment.reason_codes)
    verdicts = {v.category: v.verdict for v in assessment.verdicts}
    assert verdicts["origin"] == "satisfied" and verdicts["applicability"] == "not_required"


def test_contradictory_and_failed_observations() -> None:
    conflict = assess(
        "syn-source-1",
        (
            syn_check("syn-1", "origin", "passed"),
            syn_check("syn-2", "origin", "failed"),
            syn_check("syn-3", "integrity", "passed"),
        ),
        policy(),
    )
    assert conflict.status == "conflicting"
    assert "conflicting:origin" in conflict.reason_codes

    failed = assess("syn-source-1", (syn_check("syn-1", "origin", "failed"),), policy())
    assert failed.status == "failed"
    assert "failed:origin" in failed.reason_codes

    uncertain = assess(
        "syn-source-1", (syn_check("syn-1", "origin", "inconclusive"),), policy()
    )
    assert uncertain.status == "incomplete"
    assert "uncertain:origin" in uncertain.reason_codes


def test_non_required_failure_blocks_verified() -> None:
    assessment = assess(
        "syn-source-1",
        (
            syn_check("syn-1", "origin", "passed"),
            syn_check("syn-2", "integrity", "passed"),
            syn_check("syn-3", "completeness", "failed"),
        ),
        policy(),
    )
    assert assessment.status == "failed"
    assert "failed:completeness" not in assessment.reason_codes  # not required: no code claimed
    verdicts = {v.category: v.verdict for v in assessment.verdicts}
    assert verdicts["completeness"] == "failed"


def test_deterministic_results() -> None:
    checks = (
        syn_check("syn-b", "integrity", "passed"),
        syn_check("syn-a", "origin", "passed"),
    )
    first, second = assess("syn-source-1", checks, policy()), assess("syn-source-1", checks, policy())
    assert first == second
    assert first.check_ids == ("syn-a", "syn-b")
    assert first.reason_codes == ("satisfied:origin", "satisfied:integrity")


def test_serialization_round_trip() -> None:
    record = syn_check("syn-1", "integrity", "passed", content_hash="b" * 64,
                       verifier="synthetic reviewer", limitations="synthetic scope note")
    assert VerificationCheck.from_dict(json.loads(json.dumps(record.to_dict()))) == record
    assert VerificationCheck.from_dict(record.to_dict()).checked_at == ""

    assessment = assess("syn-source-1", (record,), policy("integrity"))
    assert json.loads(json.dumps(assessment.to_dict()))["status"] == "verified"
    assert assessment.reason_codes == ("satisfied:integrity",)
    with pytest.raises(CheckError):
        VerificationCheck.from_dict({"check_id": "syn-1"})


def test_hash_linkage_accepted_only_when_valid() -> None:
    linked = syn_check("syn-1", "integrity", "passed", content_hash="c" * 64)
    assert linked.content_hash == "c" * 64
    unlinked = syn_check("syn-2", "origin", "passed")
    assert unlinked.content_hash == ""


def test_no_default_timestamps_or_invention() -> None:
    record = syn_check("syn-1", "origin", "not_checked", observation="not yet performed")
    assert record.checked_at == "" and record.verifier == ""
    assert VerificationCheck.from_dict(record.to_dict()) == record


def test_existing_records_never_mutated() -> None:
    registry = SourceRegistry()
    source = SourceRecord(source_id="syn-source-1", title="Synthetic test source")
    registry.register(source)
    snapshot = source.to_dict()
    assess("syn-source-1", (syn_check("syn-1", "origin", "passed"),), policy())
    assert source.to_dict() == snapshot
    assert registry.get("syn-source-1") is source
    assert source.verification == "unverified"


def test_assessment_input_validation() -> None:
    with pytest.raises(CheckError, match="duplicate check"):
        assess("syn-source-1", (syn_check("syn-1", "origin", "passed"),
                                syn_check("syn-1", "integrity", "passed")), policy())
    with pytest.raises(CheckError, match="belongs to source"):
        assess("syn-source-2", (syn_check("syn-1", "origin", "passed"),), policy())
    with pytest.raises(CheckError):
        assess("syn-source-1", ("not-a-check",), policy())  # type: ignore[arg-type]
    result = assess("syn-source-1", [], policy())
    assert isinstance(result, Assessment) and result.status == "incomplete"


def test_gov_looking_origin_never_auto_verifies() -> None:
    source = SourceRecord(
        source_id="syn-source-1",
        title="Synthetic test source",
        origin="https://example.invalid/synthetic-act",
    )
    registry = SourceRegistry()
    registry.register(source)
    assessment = assess("syn-source-1", (), policy("origin"))
    assert assessment.status == "incomplete"
    assert assessment.reason_codes == ("missing:origin",)
