"""Deterministic answer-assembly contract. Standard library only.

Composes a validated evidence trace plus one verification outcome per
claim into a final, auditable answer. Pure functions over frozen
dataclasses; no FastAPI, database, network, LLM, clock, or hidden-state
dependency. Assembly composes supplied information only: it never
resolves applicability, verifies claims, performs arithmetic, or invents
facts. Answer text states contract-level metadata (identifiers, statuses,
counts), never new factual conclusions. See
``docs/answer-assembly-contract.md`` for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.applicability import (
    CONFLICTING_RULES,
    MISSING_FACT,
    NO_MATCHING_RULE,
    UNSUPPORTED_PERIOD,
    UNUSABLE_RULE,
)
from backend.app.evidence import EvidenceTrace, LegalBasis
from backend.app.verification import (
    PROVIDER_FAILURE,
    QUOTA_EXHAUSTED,
    TIMEOUT,
    VerificationOutcome,
)

__all__ = [
    "ANSWER_SCHEMA_VERSION",
    "ANSWER_STATUSES",
    "AnswerResult",
    "AssemblyError",
    "AssemblyPolicy",
    "ClaimAnswer",
    "assemble_answer",
]

# Version of this answer schema. Readers accept only this version.
ANSWER_SCHEMA_VERSION = 1

ANSWER_STATUSES = ("verified", "partial", "refused", "verification_failed", "draft")

# Assembly-level reason codes (refusal/failure codes from earlier phases
# pass through unchanged where noted).
ALL_SUPPORTED = "all_supported"
PARTIAL_COVERAGE = "partial_coverage"
PARTIAL_NOT_PERMITTED = "partial_not_permitted"
UNVERIFIED_CLAIMS_PRESENT = "unverified_claims_present"
UNRESOLVED_CONTRADICTION = "unresolved_contradiction"
NO_SUPPORTED_CLAIMS = "no_supported_claims"
VERIFICATION_INCOMPLETE = "verification_incomplete"

_FAILURE_CODES = (PROVIDER_FAILURE, QUOTA_EXHAUSTED, TIMEOUT)

_ABSTENTION_TEXT = {
    MISSING_FACT: "Required taxpayer facts are missing. No answer can be given.",
    UNSUPPORTED_PERIOD: "The requested period is not covered by any applicability record. No answer can be given.",
    NO_MATCHING_RULE: "No applicable legal rule matches the request. No answer can be given.",
    CONFLICTING_RULES: "Conflicting legal-rule matches require resolution. No answer can be given.",
    UNUSABLE_RULE: "The applicable legal records are not verified for use. No answer can be given.",
    PROVIDER_FAILURE: "Verification could not be completed because of a provider failure. No answer can be given.",
    QUOTA_EXHAUSTED: "Verification could not be completed because of quota exhaustion. No answer can be given.",
    TIMEOUT: "Verification could not be completed because of a timeout. No answer can be given.",
    UNRESOLVED_CONTRADICTION: "The sources contradict each other on a material claim. No safe answer can be given.",
    PARTIAL_NOT_PERMITTED: "Some claims remain unverified and partial answers are not permitted. No answer can be given.",
    UNVERIFIED_CLAIMS_PRESENT: "Some claims could not be verified. No answer can be given.",
    NO_SUPPORTED_CLAIMS: "None of the claims could be supported with the available evidence. No answer can be given.",
    VERIFICATION_INCOMPLETE: "Verification is not yet complete. This draft is not a verified answer.",
}


class AssemblyError(ValueError):
    """Malformed assembly input (unknown/duplicate claims, inconsistent
    policies, evidence or calculation references the trace cannot honor).
    Distinct from a valid assembly that concludes with refusal or failure."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssemblyError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class AssemblyPolicy:
    """Explicit assembly policy. Both switches default fail-closed: partial
    answers and tolerance of unevaluated claims must be opted into."""

    policy_id: str
    version: str
    allow_partial: bool = False
    tolerate_unverified: bool = False

    def __post_init__(self) -> None:
        _require_label("policy_id", self.policy_id)
        _require_label("version", self.version)
        if not isinstance(self.allow_partial, bool) or not isinstance(self.tolerate_unverified, bool):
            raise AssemblyError("policy switches must be booleans")


@dataclass(frozen=True)
class ClaimAnswer:
    """One claim's assembled representation. `included` marks whether the
    answer text covers it; excluded claims stay traceable by id and code."""

    claim_id: str
    conclusion: str
    code: str
    evidence_ids: tuple[str, ...]
    included: bool

    def __post_init__(self) -> None:
        _require_label("claim_id", self.claim_id)
        _require_label("conclusion", self.conclusion)
        _require_label("code", self.code)
        if any(not isinstance(v, str) or not v.strip() for v in self.evidence_ids):
            raise AssemblyError("evidence_ids must be a tuple of non-empty strings")
        if not isinstance(self.included, bool):
            raise AssemblyError("included must be a boolean")


@dataclass(frozen=True)
class AnswerResult:
    """Final answer. Absent references stay empty strings — never
    fabricated. Text states identifiers, statuses, and counts only."""

    schema_version: int
    status: str
    text: str
    trace_id: str
    claims: tuple[ClaimAnswer, ...]
    evidence_ids: tuple[str, ...]
    legal_resolved: bool
    framework: str
    ruleset_id: str
    calculation_id: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.schema_version != ANSWER_SCHEMA_VERSION:
            raise AssemblyError(f"unsupported answer schema version {self.schema_version!r}")
        if self.status not in ANSWER_STATUSES:
            raise AssemblyError(f"unknown answer status {self.status!r}")
        _require_label("text", self.text)
        _require_label("trace_id", self.trace_id)
        _require_label("reason_code", self.reason_code)
        if not self.claims:
            raise AssemblyError("an answer must reference at least one claim")
        if any(not isinstance(c, ClaimAnswer) for c in self.claims):
            raise AssemblyError("claims must be a tuple of ClaimAnswer")
        if not isinstance(self.legal_resolved, bool):
            raise AssemblyError("legal_resolved must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping."""
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "text": self.text,
            "trace_id": self.trace_id,
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "conclusion": c.conclusion,
                    "code": c.code,
                    "evidence_ids": list(c.evidence_ids),
                    "included": c.included,
                }
                for c in self.claims
            ],
            "evidence_ids": list(self.evidence_ids),
            "legal_resolved": self.legal_resolved,
            "framework": self.framework,
            "ruleset_id": self.ruleset_id,
            "calculation_id": self.calculation_id,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerResult:
        """Rebuild an answer, revalidating shape. Malformed input raises
        AssemblyError; it is never coerced."""
        if not isinstance(data, dict):
            raise AssemblyError("answer must be a mapping")
        if data.get("schema_version") != ANSWER_SCHEMA_VERSION:
            raise AssemblyError(
                f"unsupported answer schema version {data.get('schema_version')!r}"
            )

        def req_str(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str):
                raise AssemblyError(f"answer.{key} must be a string")
            return value

        claims_data = data.get("claims")
        if not isinstance(claims_data, list) or not claims_data:
            raise AssemblyError("answer.claims must be a non-empty list")
        claims: list[ClaimAnswer] = []
        for i, c in enumerate(claims_data):
            if not isinstance(c, dict):
                raise AssemblyError(f"answer.claims[{i}] must be a mapping")
            claim_id = c.get("claim_id")
            conclusion = c.get("conclusion")
            code = c.get("code")
            ids = c.get("evidence_ids", ())
            included = c.get("included")
            if (
                not isinstance(claim_id, str)
                or not isinstance(conclusion, str)
                or not isinstance(code, str)
                or not isinstance(ids, (list, tuple))
                or not all(isinstance(v, str) for v in ids)
                or not isinstance(included, bool)
            ):
                raise AssemblyError(f"answer.claims[{i}] is malformed")
            claims.append(ClaimAnswer(claim_id, conclusion, code, tuple(ids), included))

        evidence_ids = data.get("evidence_ids", ())
        if not isinstance(evidence_ids, (list, tuple)) or not all(
            isinstance(v, str) for v in evidence_ids
        ):
            raise AssemblyError("answer.evidence_ids must be a list of strings")
        legal_resolved = data.get("legal_resolved")
        if not isinstance(legal_resolved, bool):
            raise AssemblyError("answer.legal_resolved must be a boolean")
        for key in ("framework", "ruleset_id", "calculation_id"):
            if not isinstance(data.get(key), str):
                raise AssemblyError(f"answer.{key} must be a string")

        return cls(
            schema_version=ANSWER_SCHEMA_VERSION,
            status=req_str("status"),
            text=req_str("text"),
            trace_id=req_str("trace_id"),
            claims=tuple(claims),
            evidence_ids=tuple(evidence_ids),
            legal_resolved=legal_resolved,
            framework=data["framework"],
            ruleset_id=data["ruleset_id"],
            calculation_id=data["calculation_id"],
            reason_code=req_str("reason_code"),
        )


def _abstain(
    trace_id: str,
    answers: list[ClaimAnswer],
    basis: LegalBasis,
    calculation_id: str,
    status: str,
    reason_code: str,
) -> AnswerResult:
    text = _ABSTENTION_TEXT.get(
        reason_code,
        f"The request cannot be answered safely ({reason_code}). No answer can be given.",
    )
    return AnswerResult(
        schema_version=ANSWER_SCHEMA_VERSION,
        status=status,
        text=text,
        trace_id=trace_id,
        claims=tuple(answers),
        evidence_ids=(),
        legal_resolved=basis.resolved,
        framework=basis.framework,
        ruleset_id=basis.target_ruleset_id,
        calculation_id=calculation_id,
        reason_code=reason_code,
    )


def assemble_answer(
    trace: EvidenceTrace,
    outcomes: tuple[VerificationOutcome, ...],
    policy: AssemblyPolicy,
) -> AnswerResult:
    """Compose a final answer from a trace plus exactly one outcome per
    claim. Deterministic in trace-claim order; inputs are never mutated.
    Fail-closed precedence: refused basis > verification failure >
    contradiction > unevaluated claims > coverage check.
    """
    if not isinstance(trace, EvidenceTrace):
        raise AssemblyError("trace must be an EvidenceTrace")
    if not isinstance(policy, AssemblyPolicy):
        raise AssemblyError("policy must be an AssemblyPolicy")
    if not outcomes or any(not isinstance(o, VerificationOutcome) for o in outcomes):
        raise AssemblyError("outcomes must be a non-empty tuple of VerificationOutcome")
    outcome_ids = [o.claim_id for o in outcomes]
    if len(set(outcome_ids)) != len(outcome_ids):
        raise AssemblyError("duplicate claim outcomes")
    trace_ids = [c.claim_id for c in trace.claims]
    unknown = set(outcome_ids) - set(trace_ids)
    if unknown:
        raise AssemblyError(f"outcomes for unknown claims: {sorted(unknown)}")
    missing = set(trace_ids) - set(outcome_ids)
    if missing:
        raise AssemblyError(f"claims without outcomes: {sorted(missing)}")
    policies = {(o.policy_id, o.policy_version) for o in outcomes}
    if len(policies) != 1:
        raise AssemblyError("outcomes must share one verification policy")
    trace_evidence = {e.evidence_id for e in trace.evidence}
    for outcome in outcomes:
        foreign = set(outcome.evidence_ids) - trace_evidence
        if foreign:
            raise AssemblyError(
                f"outcome for {outcome.claim_id!r} cites evidence outside the trace: {sorted(foreign)}"
            )
        if outcome.calculation_id:
            if trace.calculation is None or trace.calculation.calculation_id != outcome.calculation_id:
                raise AssemblyError(
                    f"outcome for {outcome.claim_id!r} cites an unknown calculation"
                )

    basis = trace.legal_basis
    by_id = {o.claim_id: o for o in outcomes}
    answers = [
        ClaimAnswer(
            claim_id=c.claim_id,
            conclusion=by_id[c.claim_id].conclusion,
            code=by_id[c.claim_id].code,
            evidence_ids=by_id[c.claim_id].evidence_ids,
            included=False,
        )
        for c in trace.claims
    ]
    calc_id = trace.calculation.calculation_id if trace.calculation is not None else ""

    if not basis.resolved:
        return _abstain(trace.trace_id, answers, basis, calc_id, "refused", basis.refusal_code)

    failures = [a for a in answers if a.conclusion == "verification_failed"]
    if failures:
        return _abstain(trace.trace_id, answers, basis, calc_id, "verification_failed", failures[0].code)

    contradicted = [a for a in answers if a.conclusion == "contradicted"]
    if contradicted:
        return _abstain(
            trace.trace_id, answers, basis, calc_id, "refused", UNRESOLVED_CONTRADICTION
        )

    unevaluated = [a for a in answers if a.conclusion == "unverified"]
    if unevaluated and not policy.tolerate_unverified:
        return _abstain(
            trace.trace_id, answers, basis, calc_id, "refused", UNVERIFIED_CLAIMS_PRESENT
        )
    if unevaluated and all(a.conclusion == "unverified" for a in answers):
        return _abstain(
            trace.trace_id, answers, basis, calc_id, "draft", VERIFICATION_INCOMPLETE
        )

    supported = [a for a in answers if a.conclusion == "supported"]
    if len(supported) == len(answers):
        included = [ClaimAnswer(a.claim_id, a.conclusion, a.code, a.evidence_ids, True) for a in answers]
        shown = ", ".join(f"{c.claim_id}" for c in trace.claims)
        evids = sorted({i for c in trace.claims for i in (*c.supporting_ids, *c.contradicting_ids)})
        text = (
            f"Verified answer covering {len(included)} supported claims: {shown}. "
            f"Evidence: {', '.join(evids) if evids else 'none cited'}. "
            f"Ruleset: {basis.target_ruleset_id}."
        )
        return AnswerResult(
            schema_version=ANSWER_SCHEMA_VERSION,
            status="verified",
            text=text,
            trace_id=trace.trace_id,
            claims=tuple(included),
            evidence_ids=tuple(evids),
            legal_resolved=True,
            framework=basis.framework,
            ruleset_id=basis.target_ruleset_id,
            calculation_id=calc_id,
            reason_code=ALL_SUPPORTED,
        )

    if not supported:
        return _abstain(trace.trace_id, answers, basis, calc_id, "refused", NO_SUPPORTED_CLAIMS)
    if not policy.allow_partial:
        return _abstain(trace.trace_id, answers, basis, calc_id, "refused", PARTIAL_NOT_PERMITTED)

    included_ids = {a.claim_id for a in supported}
    included = [
        ClaimAnswer(a.claim_id, a.conclusion, a.code, a.evidence_ids, a.claim_id in included_ids)
        for a in answers
    ]
    excluded = [f"{a.claim_id}:{a.code}" for a in answers if a.claim_id not in included_ids]
    by_claim = {c.claim_id: c for c in trace.claims}
    evids = sorted(
        {i for a in included if a.included for i in (*by_claim[a.claim_id].supporting_ids, *by_claim[a.claim_id].contradicting_ids)}
    )
    text = (
        f"Partial answer: {len(supported)} of {len(answers)} claims supported "
        f"({', '.join(a.claim_id for a in supported)}); "
        f"{len(excluded)} excluded as unresolved ({', '.join(excluded)}). "
        f"Evidence: {', '.join(evids) if evids else 'none cited'}. "
        f"This answer is incomplete."
    )
    return AnswerResult(
        schema_version=ANSWER_SCHEMA_VERSION,
        status="partial",
        text=text,
        trace_id=trace.trace_id,
        claims=tuple(included),
        evidence_ids=tuple(evids),
        legal_resolved=True,
        framework=basis.framework,
        ruleset_id=basis.target_ruleset_id,
        calculation_id=calc_id,
        reason_code=PARTIAL_COVERAGE,
    )
