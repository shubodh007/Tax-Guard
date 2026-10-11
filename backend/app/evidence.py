"""Versioned evidence-trace envelope. Standard library only.

A small auditable record linking a future answer to its legal basis
(established by the applicability resolver), supporting evidence
references, per-claim verification states, and an optional deterministic
calculation reference. Frozen dataclasses with constructor validation;
no FastAPI, database, network, LLM, clock, or hidden-state dependency.
See ``docs/evidence-trace-contract.md`` for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.applicability import Refused, Resolved
from backend.app.calculation import CALCULATION_SCHEMA_VERSION, CalculationResult

__all__ = [
    "TRACE_SCHEMA_VERSION",
    "CalculationRef",
    "ClaimRef",
    "EvidenceRef",
    "EvidenceTrace",
    "LegalBasis",
    "TraceError",
]

# Version of this envelope schema. Readers accept only this version and
# reject anything else explicitly; a future version must define its own
# migration rather than being silently accepted.
TRACE_SCHEMA_VERSION = 1

TRACE_STATUSES = ("draft", "verified", "partial", "refused", "verification_failed")
CLAIM_STATUSES = (
    "unverified",
    "supported",
    "contradicted",
    "insufficient_evidence",
    "verification_failed",
)
EVIDENCE_TRUST = ("unverified", "verified", "synthetic")


class TraceError(ValueError):
    """Malformed trace or violated envelope invariant. Distinct from a valid
    trace describing an unsuccessful or incomplete process."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TraceError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_id_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise TraceError(f"{name} must be a tuple of non-empty strings")
    return values


@dataclass(frozen=True)
class LegalBasis:
    """Resolved legal basis, or an explicit refusal marker. Build only via
    from_resolved / from_refusal so a refusal can never be encoded as a
    successful resolution. A reference here is not proof the source is correct."""

    resolved: bool
    framework: str
    target_ruleset_id: str
    rule_id: str
    rule_version: str
    period: str
    records_id: str
    records_version: str
    provenance: str
    refusal_code: str = ""
    refusal_message: str = ""

    @classmethod
    def from_resolved(cls, resolution: Resolved) -> LegalBasis:
        if not isinstance(resolution, Resolved):
            raise TraceError("expected Resolved")
        return cls(
            resolved=True,
            framework=resolution.framework,
            target_ruleset_id=resolution.target_ruleset_id,
            rule_id=resolution.rule_id,
            rule_version=resolution.rule_version,
            period=resolution.period,
            records_id=resolution.records_id,
            records_version=resolution.records_version,
            provenance=resolution.provenance,
        )

    @classmethod
    def from_refusal(cls, refusal: Refused) -> LegalBasis:
        if not isinstance(refusal, Refused):
            raise TraceError("expected Refused")
        return cls(
            resolved=False,
            framework="",
            target_ruleset_id="",
            rule_id="",
            rule_version="",
            period=refusal.period,
            records_id=refusal.records_id,
            records_version="",
            provenance="",
            refusal_code=refusal.code,
            refusal_message=refusal.message,
        )

    def __post_init__(self) -> None:
        if self.resolved:
            for name in (
                "framework",
                "target_ruleset_id",
                "rule_id",
                "rule_version",
                "period",
                "records_id",
                "records_version",
                "provenance",
            ):
                _require_label(name, getattr(self, name))
            if self.refusal_code or self.refusal_message:
                raise TraceError("a resolved basis must not carry refusal fields")
        else:
            _require_label("refusal_code", self.refusal_code)
            _require_label("refusal_message", self.refusal_message)
            for name in (
                "framework",
                "target_ruleset_id",
                "rule_id",
                "rule_version",
                "records_version",
                "provenance",
            ):
                if getattr(self, name):
                    raise TraceError(f"a refused basis must not carry {name}")
            _require_label("period", self.period)
            _require_label("records_id", self.records_id)


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to supporting evidence. Identity fields required; all
    descriptive metadata optional. The `trust` label is informational and
    never confers legal authority."""

    evidence_id: str
    document_id: str = ""
    document_version: str = ""
    section: str = ""
    passage_id: str = ""
    uri: str = ""
    effective: str = ""
    digest: str = ""
    trust: str = "unverified"

    def __post_init__(self) -> None:
        _require_label("evidence_id", self.evidence_id)
        if self.trust not in EVIDENCE_TRUST:
            raise TraceError(f"unknown trust status {self.trust!r}")


@dataclass(frozen=True)
class ClaimRef:
    """One answer claim and its verification state. `status` reflects a
    verifier judgment, never the mere presence of evidence identifiers."""

    claim_id: str
    role: str
    supporting_ids: tuple[str, ...] = ()
    contradicting_ids: tuple[str, ...] = ()
    status: str = "unverified"
    reason: str = "not-yet-verified"

    def __post_init__(self) -> None:
        _require_label("claim_id", self.claim_id)
        _require_label("role", self.role)
        _require_label("reason", self.reason)
        _require_id_tuple("supporting_ids", self.supporting_ids)
        _require_id_tuple("contradicting_ids", self.contradicting_ids)
        if self.status not in CLAIM_STATUSES:
            raise TraceError(f"unknown claim status {self.status!r}")
        if self.status == "supported" and not self.supporting_ids:
            raise TraceError("a supported claim must cite at least one supporting evidence id")
        if self.status == "contradicted" and not self.contradicting_ids:
            raise TraceError("a contradicted claim must cite at least one contradicting evidence id")


@dataclass(frozen=True)
class CalculationRef:
    """Pointer to a deterministic calculation preserved elsewhere. Stores
    identities and a reproducibility reference, not duplicated amounts."""

    calculation_id: str
    contract_version: int
    framework: str
    ruleset_id: str
    result_digest: str = ""

    @classmethod
    def from_result(cls, result: CalculationResult, calculation_id: str) -> CalculationRef:
        if not isinstance(result, CalculationResult):
            raise TraceError("expected CalculationResult")
        return cls(
            calculation_id=_require_label("calculation_id", calculation_id),
            contract_version=CALCULATION_SCHEMA_VERSION,
            framework=result.framework,
            ruleset_id=result.ruleset_id,
        )

    def __post_init__(self) -> None:
        _require_label("calculation_id", self.calculation_id)
        if self.contract_version != CALCULATION_SCHEMA_VERSION:
            raise TraceError(f"unsupported calculation contract version {self.contract_version!r}")
        _require_label("framework", self.framework)
        _require_label("ruleset_id", self.ruleset_id)


@dataclass(frozen=True)
class EvidenceTrace:
    """The envelope. Constructor enforces every cross-section invariant;
    a successfully built trace is either valid or validly unsuccessful."""

    trace_id: str
    schema_version: int
    status: str
    created_at: str  # caller-supplied timestamp label; informational only,
    # never an input to validation or verification logic
    legal_basis: LegalBasis
    evidence: tuple[EvidenceRef, ...] = ()
    claims: tuple[ClaimRef, ...] = ()
    calculation: CalculationRef | None = None

    def __post_init__(self) -> None:
        _require_label("trace_id", self.trace_id)
        if self.schema_version != TRACE_SCHEMA_VERSION:
            raise TraceError(f"unsupported trace schema version {self.schema_version!r}")
        if self.status not in TRACE_STATUSES:
            raise TraceError(f"unknown trace status {self.status!r}")
        _require_label("created_at", self.created_at)
        if not isinstance(self.legal_basis, LegalBasis):
            raise TraceError("legal_basis must be a LegalBasis")
        if any(not isinstance(e, EvidenceRef) for e in self.evidence):
            raise TraceError("evidence must be a tuple of EvidenceRef")
        if any(not isinstance(c, ClaimRef) for c in self.claims):
            raise TraceError("claims must be a tuple of ClaimRef")
        if self.calculation is not None and not isinstance(self.calculation, CalculationRef):
            raise TraceError("calculation must be a CalculationRef or None")

        evidence_ids = [e.evidence_id for e in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise TraceError("duplicate evidence identifiers")
        claim_ids = [c.claim_id for c in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise TraceError("duplicate claim identifiers")
        known = set(evidence_ids)
        for claim in self.claims:
            unknown = (set(claim.supporting_ids) | set(claim.contradicting_ids)) - known
            if unknown:
                raise TraceError(
                    f"claim {claim.claim_id!r} references unknown evidence: {sorted(unknown)}"
                )

        if self.calculation is not None:
            if not self.legal_basis.resolved:
                raise TraceError("a calculation reference requires a resolved legal basis")
            if self.calculation.framework != self.legal_basis.framework:
                raise TraceError("calculation framework conflicts with the resolved legal basis")
            if self.calculation.ruleset_id != self.legal_basis.target_ruleset_id:
                raise TraceError("calculation ruleset conflicts with the resolved legal basis")

        resolved = self.legal_basis.resolved
        if self.status == "refused":
            if resolved:
                raise TraceError("a refused trace must carry an unresolved legal basis")
            return
        if not resolved:
            raise TraceError("an unresolved legal basis requires refused status")
        if self.status == "verified":
            if not self.claims or any(c.status != "supported" for c in self.claims):
                raise TraceError("verified status requires at least one claim and all claims supported")
        elif self.status == "partial":
            if not self.claims or all(c.status == "supported" for c in self.claims):
                raise TraceError("partial status requires claims with at least one not supported")
        # "draft" and "verification_failed" carry no claim constraints: a draft
        # is simply not yet verified, and a failed verification process is
        # distinct from missing evidence (per-claim "insufficient_evidence").

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping. Tuples become lists; None stays None."""
        return {
            "trace_id": self.trace_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "created_at": self.created_at,
            "legal_basis": {
                "resolved": self.legal_basis.resolved,
                "framework": self.legal_basis.framework,
                "target_ruleset_id": self.legal_basis.target_ruleset_id,
                "rule_id": self.legal_basis.rule_id,
                "rule_version": self.legal_basis.rule_version,
                "period": self.legal_basis.period,
                "records_id": self.legal_basis.records_id,
                "records_version": self.legal_basis.records_version,
                "provenance": self.legal_basis.provenance,
                "refusal_code": self.legal_basis.refusal_code,
                "refusal_message": self.legal_basis.refusal_message,
            },
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "document_id": e.document_id,
                    "document_version": e.document_version,
                    "section": e.section,
                    "passage_id": e.passage_id,
                    "uri": e.uri,
                    "effective": e.effective,
                    "digest": e.digest,
                    "trust": e.trust,
                }
                for e in self.evidence
            ],
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "role": c.role,
                    "supporting_ids": list(c.supporting_ids),
                    "contradicting_ids": list(c.contradicting_ids),
                    "status": c.status,
                    "reason": c.reason,
                }
                for c in self.claims
            ],
            "calculation": (
                None
                if self.calculation is None
                else {
                    "calculation_id": self.calculation.calculation_id,
                    "contract_version": self.calculation.contract_version,
                    "framework": self.calculation.framework,
                    "ruleset_id": self.calculation.ruleset_id,
                    "result_digest": self.calculation.result_digest,
                }
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceTrace:
        """Rebuild a trace from a mapping, revalidating every invariant.
        Malformed input raises TraceError; it is never coerced."""

        def req_str(section: str, mapping: dict[str, Any], key: str) -> str:
            value = mapping.get(key)
            if not isinstance(value, str):
                raise TraceError(f"{section}.{key} must be a string")
            return value

        def req_tuple(section: str, mapping: dict[str, Any], key: str) -> tuple[str, ...]:
            values = mapping.get(key, ())
            if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values):
                raise TraceError(f"{section}.{key} must be a list of strings")
            return tuple(values)

        def opt_str(section: str, mapping: dict[str, Any], key: str) -> str:
            value = mapping.get(key, "")
            if not isinstance(value, str):
                raise TraceError(f"{section}.{key} must be a string")
            return value

        if not isinstance(data, dict):
            raise TraceError("trace must be a mapping")
        version = data.get("schema_version")
        if version != TRACE_SCHEMA_VERSION:
            raise TraceError(f"unsupported trace schema version {version!r}")

        basis_data = data.get("legal_basis")
        if not isinstance(basis_data, dict):
            raise TraceError("legal_basis must be a mapping")
        basis = LegalBasis(
            resolved=basis_data.get("resolved") is True,
            framework=req_str("legal_basis", basis_data, "framework"),
            target_ruleset_id=req_str("legal_basis", basis_data, "target_ruleset_id"),
            rule_id=req_str("legal_basis", basis_data, "rule_id"),
            rule_version=req_str("legal_basis", basis_data, "rule_version"),
            period=req_str("legal_basis", basis_data, "period"),
            records_id=req_str("legal_basis", basis_data, "records_id"),
            records_version=req_str("legal_basis", basis_data, "records_version"),
            provenance=req_str("legal_basis", basis_data, "provenance"),
            refusal_code=req_str("legal_basis", basis_data, "refusal_code"),
            refusal_message=req_str("legal_basis", basis_data, "refusal_message"),
        )

        evidence_data = data.get("evidence", ())
        if not isinstance(evidence_data, (list, tuple)):
            raise TraceError("evidence must be a list")
        evidence_items: list[EvidenceRef] = []
        for i, e in enumerate(evidence_data):
            if not isinstance(e, dict):
                raise TraceError(f"evidence[{i}] must be a mapping")
            evidence_items.append(
                EvidenceRef(
                    evidence_id=req_str(f"evidence[{i}]", e, "evidence_id"),
                    document_id=opt_str(f"evidence[{i}]", e, "document_id"),
                    document_version=opt_str(f"evidence[{i}]", e, "document_version"),
                    section=opt_str(f"evidence[{i}]", e, "section"),
                    passage_id=opt_str(f"evidence[{i}]", e, "passage_id"),
                    uri=opt_str(f"evidence[{i}]", e, "uri"),
                    effective=opt_str(f"evidence[{i}]", e, "effective"),
                    digest=opt_str(f"evidence[{i}]", e, "digest"),
                    trust=e.get("trust", "unverified"),
                )
            )
        evidence = tuple(evidence_items)

        claims_data = data.get("claims", ())
        if not isinstance(claims_data, (list, tuple)):
            raise TraceError("claims must be a list")
        claim_items: list[ClaimRef] = []
        for i, c in enumerate(claims_data):
            if not isinstance(c, dict):
                raise TraceError(f"claims[{i}] must be a mapping")
            claim_items.append(
                ClaimRef(
                    claim_id=req_str(f"claims[{i}]", c, "claim_id"),
                    role=req_str(f"claims[{i}]", c, "role"),
                    supporting_ids=req_tuple(f"claims[{i}]", c, "supporting_ids"),
                    contradicting_ids=req_tuple(f"claims[{i}]", c, "contradicting_ids"),
                    status=c.get("status", "unverified"),
                    reason=c.get("reason", "not-yet-verified"),
                )
            )
        claims = tuple(claim_items)

        calc_data = data.get("calculation")
        calculation = None
        if calc_data is not None:
            if not isinstance(calc_data, dict):
                raise TraceError("calculation must be a mapping or null")
            contract_version = calc_data.get("contract_version")
            if not isinstance(contract_version, int):
                raise TraceError("calculation.contract_version must be an integer")
            calculation = CalculationRef(
                calculation_id=req_str("calculation", calc_data, "calculation_id"),
                contract_version=contract_version,
                framework=req_str("calculation", calc_data, "framework"),
                ruleset_id=req_str("calculation", calc_data, "ruleset_id"),
                result_digest=opt_str("calculation", calc_data, "result_digest"),
            )

        return cls(
            trace_id=req_str("trace", data, "trace_id"),
            schema_version=version,
            status=req_str("trace", data, "status"),
            created_at=req_str("trace", data, "created_at"),
            legal_basis=basis,
            evidence=evidence,
            claims=claims,
            calculation=calculation,
        )
