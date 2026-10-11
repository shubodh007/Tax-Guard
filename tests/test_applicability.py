"""Resolver tests using SYNTHETIC fixtures only.

Nothing here is Indian tax law, is legally applicable to any taxpayer, or
validates real applicability. Fixtures demonstrate resolver mechanics per
`docs/applicability-contract.md`. No external services required.
"""

from decimal import Decimal

import pytest

from backend.app.applicability import (
    CONFLICTING_RULES,
    MISSING_FACT,
    NO_MATCHING_RULE,
    UNSUPPORTED_PERIOD,
    UNUSABLE_RULE,
    ApplicabilityConfigError,
    ApplicabilityRecords,
    ApplicabilityRequest,
    ApplicabilityRule,
    Fact,
    Refused,
    Resolved,
    calculation_input_for,
    resolve_applicability,
)
from backend.app.calculation import (
    CalculationError,
    CalculationInput,
    Ruleset,
    TaxSlab,
    calculate_tax,
)

FRAMEWORK = "SYNTHETIC TEST FIXTURE — not Indian tax law"
PROVENANCE = "SYNTHETIC TEST FIXTURE — not a legal source"
RECORDS_ID = "synthetic-applicability-v0"


def resident_rule(**overrides) -> ApplicabilityRule:
    fields = {
        "rule_id": "synthetic-rule-A",
        "version": "v1",
        "framework": FRAMEWORK,
        "target_ruleset_id": "synthetic-mini-v0",
        "periods": ("2024-25",),
        "provenance": PROVENANCE,
        "conditions": (Fact("residential-status", "resident"),),
        "status": "verified",
    }
    fields.update(overrides)
    return ApplicabilityRule(**fields)  # type: ignore[arg-type]


def synthetic_records(*rules: ApplicabilityRule) -> ApplicabilityRecords:
    return ApplicabilityRecords(records_id=RECORDS_ID, version="v0", rules=rules or (resident_rule(),))


def request_for(
    facts: tuple[tuple[str, str], ...] = (("residential-status", "resident"),),
    period: str = "2024-25",
    records_id: str = RECORDS_ID,
) -> ApplicabilityRequest:
    return ApplicabilityRequest(
        period=period,
        facts=tuple(Fact(name, value) for name, value in facts),
        jurisdiction="IN",
        records_id=records_id,
    )


def mini_ruleset() -> Ruleset:
    """Minimal calculation ruleset matching the synthetic rule's target."""
    return Ruleset(
        ruleset_id="synthetic-mini-v0",
        framework=FRAMEWORK,
        regime="synthetic-mini",
        periods=("2024-25",),
        slabs=(TaxSlab(Decimal("0"), None, Decimal("0.10")),),
    )


def test_resolves_successfully() -> None:
    outcome = resolve_applicability(request_for(), synthetic_records())

    assert isinstance(outcome, Resolved)
    assert outcome.framework == FRAMEWORK
    assert outcome.target_ruleset_id == "synthetic-mini-v0"
    assert outcome.rule_id == "synthetic-rule-A"
    assert outcome.rule_version == "v1"
    assert outcome.period == "2024-25"
    assert outcome.jurisdiction == "IN"
    assert outcome.facts == (Fact("residential-status", "resident"),)
    assert outcome.provenance == PROVENANCE
    assert outcome.reason == "exact-match rule synthetic-rule-A v1"
    assert outcome.records_id == RECORDS_ID
    assert outcome.records_version == "v0"


def test_missing_required_fact_refused() -> None:
    outcome = resolve_applicability(request_for(facts=()), synthetic_records())

    assert isinstance(outcome, Refused)
    assert outcome.code == MISSING_FACT
    assert "residential-status" in outcome.message


def test_no_matching_rule_for_wrong_value() -> None:
    outcome = resolve_applicability(
        request_for(facts=(("residential-status", "non-resident"),)),
        synthetic_records(),
    )

    assert isinstance(outcome, Refused)
    assert outcome.code == NO_MATCHING_RULE


def test_conflicting_rules_refused() -> None:
    records = synthetic_records(
        resident_rule(),
        resident_rule(rule_id="synthetic-rule-B", version="v2"),
    )
    outcome = resolve_applicability(request_for(), records)

    assert isinstance(outcome, Refused)
    assert outcome.code == CONFLICTING_RULES
    assert "synthetic-rule-A" in outcome.message and "synthetic-rule-B" in outcome.message


def test_unsupported_period_refused() -> None:
    outcome = resolve_applicability(request_for(period="1999-00"), synthetic_records())

    assert isinstance(outcome, Refused)
    assert outcome.code == UNSUPPORTED_PERIOD


@pytest.mark.parametrize("status", ["unverified", "withdrawn"])
def test_unusable_rule_refused(status: str) -> None:
    outcome = resolve_applicability(
        request_for(), synthetic_records(resident_rule(status=status))
    )

    assert isinstance(outcome, Refused)
    assert outcome.code == UNUSABLE_RULE


def test_invalid_rule_configurations_rejected() -> None:
    base = {
        "rule_id": "r",
        "version": "v1",
        "framework": FRAMEWORK,
        "target_ruleset_id": "t",
        "periods": ("2024-25",),
        "provenance": PROVENANCE,
    }
    with pytest.raises(ApplicabilityConfigError):
        ApplicabilityRule(**{**base, "status": "verfied"})  # type: ignore[arg-type]
    with pytest.raises(ApplicabilityConfigError):
        ApplicabilityRule(**{**base, "periods": ()})  # type: ignore[arg-type]
    with pytest.raises(ApplicabilityConfigError):
        ApplicabilityRule(**{**base, "rule_id": "  "})  # type: ignore[arg-type]
    with pytest.raises(ApplicabilityConfigError):
        ApplicabilityRecords(  # duplicate (rule_id, version)
            records_id=RECORDS_ID,
            version="v0",
            rules=(resident_rule(), resident_rule()),
        )


def test_invalid_requests_rejected() -> None:
    with pytest.raises(ApplicabilityConfigError):
        request_for(period="  ")
    with pytest.raises(ApplicabilityConfigError):
        ApplicabilityRequest(
            period="2024-25",
            facts=(Fact("a", "1"), Fact("a", "2")),
            jurisdiction="IN",
            records_id=RECORDS_ID,
        )
    with pytest.raises(ApplicabilityConfigError):
        resolve_applicability(request_for(records_id="other-records"), synthetic_records())


def test_deterministic_repeated_resolution() -> None:
    records = synthetic_records()
    assert resolve_applicability(request_for(), records) == resolve_applicability(
        request_for(), records
    )


def test_framework_and_ruleset_identity_preserved() -> None:
    rule = resident_rule(
        rule_id="synthetic-rule-Z",
        version="v7",
        framework="SYNTHETIC FRAMEWORK Z",
        target_ruleset_id="synthetic-target-Z",
    )
    outcome = resolve_applicability(request_for(), synthetic_records(rule))

    assert isinstance(outcome, Resolved)
    assert (outcome.framework, outcome.target_ruleset_id) == (
        "SYNTHETIC FRAMEWORK Z",
        "synthetic-target-Z",
    )


def test_refusal_blocks_downstream_calculation() -> None:
    refused = resolve_applicability(request_for(facts=()), synthetic_records())
    assert isinstance(refused, Refused)
    with pytest.raises(CalculationError, match="MISSING_FACT"):
        calculation_input_for(refused, mini_ruleset(), Decimal("1000"))


def test_resolved_flows_to_calculator_with_matching_ruleset() -> None:
    outcome = resolve_applicability(request_for(), synthetic_records())
    assert isinstance(outcome, Resolved)

    calc_input = calculation_input_for(
        outcome, mini_ruleset(), Decimal("1000"), ("synthetic demo",)
    )
    assert isinstance(calc_input, CalculationInput)
    assert (calc_input.ruleset_id, calc_input.period) == ("synthetic-mini-v0", "2024-25")
    assert calculate_tax(calc_input, mini_ruleset()).total_tax == Decimal("100")


def test_mismatched_ruleset_rejected_at_bridge() -> None:
    outcome = resolve_applicability(request_for(), synthetic_records())
    assert isinstance(outcome, Resolved)
    other = Ruleset(
        ruleset_id="some-other-ruleset",
        framework=FRAMEWORK,
        regime="synthetic-mini",
        periods=("2024-25",),
        slabs=(TaxSlab(Decimal("0"), None, Decimal("0.10")),),
    )
    with pytest.raises(CalculationError, match="does not match resolved"):
        calculation_input_for(outcome, other, Decimal("1000"))
