"""Claim-verification interface contract. Standard library only.

Defines what verification means, which outcomes are possible, and how
operational failures are represented — before any real LLM or retrieval
provider exists. Pure functions over frozen dataclasses; no FastAPI,
database, network, model SDK, clock, or hidden-state dependency.

The deterministic core (`verify_claim`) evaluates a claim against an
explicitly supplied evidence subset under an explicit policy. It performs
no semantic verification: conclusions come only from configured policy
sets. A `fault` simulation switch lets tests exercise failure paths
honestly; it defaults to off. See
``docs/claim-verification-contract.md`` for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from backend.app.evidence import (
    CLAIM_STATUSES,
    CalculationRef,
    ClaimRef,
    EvidenceRef,
    EvidenceTrace,
    LegalBasis,
    TraceError,
)

__all__ = [
    "EVALUATED",
    "INVALID_INPUT",
    "MISSING_EVIDENCE",
    "PROVIDER_FAILURE",
    "QUOTA_EXHAUSTED",
    "TIMEOUT",
    "UNSUPPORTED_OPERATION",
    "VerificationError",
    "VerificationOutcome",
    "VerificationPolicy",
    "apply_outcome",
    "verify_claim",
]


class VerificationError(ValueError):
    """Malformed verification input or configuration. Distinct from a valid
    evaluation that concludes unsuccessfully (reported via outcome)."""


# Stable operational outcome codes.
EVALUATED = "evaluated"
MISSING_EVIDENCE = "missing_evidence"
INVALID_INPUT = "invalid_input"
UNSUPPORTED_OPERATION = "unsupported_operation"
PROVIDER_FAILURE = "provider_failure"
QUOTA_EXHAUSTED = "quota_exhausted"
TIMEOUT = "timeout"

_OUTCOME_CODES = (
    EVALUATED,
    MISSING_EVIDENCE,
    INVALID_INPUT,
    UNSUPPORTED_OPERATION,
    PROVIDER_FAILURE,
    QUOTA_EXHAUSTED,
    TIMEOUT,
)

_FAILURE_CODES = (PROVIDER_FAILURE, QUOTA_EXHAUSTED, TIMEOUT)
_NO_ATTEMPT_CODES = (INVALID_INPUT, UNSUPPORTED_OPERATION)
_FAULTS = ("none", PROVIDER_FAILURE, QUOTA_EXHAUSTED, TIMEOUT)


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_id_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise VerificationError(f"{name} must be a tuple of non-empty strings")
    return tuple(values)


@dataclass(frozen=True)
class VerificationPolicy:
    """Explicit deterministic policy. Conclusions come only from the
    configured claim sets — never from semantic judgment. `fault`
    simulates an operational failure for tests; default "none" means
    evaluate normally."""

    policy_id: str
    version: str
    supported_claim_ids: tuple[str, ...] = ()
    contradicted_claim_ids: tuple[str, ...] = ()
    require_calculation_for: tuple[str, ...] = ()
    fault: str = "none"

    def __post_init__(self) -> None:
        _require_label("policy_id", self.policy_id)
        _require_label("version", self.version)
        _require_id_tuple("supported_claim_ids", self.supported_claim_ids)
        _require_id_tuple("contradicted_claim_ids", self.contradicted_claim_ids)
        _require_id_tuple("require_calculation_for", self.require_calculation_for)
        overlap = set(self.supported_claim_ids) & set(self.contradicted_claim_ids)
        if overlap:
            raise VerificationError(f"claims cannot be both supported and contradicted: {sorted(overlap)}")
        if self.fault not in _FAULTS:
            raise VerificationError(f"unknown fault {self.fault!r}")


@dataclass(frozen=True)
class VerificationOutcome:
    """One verification conclusion plus the operational code that produced
    it. Constructor enforces the valid conclusion/code combinations, so a
    failure can never be constructed as a success."""

    claim_id: str
    conclusion: str  # one of evidence.CLAIM_STATUSES
    code: str  # one of the module-level operational codes
    message: str
    policy_id: str
    policy_version: str
    evidence_ids: tuple[str, ...] = ()
    calculation_id: str = ""

    def __post_init__(self) -> None:
        _require_label("claim_id", self.claim_id)
        _require_label("message", self.message)
        _require_label("policy_id", self.policy_id)
        _require_label("policy_version", self.policy_version)
        _require_id_tuple("evidence_ids", self.evidence_ids)
        if self.conclusion not in CLAIM_STATUSES:
            raise VerificationError(f"unknown conclusion {self.conclusion!r}")
        if self.code not in _OUTCOME_CODES:
            raise VerificationError(f"unknown outcome code {self.code!r}")
        if self.code == EVALUATED:
            if self.conclusion not in ("supported", "contradicted", "insufficient_evidence"):
                raise VerificationError("a successful evaluation cannot conclude " + self.conclusion)
        elif self.code == MISSING_EVIDENCE:
            if self.conclusion != "insufficient_evidence":
                raise VerificationError("missing evidence must conclude insufficient_evidence")
        elif self.code in _FAILURE_CODES:
            if self.conclusion != "verification_failed":
                raise VerificationError(f"{self.code} must conclude verification_failed")
        else:  # INVALID_INPUT, UNSUPPORTED_OPERATION: no attempt was possible
            if self.conclusion != "unverified":
                raise VerificationError(f"{self.code} must conclude unverified")


def verify_claim(
    claim: ClaimRef,
    evidence: tuple[EvidenceRef, ...],
    policy: VerificationPolicy,
    calculation: CalculationRef | None = None,
    legal_basis: LegalBasis | None = None,
) -> VerificationOutcome:
    """Deterministically evaluate `claim` against `evidence` under `policy`.

    Same inputs always yield the same outcome. Only the supplied subset is
    considered; cited identifiers absent from it are missing evidence, never
    synthesized. Trust labels pass through untouched.
    """
    if not isinstance(claim, ClaimRef):
        raise VerificationError("claim must be a ClaimRef")
    if any(not isinstance(e, EvidenceRef) for e in evidence):
        raise VerificationError("evidence must be a tuple of EvidenceRef")
    if not isinstance(policy, VerificationPolicy):
        raise VerificationError("policy must be a VerificationPolicy")
    if calculation is not None and not isinstance(calculation, CalculationRef):
        raise VerificationError("calculation must be a CalculationRef or None")
    if legal_basis is not None and not isinstance(legal_basis, LegalBasis):
        raise VerificationError("legal_basis must be a LegalBasis or None")
    subset_ids = [e.evidence_id for e in evidence]
    if len(set(subset_ids)) != len(subset_ids):
        raise VerificationError("duplicate evidence identifiers in subset")

    base = {
        "claim_id": claim.claim_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "evidence_ids": tuple(subset_ids),
    }

    if policy.fault != "none":
        return VerificationOutcome(
            conclusion="verification_failed",
            code=policy.fault,
            message=f"{policy.fault} (simulated fault {policy.fault!r})",
            **base,  # type: ignore[arg-type]
        )

    if calculation is not None:
        if legal_basis is None or not legal_basis.resolved:
            return VerificationOutcome(
                conclusion="unverified",
                code=INVALID_INPUT,
                message="calculation reference requires a resolved legal basis",
                **base,  # type: ignore[arg-type]
            )
        if (
            calculation.framework != legal_basis.framework
            or calculation.ruleset_id != legal_basis.target_ruleset_id
        ):
            return VerificationOutcome(
                conclusion="unverified",
                code=INVALID_INPUT,
                message="calculation identity conflicts with the legal basis",
                **base,  # type: ignore[arg-type]
            )
        calc_id = calculation.calculation_id
    else:
        calc_id = ""

    if claim.claim_id in policy.require_calculation_for and calculation is None:
        return VerificationOutcome(
            conclusion="unverified",
            code=UNSUPPORTED_OPERATION,
            message="claim requires a calculation reference",
            **base,  # type: ignore[arg-type]
        )

    if claim.claim_id in policy.supported_claim_ids:
        missing = [i for i in claim.supporting_ids if i not in subset_ids]
        if claim.supporting_ids and not missing:
            return VerificationOutcome(
                conclusion="supported",
                code=EVALUATED,
                message=(
                    f"supported by {len(claim.supporting_ids)} cited references "
                    f"under {policy.policy_id} {policy.version}"
                ),
                calculation_id=calc_id,
                **base,  # type: ignore[arg-type]
            )
        return VerificationOutcome(
            conclusion="insufficient_evidence",
            code=MISSING_EVIDENCE,
            message=f"missing evidence: {missing or list(claim.supporting_ids)}",
            calculation_id=calc_id,
            **base,  # type: ignore[arg-type]
        )

    if claim.claim_id in policy.contradicted_claim_ids:
        missing = [i for i in claim.contradicting_ids if i not in subset_ids]
        if claim.contradicting_ids and not missing:
            return VerificationOutcome(
                conclusion="contradicted",
                code=EVALUATED,
                message=(
                    f"contradicted by {len(claim.contradicting_ids)} cited references "
                    f"under {policy.policy_id} {policy.version}"
                ),
                calculation_id=calc_id,
                **base,  # type: ignore[arg-type]
            )
        return VerificationOutcome(
            conclusion="insufficient_evidence",
            code=MISSING_EVIDENCE,
            message=f"missing evidence: {missing or list(claim.contradicting_ids)}",
            calculation_id=calc_id,
            **base,  # type: ignore[arg-type]
        )

    return VerificationOutcome(
        conclusion="unverified",
        code=UNSUPPORTED_OPERATION,
        message=f"claim not covered by policy {policy.policy_id} {policy.version}",
        calculation_id=calc_id,
        **base,  # type: ignore[arg-type]
    )


def apply_outcome(trace: EvidenceTrace, outcome: VerificationOutcome) -> EvidenceTrace:
    """Apply one outcome to its claim, returning a new trace.

    The claim keeps its identifiers and role; only status/reason change.
    Evidence (including trust labels) and the legal basis are never
    modified, so refusal can never become resolution here. Overall status
    is recomputed from claim states: all supported → verified, else
    partial — a failure therefore can never promote a trace to verified.
    """
    if not isinstance(trace, EvidenceTrace):
        raise VerificationError("trace must be an EvidenceTrace")
    if not isinstance(outcome, VerificationOutcome):
        raise VerificationError("outcome must be a VerificationOutcome")
    if not trace.legal_basis.resolved:
        raise VerificationError("cannot apply verification to an unresolved legal basis")
    current = next((c for c in trace.claims if c.claim_id == outcome.claim_id), None)
    if current is None:
        raise VerificationError(f"unknown claim {outcome.claim_id!r} for this trace")

    claims = tuple(
        replace(c, status=outcome.conclusion, reason=outcome.message)
        if c.claim_id == outcome.claim_id
        else c
        for c in trace.claims
    )
    try:
        return EvidenceTrace(
            trace_id=trace.trace_id,
            schema_version=trace.schema_version,
            status="verified" if all(c.status == "supported" for c in claims) else "partial",
            created_at=trace.created_at,
            legal_basis=trace.legal_basis,
            evidence=trace.evidence,
            claims=claims,
            calculation=trace.calculation,
        )
    except TraceError as exc:
        raise VerificationError(str(exc)) from exc


def outcome_to_dict(outcome: VerificationOutcome) -> dict[str, Any]:
    """JSON-compatible mapping for an outcome."""
    return {
        "claim_id": outcome.claim_id,
        "conclusion": outcome.conclusion,
        "code": outcome.code,
        "message": outcome.message,
        "policy_id": outcome.policy_id,
        "policy_version": outcome.policy_version,
        "evidence_ids": list(outcome.evidence_ids),
        "calculation_id": outcome.calculation_id,
    }
