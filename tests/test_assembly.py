"""Assembly tests using SYNTHETIC fixtures only.

Nothing here states tax law or performs real verification. Outcomes come
from the deterministic verification contract under configured policies.
No external services required.
"""

import json

import pytest

from backend.app.applicability import NO_MATCHING_RULE, Refused
from backend.app.assembly import (
    ANSWER_SCHEMA_VERSION,
    AnswerResult,
    AssemblyError,
    AssemblyPolicy,
    assemble_answer,
)
from backend.app.evidence import (
    TRACE_SCHEMA_VERSION,
    CalculationRef,
    ClaimRef,
    EvidenceRef,
    EvidenceTrace,
    LegalBasis,
)
from backend.app.verification import (
    PROVIDER_FAILURE,
    QUOTA_EXHAUSTED,
    TIMEOUT,
    VerificationOutcome,
    VerificationPolicy,
    verify_claim,
)

FRAMEWORK = "SYNTHETIC TEST FIXTURE — not Indian tax law"
RULESET_ID = "synthetic-mini-v0"


def resolved_basis() -> LegalBasis:
    return LegalBasis(
        resolved=True, framework=FRAMEWORK, target_ruleset_id=RULESET_ID,
        rule_id="synthetic-rule-A", rule_version="v1", period="2024-25",
        records_id="synthetic-applicability-v0", records_version="v0",
        provenance="SYNTHETIC TEST FIXTURE — not a legal source",
    )


def refused_basis() -> LegalBasis:
    return LegalBasis.from_refusal(Refused(
        code=NO_MATCHING_RULE, message="synthetic refusal",
        period="2024-25", records_id="synthetic-applicability-v0"))


def trace_evidence() -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef("syn-ev-1", document_id="synthetic-doc-1", trust="synthetic"),
        EvidenceRef("syn-ev-2", trust="synthetic"),
    )


def two_claim_trace(**overrides) -> EvidenceTrace:
    fields = {
        "trace_id": "syn-trace-1",
        "schema_version": TRACE_SCHEMA_VERSION,
        "status": "draft",
        "created_at": "2026-01-01T00:00:00Z",
        "legal_basis": resolved_basis(),
        "evidence": trace_evidence(),
        "claims": (
            ClaimRef("syn-claim-1", role="conclusion", supporting_ids=("syn-ev-1",)),
            ClaimRef("syn-claim-2", role="citation", supporting_ids=("syn-ev-2",)),
        ),
    }
    fields.update(overrides)
    return EvidenceTrace(**fields)  # type: ignore[arg-type]


def verify_policy(**overrides) -> VerificationPolicy:
    fields = {
        "policy_id": "synthetic-verify",
        "version": "v1",
        "supported_claim_ids": ("syn-claim-1", "syn-claim-2"),
    }
    fields.update(overrides)
    return VerificationPolicy(**fields)  # type: ignore[arg-type]


def assembly_policy(**overrides) -> AssemblyPolicy:
    fields = {"policy_id": "synthetic-assembly", "version": "v1"}
    fields.update(overrides)
    return AssemblyPolicy(**fields)  # type: ignore[arg-type]


def test_verified_answer() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace.evidence, verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy())

    assert answer.schema_version == ANSWER_SCHEMA_VERSION
    assert answer.status == "verified"
    assert answer.reason_code == "all_supported"
    assert answer.trace_id == "syn-trace-1"
    assert [c.claim_id for c in answer.claims] == ["syn-claim-1", "syn-claim-2"]
    assert all(c.included and c.conclusion == "supported" for c in answer.claims)
    assert answer.evidence_ids == ("syn-ev-1", "syn-ev-2")
    assert (answer.legal_resolved, answer.framework, answer.ruleset_id) == (True, FRAMEWORK, RULESET_ID)
    assert answer.calculation_id == ""
    assert answer.text == (
        "Verified answer covering 2 supported claims: syn-claim-1, syn-claim-2. "
        "Evidence: syn-ev-1, syn-ev-2. Ruleset: synthetic-mini-v0."
    )


def test_safe_partial_answer() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace_evidence()[:1], verify_policy()),
    )
    assert outcomes[1].conclusion == "insufficient_evidence"
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))

    assert answer.status == "partial"
    assert answer.reason_code == "partial_coverage"
    assert [c.included for c in answer.claims] == [True, False]
    assert answer.evidence_ids == ("syn-ev-1",)
    assert "incomplete" in answer.text
    assert "syn-claim-2:missing_evidence" in answer.text
    assert answer.trace_id == "syn-trace-1"


def test_partial_refused_without_permission() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace_evidence()[:1], verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy())

    assert answer.status == "refused"
    assert answer.reason_code == "partial_not_permitted"
    assert "not permitted" in answer.text


def test_refused_basis_stays_refused() -> None:
    trace = two_claim_trace(legal_basis=refused_basis(), status="refused")
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace.evidence, verify_policy()),
    )
    assert all(o.conclusion == "supported" for o in outcomes)
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))

    assert answer.status == "refused"
    assert answer.reason_code == NO_MATCHING_RULE
    assert answer.legal_resolved is False
    assert answer.framework == "" and answer.ruleset_id == ""


@pytest.mark.parametrize("fault,code", [
    (PROVIDER_FAILURE, PROVIDER_FAILURE),
    (QUOTA_EXHAUSTED, QUOTA_EXHAUSTED),
    (TIMEOUT, TIMEOUT),
])
def test_operational_failures_stay_failed(fault: str, code: str) -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace.evidence, verify_policy(fault=fault)),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))

    assert answer.status == "verification_failed"
    assert answer.reason_code == code
    assert "No answer can be given" in answer.text
    assert all(not c.included for c in answer.claims)


def test_contradiction_refuses_despite_supported_majority() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(
            ClaimRef("syn-claim-2", role="citation", contradicting_ids=("syn-ev-2",)),
            trace.evidence,
            verify_policy(supported_claim_ids=("syn-claim-1",),
                          contradicted_claim_ids=("syn-claim-2",)),
        ),
    )
    assert outcomes[1].conclusion == "contradicted"
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))

    assert answer.status == "refused"
    assert answer.reason_code == "unresolved_contradiction"
    assert "contradict" in answer.text


def test_draft_and_untolerated_unverified() -> None:
    trace = two_claim_trace()
    outcomes = (
        VerificationOutcome(claim_id="syn-claim-1", conclusion="unverified",
                            code="unsupported_operation", message="synthetic",
                            policy_id="synthetic-verify", policy_version="v1"),
        VerificationOutcome(claim_id="syn-claim-2", conclusion="unverified",
                            code="unsupported_operation", message="synthetic",
                            policy_id="synthetic-verify", policy_version="v1"),
    )
    draft = assemble_answer(trace, outcomes, assembly_policy(tolerate_unverified=True))
    assert draft.status == "draft"
    assert draft.reason_code == "verification_incomplete"
    assert "not a verified answer" in draft.text

    refused = assemble_answer(trace, outcomes, assembly_policy())
    assert refused.status == "refused"
    assert refused.reason_code == "unverified_claims_present"


def test_nothing_supportable_refuses() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], (), verify_policy()),
        verify_claim(trace.claims[1], (), verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))
    assert answer.status == "refused"
    assert answer.reason_code == "no_supported_claims"


def test_outcome_correspondence_guards() -> None:
    trace = two_claim_trace()
    good = verify_claim(trace.claims[0], trace.evidence, verify_policy())
    other = verify_claim(trace.claims[1], trace.evidence, verify_policy())

    with pytest.raises(AssemblyError, match="without outcomes"):
        assemble_answer(trace, (good,), assembly_policy())
    stranger = VerificationOutcome(claim_id="syn-claim-9", conclusion="unverified",
                                   code="unsupported_operation", message="synthetic",
                                   policy_id="synthetic-verify", policy_version="v1")
    with pytest.raises(AssemblyError, match="unknown claims"):
        assemble_answer(trace, (good, stranger), assembly_policy())
    with pytest.raises(AssemblyError, match="duplicate"):
        assemble_answer(trace, (good, good), assembly_policy())
    mixed = VerificationOutcome(claim_id="syn-claim-2", conclusion="supported",
                                code="evaluated", message="synthetic",
                                policy_id="other-policy", policy_version="v1",
                                evidence_ids=("syn-ev-2",))
    with pytest.raises(AssemblyError, match="one verification policy"):
        assemble_answer(trace, (good, mixed), assembly_policy())
    with pytest.raises(AssemblyError, match="non-empty tuple"):
        assemble_answer(trace, (), assembly_policy())


def test_foreign_evidence_and_calculation_rejected() -> None:
    trace = two_claim_trace()
    foreign = VerificationOutcome(
        claim_id="syn-claim-1", conclusion="insufficient_evidence",
        code="missing_evidence", message="synthetic",
        policy_id="synthetic-verify", policy_version="v1",
        evidence_ids=("syn-ev-outside",))
    with pytest.raises(AssemblyError, match="outside the trace"):
        assemble_answer(trace, (foreign, verify_claim(trace.claims[1], trace.evidence, verify_policy())),
                        assembly_policy())

    ghost_calc = VerificationOutcome(
        claim_id="syn-claim-1", conclusion="supported", code="evaluated",
        message="synthetic", policy_id="synthetic-verify", policy_version="v1",
        evidence_ids=("syn-ev-1",), calculation_id="syn-calc-ghost")
    with pytest.raises(AssemblyError, match="unknown calculation"):
        assemble_answer(trace, (ghost_calc, verify_claim(trace.claims[1], trace.evidence, verify_policy())),
                        assembly_policy())


def test_matching_calculation_flows_through() -> None:
    calc = CalculationRef("syn-calc-1", 1, FRAMEWORK, RULESET_ID)
    trace = two_claim_trace(calculation=calc)
    outcomes = (
        VerificationOutcome(claim_id="syn-claim-1", conclusion="supported", code="evaluated",
                            message="synthetic", policy_id="synthetic-verify",
                            policy_version="v1", evidence_ids=("syn-ev-1",),
                            calculation_id="syn-calc-1"),
        verify_claim(trace.claims[1], trace.evidence, verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy())
    assert answer.status == "verified"
    assert answer.calculation_id == "syn-calc-1"


def test_reason_codes_and_ids_preserved() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace_evidence()[:1], verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))
    assert [(c.claim_id, c.conclusion, c.code) for c in answer.claims] == [
        ("syn-claim-1", "supported", "evaluated"),
        ("syn-claim-2", "insufficient_evidence", "missing_evidence"),
    ]


def test_abstention_determinism_and_immutability() -> None:
    trace = two_claim_trace(legal_basis=refused_basis(), status="refused")
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace.evidence, verify_policy()),
    )
    before_trace, before_outcomes = trace.to_dict(), [o for o in outcomes]
    first = assemble_answer(trace, outcomes, assembly_policy())
    second = assemble_answer(trace, outcomes, assembly_policy())

    assert first == second
    assert first.text == "No applicable legal rule matches the request. No answer can be given."
    assert trace.to_dict() == before_trace and list(outcomes) == before_outcomes


def test_serialization_round_trip() -> None:
    trace = two_claim_trace()
    outcomes = (
        verify_claim(trace.claims[0], trace.evidence, verify_policy()),
        verify_claim(trace.claims[1], trace_evidence()[:1], verify_policy()),
    )
    answer = assemble_answer(trace, outcomes, assembly_policy(allow_partial=True))
    restored = AnswerResult.from_dict(json.loads(json.dumps(answer.to_dict())))
    assert restored == answer

    with pytest.raises(AssemblyError):
        AnswerResult.from_dict({"schema_version": 999})
    with pytest.raises(AssemblyError):
        AnswerResult.from_dict(dict(answer.to_dict(), claims=[]))
