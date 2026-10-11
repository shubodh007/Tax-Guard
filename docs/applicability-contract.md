# TaxGuard Applicability Contract (Phase 2)

Status: resolver skeleton only. There is no legal corpus, no verified rule
registry, and no production endpoint. Anything labelled `synthetic-` is a
test fixture with no legal force and must never be presented as tax advice.

A resolved framework does **not** prove the underlying tax ruleset is
legally correct — it only records which supplied record selected it.
Ruleset correctness requires independently verified legal data, which does
not exist in this phase.

## 1. Input contract (`ApplicabilityRequest`)

| Field | Type | Semantics |
|---|---|---|
| `period` | non-empty `str` | Opaque period label. Alone it determines **nothing**; it only filters records that explicitly list it. |
| `facts` | `tuple[Fact, ...]` | Taxpayer facts as opaque `(name, value)` string pairs. Each rule declares which facts it needs; unneeded facts are ignored, never collected speculatively. |
| `jurisdiction` | non-empty `str` | Explicit context, echoed for reproducibility. |
| `records_id` | non-empty `str` | Binds the request to the evaluated record set; mismatch is a configuration error. |

Missing vs. explicit: an absent fact name is *missing*; a present name with
value `"false"`, `"0"`, or `"not-applicable"` is an explicit answer and
matches only a condition expecting exactly that value. Values are
exact-match strings — no type coercion. Duplicate fact names are rejected
as ambiguous. Collect only facts a rule requires; no personal information
is defined by this contract.

## 2. Rule contract (`ApplicabilityRule` in `ApplicabilityRecords`)

Each rule states: stable `rule_id` + `version`, `framework` label,
`target_ruleset_id`, supported `periods`, required `conditions`
(`tuple[Fact, ...]`, possibly empty), `provenance` source reference, and
`status` (`"verified"` | `"unverified"` | `"withdrawn"`).

- Only `"verified"` records are usable; the default status is
  `"unverified"` (fail-closed). Unknown statuses, blank identifiers, empty
  periods, and duplicate `(rule_id, version)` identities are configuration
  errors raised at construction, not silent skips.
- No invented legal content is encoded: no effective dates, sections,
  transitions, or rates. A present record is not authoritative merely
  because it is present — usability requires verified status **and**
  full condition match.

## 3. Resolver behavior (`resolve_applicability`)

Pure function `(request, records) -> Resolved | Refused`, deterministic in
record order. Malformed requests or configurations raise
`ApplicabilityConfigError` (kept distinct from "valid but unknown").

**Resolved** carries: `framework`, `target_ruleset_id`, `rule_id`,
`rule_version`, `period`, `jurisdiction`, echoed `facts`, `provenance`,
machine-readable `reason` (`"exact-match rule <id> <version>"`), and the
`records_id` / `records_version` evaluated.

**Refused** carries a stable `code`, message, `period`, and `records_id`:

| Code | Meaning |
|---|---|
| `UNSUPPORTED_PERIOD` | no record lists the requested period |
| `MISSING_FACT` | a verified candidate requires an absent fact (names rule + fact) |
| `UNUSABLE_RULE` | candidates exist but none is verified |
| `CONFLICTING_RULES` | ≥ 2 verified rules fully match (lists them); no tie-breaking, no first-wins |
| `NO_MATCHING_RULE` | verified candidates exist but fact values match none |

Precedence when several apply: `MISSING_FACT` (most actionable), then
`UNUSABLE_RULE`, then `NO_MATCHING_RULE`. There is no fallback to the
first, latest, or default rule, and no LLM guess.

## 4. Legal-safety boundary

The resolver encodes no assumption linking a financial/assessment year to
the 1961 Act, the 2025 Act, or any transition. Selection comes solely from
supplied records satisfying the request. Synthetic fixtures demonstrate
mechanics only.

## 5. Orchestration (future layer; contract-level bridge provided)

1. Collect and validate only the facts the records require.
2. Call `resolve_applicability` with an explicit, versioned record set.
3. Stop on `Refused` — `calculation_input_for` raises `CalculationError`
   for refusals, so unresolved results cannot flow downstream.
4. Confirm the available `Ruleset` matches the resolved `framework` and
   `target_ruleset_id` (the bridge raises on mismatch).
5. Only then build `CalculationInput` and call `calculate_tax`.

No workflow, API endpoint, or default bypass is built in this phase.
