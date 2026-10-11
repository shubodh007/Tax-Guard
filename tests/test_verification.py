"""Verification interface tests using SYNTHETIC fixtures only.

Nothing here performs real semantic verification or states tax law.
Policies return configured outcomes; the `fault` switch simulates
operational failures without calling any provider.
No external services required.
"""

import pytest

from backend.app.applicability import NO_MATCHING_RULE, Refused
from backend.app.evidence import (
    TRACE_SCHEMA_VERSION,
    CalculationRef,
    ClaimRef,
    EvidenceRef,
    EvidenceTrace,
    LegalBasis,
    TraceError,
)
from backend.app.verification import (
    EVALUATED,
    INVALID_INPUT,
    MISSING_EVIDENCE,
    PROVIDER_FAILURE,
    QUOTA_EXHAUSTED,
    TIMEOUT,
    UNSUPPORTED_OPERATION,
    VerificationError,
    VerificationOutcome,
    VerificationPolicy,
    apply_outcome,
    verify_claim,
)

FRAMEWORK = "SYNTHETIC TEST FIXTURE — not Indian tax law"
RULESET_ID = "synthetic-mini-v0"


def policy(**overrides) -> VerificationPolicy:
    fields = {
        "policy_id": "synthetic-policy",
        "version": "v1",
        "supported_claim_ids": ("syn-claim-1",),
        "contradicted_claim_ids": ("syn-claim-2",),
    }
    fields.update(overrides)
    return VerificationPolicy(**fields)  # type: ignore[arg-type]


def evidence_subset() -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef("syn-ev-1", document_id="synthetic-doc-1", trust="synthetic"),
        EvidenceRef("syn-ev-2", trust="unverified"),
    )


def support_claim() -> ClaimRef:
    return ClaimRef("syn-claim-1", role="conclusion", supporting_ids=("syn-ev-1",))


def contradict_claim() -> ClaimRef:
    return ClaimRef(
        "syn-claim-2", role="citation", contradicting_ids=("syn-ev-2",),
        supporting_ids=("syn-ev-1",),
    )


def basis() -> LegalBasis:
    return LegalBasis(
        resolved=True, framework=FRAMEWORK, target_ruleset_id=RULESET_ID,
        rule_id="synthetic-rule-A", rule_version="v1", period="2024-25",
        records_id="synthetic-applicability-v0", records_version="v0",
        provenance="SYNTHETIC TEST FIXTURE — not a legal source",
    )


def calc_ref() -> CalculationRef:
    return CalculationRef("syn-calc-1", 1, FRAMEWORK, RULESET_ID)


def draft_trace() -> EvidenceTrace:
    return EvidenceTrace(
        trace_id="syn-trace-1", schema_version=TRACE_SCHEMA_VERSION, status="draft",
        created_at="2026-01-01T00:00:00Z", legal_basis=basis(),
        evidence=evidence_subset(),
        claims=(support_claim(), contradict_claim()),
    )


def test_supported_outcome() -> None:
    outcome = verify_claim(support_claim(), evidence_subset(), policy())

    assert outcome.conclusion == "supported"
    assert outcome.code == EVALUATED
    assert (outcome.policy_id, outcome.policy_version) == ("synthetic-policy", "v1")
    assert outcome.evidence_ids == ("syn-ev-1", "syn-ev-2")
    assert "synthetic-policy v1" in outcome.message


def test_contradicted_outcome_keeps_supporting_evidence() -> None:
    outcome = verify_claim(contradict_claim(), evidence_subset(), policy())

    assert outcome.conclusion == "contradicted"
    assert outcome.code == EVALUATED
    assert outcome.evidence_ids == ("syn-ev-1", "syn-ev-2")


def test_missing_and_empty_evidence() -> None:
    partial = (EvidenceRef("syn-ev-2", trust="unverified"),)
    outcome = verify_claim(support_claim(), partial, policy())
    assert (outcome.conclusion, outcome.code) == ("insufficient_evidence", MISSING_EVIDENCE)
    assert "syn-ev-1" in outcome.message

    outcome = verify_claim(support_claim(), (), policy())
    assert (outcome.conclusion, outcome.code) == ("insufficient_evidence", MISSING_EVIDENCE)


def test_uncovered_claim_is_unverified() -> None:
    claim = ClaimRef("syn-claim-9", role="note", supporting_ids=("syn-ev-1",))
    outcome = verify_claim(claim, evidence_subset(), policy())
    assert (outcome.conclusion, outcome.code) == ("unverified", UNSUPPORTED_OPERATION)


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(VerificationError):
        verify_claim("not-a-claim", evidence_subset(), policy())  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        verify_claim(support_claim(), (EvidenceRef("syn-ev-1"), EvidenceRef("syn-ev-1")), policy())
    with pytest.raises(VerificationError):
        verify_claim(support_claim(), ("syn-ev-1",), policy())  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        VerificationPolicy(policy_id="p", version="v",
                           supported_claim_ids=("a",), contradicted_claim_ids=("a",))
    with pytest.raises(VerificationError):
        VerificationPolicy(policy_id="p", version="v", fault="meltdown")


@pytest.mark.parametrize("fault,code", [
    (PROVIDER_FAILURE, PROVIDER_FAILURE),
    (QUOTA_EXHAUSTED, QUOTA_EXHAUSTED),
    (TIMEOUT, TIMEOUT),
])
def test_simulated_faults_never_support(fault: str, code: str) -> None:
    outcome = verify_claim(support_claim(), evidence_subset(), policy(fault=fault))

    assert outcome.code == code
    assert outcome.conclusion == "verification_failed"
    assert outcome.conclusion != "supported"


def test_invalid_outcome_combinations_rejected() -> None:
    base = {"claim_id": "c", "message": "m", "policy_id": "p", "policy_version": "v"}
    with pytest.raises(VerificationError):
        VerificationOutcome(conclusion="unverified", code=EVALUATED, **base)  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        VerificationOutcome(conclusion="supported", code=PROVIDER_FAILURE, **base)  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        VerificationOutcome(conclusion="supported", code=QUOTA_EXHAUSTED, **base)  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        VerificationOutcome(conclusion="insufficient_evidence", code=INVALID_INPUT, **base)  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        VerificationOutcome(conclusion="supported", code="bogus", **base)  # type: ignore[arg-type]


def test_calculation_absent_for_non_numerical_claim() -> None:
    outcome = verify_claim(support_claim(), evidence_subset(), policy())
    assert outcome.calculation_id == ""
    assert outcome.conclusion == "supported"


def test_calculation_requirement_and_identity() -> None:
    calc_policy = policy(require_calculation_for=("syn-claim-1",))

    missing = verify_claim(support_claim(), evidence_subset(), calc_policy)
    assert (missing.conclusion, missing.code) == ("unverified", UNSUPPORTED_OPERATION)

    ok = verify_claim(support_claim(), evidence_subset(), calc_policy,
                      calculation=calc_ref(), legal_basis=basis())
    assert ok.conclusion == "supported"
    assert ok.calculation_id == "syn-calc-1"

    wrong = CalculationRef("syn-calc-1", 1, FRAMEWORK, "some-other-ruleset")
    mismatch = verify_claim(support_claim(), evidence_subset(), calc_policy,
                            calculation=wrong, legal_basis=basis())
    assert (mismatch.conclusion, mismatch.code) == ("unverified", INVALID_INPUT)

    no_basis = verify_claim(support_claim(), evidence_subset(), calc_policy,
                            calculation=calc_ref())
    assert (no_basis.conclusion, no_basis.code) == ("unverified", INVALID_INPUT)


def test_trust_labels_preserved() -> None:
    outcome = verify_claim(support_claim(), evidence_subset(), policy())
    assert [e.trust for e in evidence_subset()] == ["synthetic", "unverified"]

    updated = apply_outcome(draft_trace(), outcome)
    assert [e.trust for e in updated.evidence] == ["synthetic", "unverified"]


def test_apply_supported_outcome_recomputes_overall() -> None:
    updated = apply_outcome(draft_trace(), verify_claim(support_claim(), evidence_subset(), policy()))

    assert updated.claims[0].status == "supported"
    assert "synthetic-policy v1" in updated.claims[0].reason
    assert updated.claims[0].claim_id == "syn-claim-1"
    assert updated.status == "partial"  # second claim still unverified
    assert updated.legal_basis == basis()

    both = apply_outcome(updated, verify_claim(
        contradict_claim(), evidence_subset(),
        policy(supported_claim_ids=("syn-claim-1", "syn-claim-2"),
               contradicted_claim_ids=()),
    ))
    assert both.status == "verified"


def test_apply_failure_never_verifies() -> None:
    failed = verify_claim(support_claim(), evidence_subset(), policy(fault=QUOTA_EXHAUSTED))
    updated = apply_outcome(draft_trace(), failed)

    assert updated.claims[0].status == "verification_failed"
    assert updated.status == "partial"
    assert updated.status != "verified"


def test_apply_guards() -> None:
    refused_trace = EvidenceTrace(
        trace_id="syn-trace-1", schema_version=TRACE_SCHEMA_VERSION, status="refused",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis.from_refusal(Refused(
            code=NO_MATCHING_RULE, message="synthetic refusal",
            period="2024-25", records_id="synthetic-applicability-v0")),
    )
    with pytest.raises(VerificationError, match="unresolved legal basis"):
        apply_outcome(refused_trace, verify_claim(support_claim(), evidence_subset(), policy()))

    unknown = VerificationOutcome(claim_id="syn-claim-9", conclusion="unverified",
                                  code=UNSUPPORTED_OPERATION, message="synthetic",
                                  policy_id="synthetic-policy", policy_version="v1")
    with pytest.raises(VerificationError, match="unknown claim"):
        apply_outcome(draft_trace(), unknown)


def test_verified_blocked_while_material_claims_pending() -> None:
    trace = draft_trace()
    assert trace.status == "draft"
    with pytest.raises(TraceError, match="verified status"):
        EvidenceTrace(trace_id=trace.trace_id, schema_version=trace.schema_version,
                      status="verified", created_at=trace.created_at,
                      legal_basis=trace.legal_basis, evidence=trace.evidence,
                      claims=trace.claims, calculation=trace.calculation)


def test_deterministic_repeated_verification() -> None:
    first = verify_claim(support_claim(), evidence_subset(), policy())
    second = verify_claim(support_claim(), evidence_subset(), policy())
    assert first == second
