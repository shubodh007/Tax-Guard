# TaxGuard Calculation Contract (Phase 1)

Status: contract + minimal worked example only. There is no production tax
engine, no rate table of Indian law, and no applicability resolver yet.
Anything labelled `synthetic-fixture` is a test fixture with no legal force
and must never be presented as tax advice.

## 1. Input contract (`CalculationInput`)

| Field | Type | Semantics |
|---|---|---|
| `gross_income` | `Decimal` (finite, ≥ 0) | Monetary amount in rupees. Must be `Decimal` — `float`, `int`, `str`, and `None` are rejected. |
| `period` | non-empty `str` | Opaque period label (e.g. `"2024-25"`). A label alone implies **nothing** about which Act applies. |
| `regime` | non-empty `str` | Requested calculation method label. Must exactly match the ruleset's regime. |
| `ruleset_id` | non-empty `str` | Exact identifier of the rule set to apply. Must exactly match the ruleset. |
| `assumptions` | `tuple[str, ...]` | Explicit assumptions (may be empty). Anything not listed here was not assumed. |

Missing vs. invalid vs. zero:

- Missing (`None`, absent, blank string) → `CalculationError`. Never defaulted.
- Invalid (wrong type, negative income, NaN/Infinity, unknown regime/period/ruleset id) → `CalculationError`. Never coerced.
- Valid zero (`Decimal("0")`) → a real calculation returning zero tax with a full trace. Zero is evidence, not absence.

## 2. Output contract (`CalculationResult`)

A successful result carries: `total_tax` (`Decimal`), `taxable_income`
(`Decimal`), the `ruleset_id` / `framework` / `period` / `regime` used, a
per-slab trace (`SlabLine`: bounds, rate, taxable portion, unrounded line
tax), the echoed `assumptions`, and a `rounding_applied` description.

Reproducibility rule: the same `CalculationInput` plus the same `Ruleset`
object always yields an equal result. No clock, environment, randomness,
or hidden state may influence output.

## 3. Ruleset contract (`Ruleset` + `TaxSlab`)

- `ruleset_id`, `framework` (e.g. `"Income-tax Act, 1961"` once real rules
  arrive; `"SYNTHETIC TEST FIXTURE"` for now), `regime`, supported
  `periods`, non-empty ordered `slabs`, plus rounding policy (`quantum` +
  `rounding` mode name).
- Slab validity: bounds are finite `Decimal`s, first slab starts at 0,
  slabs are contiguous (`next.lower == prev.upper`), `upper > lower`,
  only the last slab may be open-ended (`upper=None`), each rate satisfies
  `0 ≤ rate ≤ 1`. Violations → `CalculationError` at ruleset construction.
- A request referencing an unknown `ruleset_id`, a regime the ruleset does
  not define, or a period the ruleset does not list → `CalculationError`.
  The calculator fabricates no result when the rules are unknown.

## 4. Money and rounding

- All authoritative arithmetic uses `decimal.Decimal`. Binary `float` is
  banned from inputs and rules; floats are rejected, not converted.
- Intermediate line taxes keep full precision (no per-line rounding).
- The final total is quantized once to the ruleset's `quantum` with its
  named rounding mode (e.g. `ROUND_HALF_UP to 1`). The fixture rounds only
  the final total; whether real provisions round taxable income, and to
  what quantum, is a property of each future versioned ruleset — there is
  no universal policy assumed here.
- `quantize` is always called with an explicit rounding argument, so
  results do not depend on the ambient decimal context.

## 5. Legal-version safety

- Every calculation names its `framework` + `ruleset_id` explicitly so the
  applicability resolver (1961 Act vs. 2025 Act vs. transition provisions;
  see `docs/applicability-contract.md`) can supply them. The calculator
  itself resolves nothing.
- No complete rate table is implemented in this phase. Real rates require
  citing the authoritative source (Act section / Finance Act / notification)
  and its effective period before encoding.
- Until then, tests use an explicitly synthetic slab fixture. Its outputs
  demonstrate the contract only and carry no legal meaning.

## 6. Worked example (synthetic fixture `synthetic-fixture-v0`)

Slabs: 0–250000 @ 0%, 250000–500000 @ 5%, 500000+ @ 20%;
rounding `ROUND_HALF_UP to 1`.

Input `gross_income = 850000` → portions 250000 / 250000 / 350000 →
line taxes 0 / 12500 / 70000 → **`total_tax = 82500`**.
See `tests/test_calculation.py::test_worked_example`.
