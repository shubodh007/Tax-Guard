"""Worked example + boundary tests for the deterministic calculation contract.

The `synthetic-fixture-v0` ruleset below is a SYNTHETIC TEST FIXTURE. It is
not Indian tax law, is not legally applicable to any taxpayer, and exists
only to demonstrate the contract in `docs/calculation-contract.md`.

No external services required.
"""

from decimal import Decimal

import pytest

from backend.app.calculation import (
    CalculationError,
    CalculationInput,
    Ruleset,
    TaxSlab,
    calculate_tax,
)


def synthetic_ruleset() -> Ruleset:
    """Fresh fixture instance per call (also proves no shared state)."""
    return Ruleset(
        ruleset_id="synthetic-fixture-v0",
        framework="SYNTHETIC TEST FIXTURE — not Indian tax law",
        regime="synthetic-progressive",
        periods=("2024-25",),
        slabs=(
            TaxSlab(Decimal("0"), Decimal("250000"), Decimal("0")),
            TaxSlab(Decimal("250000"), Decimal("500000"), Decimal("0.05")),
            TaxSlab(Decimal("500000"), None, Decimal("0.20")),
        ),
    )


def request_for(income: Decimal, **overrides) -> CalculationInput:
    fields = {
        "gross_income": income,
        "period": "2024-25",
        "regime": "synthetic-progressive",
        "ruleset_id": "synthetic-fixture-v0",
        "assumptions": ("no deductions claimed",),
    }
    fields.update(overrides)
    return CalculationInput(**fields)  # type: ignore[arg-type]


def test_worked_example() -> None:
    result = calculate_tax(request_for(Decimal("850000")), synthetic_ruleset())

    assert result.total_tax == Decimal("82500")
    assert result.taxable_income == Decimal("850000")
    assert result.ruleset_id == "synthetic-fixture-v0"
    assert result.framework.startswith("SYNTHETIC TEST FIXTURE")
    assert result.rounding_applied == "ROUND_HALF_UP to 1"
    assert result.assumptions == ("no deductions claimed",)
    assert [(line.portion, line.tax) for line in result.lines] == [
        (Decimal("250000"), Decimal("0")),
        (Decimal("250000"), Decimal("12500")),
        (Decimal("350000"), Decimal("70000")),
    ]


def test_rounding_truncates_fractional_paise() -> None:
    # (300000.07 - 250000) * 0.05 = 2500.0035 -> ROUND_HALF_UP to 1 -> 2500
    result = calculate_tax(request_for(Decimal("300000.07")), synthetic_ruleset())
    assert result.lines[1].tax == Decimal("2500.0035")
    assert result.total_tax == Decimal("2500")


def test_rounding_half_up_on_exact_half() -> None:
    # (300010 - 250000) * 0.05 = 2500.5 -> ROUND_HALF_UP to 1 -> 2501
    result = calculate_tax(request_for(Decimal("300010")), synthetic_ruleset())
    assert result.total_tax == Decimal("2501")


def test_zero_income_is_valid_zero_not_missing() -> None:
    result = calculate_tax(request_for(Decimal("0")), synthetic_ruleset())
    assert result.total_tax == Decimal("0")
    assert all(line.tax == 0 for line in result.lines)


def test_deterministic_across_repeated_calls() -> None:
    first = calculate_tax(request_for(Decimal("850000")), synthetic_ruleset())
    second = calculate_tax(request_for(Decimal("850000")), synthetic_ruleset())
    assert first == second


@pytest.mark.parametrize("bad_income", [Decimal("-1"), 850000, 850000.0, "850000", None])
def test_invalid_incomes_rejected(bad_income) -> None:
    with pytest.raises(CalculationError):
        request_for(bad_income)


@pytest.mark.parametrize(
    "field,value",
    [("period", ""), ("period", "1999-00"), ("regime", "other-regime"), ("ruleset_id", "unknown-v9")],
)
def test_unknown_rules_rejected(field, value) -> None:
    with pytest.raises(CalculationError):
        calculate_tax(request_for(Decimal("850000"), **{field: value}), synthetic_ruleset())


def test_invalid_rulesets_rejected() -> None:
    base = {"ruleset_id": "bad", "framework": "SYNTHETIC", "regime": "r", "periods": ("p",)}
    bad_slabs = [
        (),  # empty
        (TaxSlab(Decimal("100"), Decimal("200"), Decimal("0.1")),),  # must start at 0
        (  # overlap: second lower != first upper
            TaxSlab(Decimal("0"), Decimal("200"), Decimal("0.1")),
            TaxSlab(Decimal("100"), Decimal("300"), Decimal("0.1")),
        ),
        (  # gap
            TaxSlab(Decimal("0"), Decimal("100"), Decimal("0.1")),
            TaxSlab(Decimal("200"), None, Decimal("0.1")),
        ),
        ((TaxSlab(Decimal("0"), None, Decimal("0.1")), TaxSlab(Decimal("0"), None, Decimal("0.1")))),  # open-ended non-last
        ((TaxSlab(Decimal("0"), Decimal("100"), Decimal("1.5")),)),  # rate > 1
    ]
    for slabs in bad_slabs:
        with pytest.raises(CalculationError):
            Ruleset(slabs=slabs, **base)
    with pytest.raises(CalculationError):
        Ruleset(
            slabs=(TaxSlab(Decimal("0"), None, Decimal("0.1")),),
            rounding="ROUND_SOMETHING",
            **base,
        )
