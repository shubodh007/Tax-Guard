"""Envelope invariant tests using SYNTHETIC records only.

Nothing here is a real legal source, citation, or content hash. Fixtures
demonstrate the contract in `docs/evidence-trace-contract.md`.
No external services required.
"""

import json
from decimal import Decimal

import pytest

from backend.app.applicability import NO_MATCHING_RULE, Refused, Resolved
from backend.app.calculation import CalculationResult
from backend.app.evidence import (
    TRACE_SCHEMA_VERSION,
    CalculationRef,
    ClaimRef,
    EvidenceRef,
    EvidenceTrace,
    LegalBasis,
    TraceError,
)

FRAMEWORK = "SYNTHETIC TEST FIXTURE — not Indian tax law"
PROVENANCE = "SYNTHETIC TEST FIXTURE — not a legal source"
RULESET_ID = "synthetic-mini-v0"


def synthetic_resolved() -> Resolved:
    return Resolved(
        framework=FRAMEWORK,
        target_ruleset_id=RULESET_ID,
        rule_id="synthetic-rule-A",
        rule_version="v1",
        period="2024-25",
        jurisdiction="IN",
        facts=(),
        provenance=PROVENANCE,
        reason="exact-match rule synthetic-rule-A v1",
        records_id="synthetic-applicability-v0",
        records_version="v0",
    )


def synthetic_refusal() -> Refused:
    return Refused(
        code=NO_MATCHING_RULE,
        message="synthetic refusal",
        period="2024-25",
        records_id="synthetic-applicability-v0",
    )


def synthetic_evidence() -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(
            "syn-ev-1",
            document_id="synthetic-doc-1",
            section="synthetic-section-1",
            passage_id="synthetic-passage-1",
            trust="synthetic",
        ),
        EvidenceRef("syn-ev-2", trust="synthetic"),
    )


def supported_claim() -> ClaimRef:
    return ClaimRef(
        "syn-claim-1",
        role="conclusion",
        supporting_ids=("syn-ev-1",),
        status="supported",
        reason="synthetic-support",
    )


def synthetic_calc_result() -> CalculationResult:
    return CalculationResult(
        total_tax=Decimal("100"),
        taxable_income=Decimal("1000"),
        ruleset_id=RULESET_ID,
        framework=FRAMEWORK,
        period="2024-25",
        regime="synthetic-mini",
        lines=(),
        assumptions=(),
        rounding_applied="ROUND_HALF_UP to 1",
    )


def verified_trace() -> EvidenceTrace:
    return EvidenceTrace(
        trace_id="syn-trace-1",
        schema_version=TRACE_SCHEMA_VERSION,
        status="verified",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
        evidence=synthetic_evidence(),
        claims=(supported_claim(),),
        calculation=CalculationRef.from_result(synthetic_calc_result(), "syn-calc-1"),
    )


def test_valid_verified_trace_constructs() -> None:
    trace = verified_trace()
    assert trace.schema_version == 1
    assert trace.legal_basis.target_ruleset_id == RULESET_ID
    assert trace.calculation is not None
    assert trace.calculation.ruleset_id == RULESET_ID


def test_unsupported_schema_version_rejected() -> None:
    with pytest.raises(TraceError, match="schema version"):
        EvidenceTrace(
            trace_id="syn-trace-1",
            schema_version=999,
            status="draft",
            created_at="2026-01-01T00:00:00Z",
            legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
        )
    bad = verified_trace().to_dict()
    bad["schema_version"] = 2
    with pytest.raises(TraceError, match="schema version"):
        EvidenceTrace.from_dict(bad)


def test_malformed_identifiers_rejected() -> None:
    with pytest.raises(TraceError):
        EvidenceTrace(
            trace_id="  ",
            schema_version=1,
            status="draft",
            created_at="2026-01-01T00:00:00Z",
            legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
        )
    with pytest.raises(TraceError):
        EvidenceRef("  ")
    with pytest.raises(TraceError):
        ClaimRef("  ", role="conclusion")
    with pytest.raises(TraceError):
        EvidenceRef("syn-ev-1", trust="authoritative")
    with pytest.raises(TraceError):
        ClaimRef("syn-claim-1", role="conclusion", status="maybe")
    with pytest.raises(TraceError):
        LegalBasis.from_resolved(synthetic_refusal())  # type: ignore[arg-type]
    with pytest.raises(TraceError):
        LegalBasis.from_refusal(synthetic_resolved())  # type: ignore[arg-type]


def test_duplicate_identifiers_rejected() -> None:
    with pytest.raises(TraceError, match="duplicate evidence"):
        EvidenceTrace(
            trace_id="syn-trace-1",
            schema_version=1,
            status="draft",
            created_at="2026-01-01T00:00:00Z",
            legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
            evidence=(EvidenceRef("syn-ev-1"), EvidenceRef("syn-ev-1")),
        )
    with pytest.raises(TraceError, match="duplicate claim"):
        EvidenceTrace(
            trace_id="syn-trace-1",
            schema_version=1,
            status="draft",
            created_at="2026-01-01T00:00:00Z",
            legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
            claims=(supported_claim(), supported_claim()),
            evidence=synthetic_evidence(),
        )


def test_unknown_evidence_reference_rejected() -> None:
    with pytest.raises(TraceError, match="unknown evidence"):
        EvidenceTrace(
            trace_id="syn-trace-1",
            schema_version=1,
            status="draft",
            created_at="2026-01-01T00:00:00Z",
            legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
            evidence=synthetic_evidence(),
            claims=(supported_claim(),
                    ClaimRef("syn-claim-2", role="citation",
                             supporting_ids=("syn-ev-missing",))),
        )


def test_claim_evidence_citation_rules() -> None:
    with pytest.raises(TraceError, match="supporting evidence"):
        ClaimRef("syn-claim-1", role="conclusion", status="supported", reason="synthetic")
    with pytest.raises(TraceError, match="contradicting evidence"):
        ClaimRef("syn-claim-1", role="conclusion", status="contradicted", reason="synthetic")
    # Other states need no citations but always need a reason.
    ClaimRef("syn-claim-1", role="conclusion", status="insufficient_evidence",
             reason="synthetic-gap")
    ClaimRef("syn-claim-1", role="conclusion", status="verification_failed",
             reason="synthetic-provider-failure")


def test_non_verified_claim_states_representable_as_partial() -> None:
    trace = EvidenceTrace(
        trace_id="syn-trace-1",
        schema_version=1,
        status="partial",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis.from_resolved(synthetic_resolved()),
        evidence=synthetic_evidence(),
        claims=(
            supported_claim(),
            ClaimRef("syn-claim-2", role="citation", supporting_ids=("syn-ev-2",),
                     contradicting_ids=("syn-ev-1",), status="contradicted",
                     reason="synthetic-conflict"),
            ClaimRef("syn-claim-3", role="citation", status="insufficient_evidence",
                     reason="synthetic-gap"),
        ),
    )
    assert [c.status for c in trace.claims] == ["supported", "contradicted", "insufficient_evidence"]


def test_verified_status_invariants() -> None:
    basis = LegalBasis.from_resolved(synthetic_resolved())
    # Unverified claim present.
    with pytest.raises(TraceError, match="verified status"):
        EvidenceTrace("syn-trace-1", 1, "verified", "2026-01-01T00:00:00Z", basis,
                      synthetic_evidence(), (supported_claim(), ClaimRef("syn-claim-2", role="note")))
    # Contradicted claim present.
    with pytest.raises(TraceError, match="verified status"):
        EvidenceTrace("syn-trace-1", 1, "verified", "2026-01-01T00:00:00Z", basis,
                      synthetic_evidence(),
                      (supported_claim(),
                       ClaimRef("syn-claim-2", role="note", contradicting_ids=("syn-ev-1",),
                                status="contradicted", reason="synthetic")))
    # No claims at all.
    with pytest.raises(TraceError, match="verified status"):
        EvidenceTrace("syn-trace-1", 1, "verified", "2026-01-01T00:00:00Z", basis)
    # All-supported claims marked partial is equally contradictory.
    with pytest.raises(TraceError, match="partial status"):
        EvidenceTrace("syn-trace-1", 1, "partial", "2026-01-01T00:00:00Z", basis,
                      synthetic_evidence(), (supported_claim(),))


def test_refused_applicability_representation() -> None:
    refused = EvidenceTrace(
        trace_id="syn-trace-1",
        schema_version=1,
        status="refused",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis.from_refusal(synthetic_refusal()),
    )
    assert refused.legal_basis.resolved is False
    assert refused.legal_basis.refusal_code == NO_MATCHING_RULE
    assert refused.claims == () and refused.calculation is None

    # A refusal must never be encoded as a successful basis or status.
    with pytest.raises(TraceError):
        EvidenceTrace("syn-trace-1", 1, "verified", "2026-01-01T00:00:00Z",
                      LegalBasis.from_refusal(synthetic_refusal()))
    with pytest.raises(TraceError):
        EvidenceTrace("syn-trace-1", 1, "refused", "2026-01-01T00:00:00Z",
                      LegalBasis.from_resolved(synthetic_resolved()))
    with pytest.raises(TraceError):
        EvidenceTrace("syn-trace-1", 1, "draft", "2026-01-01T00:00:00Z",
                      LegalBasis.from_refusal(synthetic_refusal()))


def test_calculation_identity_mismatch_rejected() -> None:
    basis = LegalBasis.from_resolved(synthetic_resolved())
    other_framework = CalculationRef("syn-calc-1", 1, "SOME OTHER FRAMEWORK", RULESET_ID)
    with pytest.raises(TraceError, match="framework"):
        EvidenceTrace("syn-trace-1", 1, "draft", "2026-01-01T00:00:00Z", basis,
                      calculation=other_framework)
    other_ruleset = CalculationRef("syn-calc-1", 1, FRAMEWORK, "some-other-ruleset")
    with pytest.raises(TraceError, match="ruleset"):
        EvidenceTrace("syn-trace-1", 1, "draft", "2026-01-01T00:00:00Z", basis,
                      calculation=other_ruleset)
    with pytest.raises(TraceError, match="resolved legal basis"):
        EvidenceTrace("syn-trace-1", 1, "refused", "2026-01-01T00:00:00Z",
                      LegalBasis.from_refusal(synthetic_refusal()),
                      calculation=CalculationRef.from_result(synthetic_calc_result(), "syn-calc-1"))


def test_serialization_round_trip() -> None:
    trace = verified_trace()
    restored = EvidenceTrace.from_dict(json.loads(json.dumps(trace.to_dict())))
    assert restored == trace
    assert restored.evidence[0].trust == "synthetic"

    refused = EvidenceTrace(
        trace_id="syn-trace-2", schema_version=1, status="refused",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis.from_refusal(synthetic_refusal()),
    )
    assert EvidenceTrace.from_dict(json.loads(json.dumps(refused.to_dict()))) == refused


def test_from_dict_rejects_malformed() -> None:
    good = verified_trace().to_dict()
    bad_version = dict(good, schema_version="1")
    with pytest.raises(TraceError, match="schema version"):
        EvidenceTrace.from_dict(bad_version)
    bad_evidence = verified_trace().to_dict()
    bad_evidence["evidence"] = [{"document_id": "no-id"}]
    with pytest.raises(TraceError):
        EvidenceTrace.from_dict(bad_evidence)
    bad_types = verified_trace().to_dict()
    bad_types["evidence"][0]["document_id"] = 5
    with pytest.raises(TraceError):
        EvidenceTrace.from_dict(bad_types)
    bad_claim = verified_trace().to_dict()
    bad_claim["claims"][0]["supporting_ids"] = ["syn-ev-absent"]
    with pytest.raises(TraceError, match="unknown evidence"):
        EvidenceTrace.from_dict(bad_claim)
    with pytest.raises(TraceError):
        EvidenceTrace.from_dict([])  # type: ignore[arg-type]


def test_deterministic_construction() -> None:
    assert verified_trace() == verified_trace()
