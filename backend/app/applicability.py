"""Deterministic legal-applicability resolver. Standard library only.

Pure functions over frozen dataclasses: no FastAPI, database, network,
LLM, clock, environment, or hidden-state dependency. The resolver selects
only from explicitly supplied applicability records and refuses
(fail-closed) whenever they do not establish applicability. See
``docs/applicability-contract.md`` for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.calculation import CalculationError, CalculationInput, Ruleset

__all__ = [
    "CONFLICTING_RULES",
    "MISSING_FACT",
    "NO_MATCHING_RULE",
    "UNSUPPORTED_PERIOD",
    "UNUSABLE_RULE",
    "ApplicabilityConfigError",
    "ApplicabilityRecords",
    "ApplicabilityRequest",
    "ApplicabilityRule",
    "Fact",
    "Refused",
    "Resolved",
    "calculation_input_for",
    "resolve_applicability",
]


class ApplicabilityConfigError(ValueError):
    """Malformed request or rule configuration. Distinct from a valid request
    whose applicability is simply unknown (reported via Refused)."""


# Stable refusal reason codes.
MISSING_FACT = "MISSING_FACT"
NO_MATCHING_RULE = "NO_MATCHING_RULE"
CONFLICTING_RULES = "CONFLICTING_RULES"
UNSUPPORTED_PERIOD = "UNSUPPORTED_PERIOD"
UNUSABLE_RULE = "UNUSABLE_RULE"

_VERIFIED = "verified"
_ALLOWED_STATUSES = ("verified", "unverified", "withdrawn")


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicabilityConfigError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class Fact:
    """One taxpayer fact as an opaque name/value pair.

    Presence carries meaning: a missing name differs from a name present
    with value "false", "0", or "not-applicable". Values are exact-match
    strings; the resolver performs no type coercion.
    """

    name: str
    value: str

    def __post_init__(self) -> None:
        _require_label("fact name", self.name)
        if not isinstance(self.value, str):
            raise ApplicabilityConfigError(f"fact value must be a string, got {self.value!r}")


@dataclass(frozen=True)
class ApplicabilityRequest:
    """Explicit resolution request. The rule set, not the period label,
    determines applicability."""

    period: str  # opaque label; implies nothing about which Act applies
    facts: tuple[Fact, ...]
    jurisdiction: str  # explicit context, echoed for reproducibility
    records_id: str  # binds the request to the evaluated record set

    def __post_init__(self) -> None:
        _require_label("period", self.period)
        _require_label("jurisdiction", self.jurisdiction)
        _require_label("records_id", self.records_id)
        if any(not isinstance(f, Fact) for f in self.facts):
            raise ApplicabilityConfigError("facts must be a tuple of Fact")
        names = [f.name for f in self.facts]
        if len(set(names)) != len(names):
            raise ApplicabilityConfigError("duplicate fact names are ambiguous")


@dataclass(frozen=True)
class ApplicabilityRule:
    """One versioned applicability record. Data only; decision logic lives
    in resolve_applicability. Only status "verified" records are usable."""

    rule_id: str
    version: str
    framework: str
    target_ruleset_id: str
    periods: tuple[str, ...]
    provenance: str
    conditions: tuple[Fact, ...] = ()
    status: str = "unverified"  # fail-closed default: unusable until verified

    def __post_init__(self) -> None:
        _require_label("rule_id", self.rule_id)
        _require_label("version", self.version)
        _require_label("framework", self.framework)
        _require_label("target_ruleset_id", self.target_ruleset_id)
        _require_label("provenance", self.provenance)
        if not self.periods or any(not isinstance(p, str) or not p.strip() for p in self.periods):
            raise ApplicabilityConfigError("periods must be a non-empty tuple of non-empty strings")
        if any(not isinstance(c, Fact) for c in self.conditions):
            raise ApplicabilityConfigError("conditions must be a tuple of Fact")
        if self.status not in _ALLOWED_STATUSES:
            raise ApplicabilityConfigError(f"unknown status {self.status!r}")


@dataclass(frozen=True)
class ApplicabilityRecords:
    """Explicit, versioned set of applicability records under evaluation."""

    records_id: str
    version: str
    rules: tuple[ApplicabilityRule, ...]

    def __post_init__(self) -> None:
        _require_label("records_id", self.records_id)
        _require_label("version", self.version)
        if any(not isinstance(r, ApplicabilityRule) for r in self.rules):
            raise ApplicabilityConfigError("rules must be a tuple of ApplicabilityRule")
        identities = [(r.rule_id, r.version) for r in self.rules]
        if len(set(identities)) != len(identities):
            raise ApplicabilityConfigError("duplicate (rule_id, version) identities are ambiguous")


@dataclass(frozen=True)
class Resolved:
    """Successful resolution. Records exactly which rule selected which
    ruleset, so the outcome is reproducible and auditable."""

    framework: str
    target_ruleset_id: str
    rule_id: str
    rule_version: str
    period: str
    jurisdiction: str
    facts: tuple[Fact, ...]
    provenance: str
    reason: str
    records_id: str
    records_version: str


@dataclass(frozen=True)
class Refused:
    """Typed refusal. `code` is one of the stable module-level constants."""

    code: str
    message: str
    period: str
    records_id: str


def resolve_applicability(
    request: ApplicabilityRequest, records: ApplicabilityRecords
) -> Resolved | Refused:
    """Match `request` against usable records. Never guesses, never defaults.

    Refusal precedence when several apply: MISSING_FACT (most actionable),
    then UNUSABLE_RULE, then NO_MATCHING_RULE. UNSUPPORTED_PERIOD and
    CONFLICTING_RULES are determined structurally.
    """
    if not isinstance(request, ApplicabilityRequest) or not isinstance(records, ApplicabilityRecords):
        raise ApplicabilityConfigError("expected ApplicabilityRequest and ApplicabilityRecords")
    if request.records_id != records.records_id:
        raise ApplicabilityConfigError(
            f"request targets records {request.records_id!r}, got {records.records_id!r}"
        )

    candidates = [r for r in records.rules if request.period in r.periods]
    if not candidates:
        return Refused(
            UNSUPPORTED_PERIOD,
            f"no applicability record supports period {request.period!r}",
            request.period,
            records.records_id,
        )

    usable = [r for r in candidates if r.status == _VERIFIED]
    fact_map = {f.name: f.value for f in request.facts}
    matches = [
        r
        for r in usable
        if all(fact_map.get(c.name) == c.value for c in r.conditions)
    ]
    if len(matches) > 1:
        ids = ", ".join(f"{r.rule_id} {r.version}" for r in matches)
        return Refused(
            CONFLICTING_RULES,
            f"conflicting matching rules: {ids}",
            request.period,
            records.records_id,
        )
    if len(matches) == 1:
        rule = matches[0]
        return Resolved(
            framework=rule.framework,
            target_ruleset_id=rule.target_ruleset_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            period=request.period,
            jurisdiction=request.jurisdiction,
            facts=request.facts,
            provenance=rule.provenance,
            reason=f"exact-match rule {rule.rule_id} {rule.version}",
            records_id=records.records_id,
            records_version=records.version,
        )
    for rule in usable:
        for condition in rule.conditions:
            if condition.name not in fact_map:
                return Refused(
                    MISSING_FACT,
                    f"rule {rule.rule_id} {rule.version} requires fact {condition.name!r}",
                    request.period,
                    records.records_id,
                )
    if not usable:
        return Refused(
            UNUSABLE_RULE,
            "matching records exist but none is verified for use",
            request.period,
            records.records_id,
        )
    return Refused(
        NO_MATCHING_RULE,
        "no verified rule matches the given period and facts",
        request.period,
        records.records_id,
    )


def calculation_input_for(
    resolution: Resolved | Refused,
    ruleset: Ruleset,
    gross_income,
    assumptions: tuple[str, ...] = (),
) -> CalculationInput:
    """Smallest contract-level bridge to the calculator.

    Raises CalculationError when applicability is unresolved (Refused) or
    when the supplied ruleset does not match the resolved framework and
    target identifier, so an unresolved result can never flow downstream
    through a hidden default.
    """
    if isinstance(resolution, Refused):
        raise CalculationError(f"applicability unresolved ({resolution.code}): {resolution.message}")
    if not isinstance(resolution, Resolved):
        raise ApplicabilityConfigError("expected Resolved or Refused")
    if ruleset.ruleset_id != resolution.target_ruleset_id:
        raise CalculationError(
            f"ruleset {ruleset.ruleset_id!r} does not match resolved {resolution.target_ruleset_id!r}"
        )
    if ruleset.framework != resolution.framework:
        raise CalculationError(
            f"ruleset framework {ruleset.framework!r} does not match resolved {resolution.framework!r}"
        )
    return CalculationInput(
        gross_income=gross_income,
        period=resolution.period,
        regime=ruleset.regime,
        ruleset_id=ruleset.ruleset_id,
        assumptions=tuple(assumptions),
    )
