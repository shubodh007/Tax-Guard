"""Deterministic tax-calculation primitives. Standard library only.

Pure functions over frozen dataclasses: no FastAPI, database, network,
LLM, clock, environment, or hidden-state dependency. Identical inputs plus
an identical ruleset always yield an identical result. See
``docs/calculation-contract.md`` for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)

__all__ = [
    "CALCULATION_SCHEMA_VERSION",
    "CalculationError",
    "CalculationInput",
    "CalculationResult",
    "Ruleset",
    "SlabLine",
    "TaxSlab",
    "calculate_tax",
]

# Version of the calculation identity semantics (framework/ruleset/result
# references). Referenced by evidence-trace calculation references.
CALCULATION_SCHEMA_VERSION = 1


class CalculationError(ValueError):
    """Any invalid input, unknown rule reference, or malformed ruleset."""


_ROUNDING_MODES = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "ROUND_CEILING": ROUND_CEILING,
    "ROUND_FLOOR": ROUND_FLOOR,
}


def _require_money(name: str, value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise CalculationError(f"{name} must be a finite Decimal, got {value!r}")
    return value


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalculationError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class TaxSlab:
    """One progressive bracket: [lower, upper) at `rate` (fraction, 0-1)."""

    lower: Decimal
    upper: Decimal | None  # None = open-ended; allowed on the last slab only
    rate: Decimal


@dataclass(frozen=True)
class Ruleset:
    """Versioned rule set. Construction validates slab geometry and policy."""

    ruleset_id: str
    framework: str  # e.g. "SYNTHETIC TEST FIXTURE" until real rules arrive
    regime: str
    periods: tuple[str, ...]  # opaque supported period labels
    slabs: tuple[TaxSlab, ...]
    quantum: Decimal = Decimal("1")  # final-total rounding quantum
    rounding: str = "ROUND_HALF_UP"

    def __post_init__(self) -> None:
        _require_label("ruleset_id", self.ruleset_id)
        _require_label("framework", self.framework)
        _require_label("regime", self.regime)
        if not self.periods or any(not isinstance(p, str) or not p.strip() for p in self.periods):
            raise CalculationError("periods must be a non-empty tuple of non-empty strings")
        if not self.slabs:
            raise CalculationError("slabs must be a non-empty tuple of TaxSlab")
        for i, slab in enumerate(self.slabs):
            _require_money(f"slabs[{i}].lower", slab.lower)
            if slab.upper is not None:
                _require_money(f"slabs[{i}].upper", slab.upper)
                if slab.upper <= slab.lower:
                    raise CalculationError(f"slabs[{i}].upper must exceed lower")
            elif i != len(self.slabs) - 1:
                raise CalculationError("only the last slab may be open-ended")
            _require_money(f"slabs[{i}].rate", slab.rate)
            if not Decimal("0") <= slab.rate <= Decimal("1"):
                raise CalculationError(f"slabs[{i}].rate must be within [0, 1]")
            if i == 0:
                if slab.lower != Decimal("0"):
                    raise CalculationError("first slab must start at 0")
            elif slab.lower != self.slabs[i - 1].upper:
                raise CalculationError(f"slabs[{i}].lower must equal previous slab upper (contiguous, no overlap/gap)")
        _require_money("quantum", self.quantum)
        if self.quantum <= 0:
            raise CalculationError("quantum must be positive")
        if self.rounding not in _ROUNDING_MODES:
            raise CalculationError(f"unknown rounding mode {self.rounding!r}")


@dataclass(frozen=True)
class CalculationInput:
    """Explicit calculation request. Missing/invalid fields fail, never default."""

    gross_income: Decimal
    period: str
    regime: str
    ruleset_id: str
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_money("gross_income", self.gross_income)
        if self.gross_income < 0:
            raise CalculationError("gross_income must not be negative")
        _require_label("period", self.period)
        _require_label("regime", self.regime)
        _require_label("ruleset_id", self.ruleset_id)
        if any(not isinstance(a, str) for a in self.assumptions):
            raise CalculationError("assumptions must be a tuple of strings")


@dataclass(frozen=True)
class SlabLine:
    """One trace line: unrounded line tax at full intermediate precision."""

    index: int
    lower: Decimal
    upper: Decimal | None
    rate: Decimal
    portion: Decimal
    tax: Decimal


@dataclass(frozen=True)
class CalculationResult:
    """Reproducible outcome: equal inputs + equal ruleset => equal result."""

    total_tax: Decimal
    taxable_income: Decimal
    ruleset_id: str
    framework: str
    period: str
    regime: str
    lines: tuple[SlabLine, ...]
    assumptions: tuple[str, ...]
    rounding_applied: str


def calculate_tax(request: CalculationInput, ruleset: Ruleset) -> CalculationResult:
    """Apply `ruleset` to `request` progressively. Raises CalculationError on any mismatch."""
    if request.ruleset_id != ruleset.ruleset_id:
        raise CalculationError(f"unknown ruleset {request.ruleset_id!r}")
    if request.regime != ruleset.regime:
        raise CalculationError(f"unsupported regime {request.regime!r} for ruleset {ruleset.ruleset_id!r}")
    if request.period not in ruleset.periods:
        raise CalculationError(f"unsupported period {request.period!r} for ruleset {ruleset.ruleset_id!r}")

    income = request.gross_income
    lines: list[SlabLine] = []
    unrounded_total = Decimal("0")
    for i, slab in enumerate(ruleset.slabs):
        cap = slab.upper if slab.upper is not None else income
        portion = min(income, cap) - slab.lower
        if portion < 0:
            portion = Decimal("0")
        line_tax = portion * slab.rate
        unrounded_total += line_tax
        lines.append(SlabLine(i, slab.lower, slab.upper, slab.rate, portion, line_tax))

    total_tax = unrounded_total.quantize(ruleset.quantum, rounding=_ROUNDING_MODES[ruleset.rounding])
    return CalculationResult(
        total_tax=total_tax,
        taxable_income=income,
        ruleset_id=ruleset.ruleset_id,
        framework=ruleset.framework,
        period=request.period,
        regime=request.regime,
        lines=tuple(lines),
        assumptions=request.assumptions,
        rounding_applied=f"{ruleset.rounding} to {ruleset.quantum}",
    )
