"""Shared SYNTHETIC fixtures for registry and API tests.

Nothing here states tax law. Objects are built from the real
constructors in `backend/app/evidence.py` and `backend/app/assembly.py`.
"""

from backend.app.assembly import AnswerResult, ClaimAnswer
from backend.app.evidence import (
    TRACE_SCHEMA_VERSION,
    ClaimRef,
    EvidenceRef,
    EvidenceTrace,
    LegalBasis,
)

FRAMEWORK = "SYNTHETIC TEST FIXTURE — not Indian tax law"


def synthetic_trace(trace_id: str = "syn-trace-1") -> EvidenceTrace:
    return EvidenceTrace(
        trace_id=trace_id,
        schema_version=TRACE_SCHEMA_VERSION,
        status="draft",
        created_at="2026-01-01T00:00:00Z",
        legal_basis=LegalBasis(
            resolved=True, framework=FRAMEWORK, target_ruleset_id="synthetic-mini-v0",
            rule_id="synthetic-rule-A", rule_version="v1", period="2024-25",
            records_id="synthetic-applicability-v0", records_version="v0",
            provenance="SYNTHETIC TEST FIXTURE — not a legal source",
        ),
        evidence=(EvidenceRef("syn-ev-1", trust="synthetic"),),
        claims=(ClaimRef("syn-claim-1", role="conclusion", supporting_ids=("syn-ev-1",)),),
    )


def synthetic_answer(trace_id: str = "syn-trace-1") -> AnswerResult:
    return AnswerResult(
        schema_version=1,
        status="draft",
        text="Verification is not yet complete. This draft is not a verified answer.",
        trace_id=trace_id,
        claims=(ClaimAnswer("syn-claim-1", "unverified", "unsupported_operation",
                            ("syn-ev-1",), False),),
        evidence_ids=(),
        legal_resolved=True,
        framework=FRAMEWORK,
        ruleset_id="synthetic-mini-v0",
        calculation_id="",
        reason_code="verification_incomplete",
    )
