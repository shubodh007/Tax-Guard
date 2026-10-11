# TaxGuard Claim-Verification Contract (Phase 4)

Status: interface contract only. There is no semantic verifier, no LLM, no
retrieval provider, and no verified legal data. Anything labelled
`synthetic-` is a test fixture with no legal force and must never be
presented as tax advice.

## Function semantics

`verify_claim(claim, evidence, policy, calculation=None, legal_basis=None)`
(`backend/app/verification.py`) deterministically evaluates one `ClaimRef`
against an explicitly supplied evidence subset under an explicit
`VerificationPolicy`. Same inputs always yield the same
`VerificationOutcome`. Conclusions come only from the policy's configured
claim sets — the core performs no semantic judgment and claims no
verification capability it does not have.

## Conclusions (reusing envelope terminology exactly)

- `supported`: the claim is in the policy's supported set **and** every
  cited supporting id is present in the subset (citations alone never
  suffice; the policy is the defined basis).
- `contradicted`: the claim is in the policy's contradicted set **and**
  every cited contradicting id is present.
- `insufficient_evidence`: the subset cannot establish support or
  contradiction (cited ids absent, or none cited).
- `unverified`: no conclusion attempted or established (claim not covered
  by the policy, or a prerequisite is missing).
- `verification_failed`: the operation failed and no trustworthy
  conclusion exists. Never support.

## Operational codes

`evaluated` (completed evaluation) · `missing_evidence` · `invalid_input`
· `unsupported_operation` · `provider_failure` · `quota_exhausted` ·
`timeout`. Failure and quota codes are supported contract states for future
provider adapters; this phase never observes a real provider. Provider
failure is never evidence against a claim, quota exhaustion is never
insufficient evidence, and neither can yield a verified result.

## Valid conclusion/code combinations

| Code | Permitted conclusion |
|---|---|
| `evaluated` | `supported`, `contradicted`, `insufficient_evidence` |
| `missing_evidence` | `insufficient_evidence` |
| `invalid_input`, `unsupported_operation` | `unverified` |
| `provider_failure`, `quota_exhausted`, `timeout` | `verification_failed` |

Anything else raises `VerificationError` at outcome construction, so a
failure cannot be built as a success. Malformed inputs (wrong types,
blank ids, duplicate subset ids) also raise `VerificationError` — kept
distinct from valid-but-unsuccessful evaluations.

## Evidence-subset rules

The subset is the complete input: cited ids absent from it are missing
evidence (`missing_evidence`), never synthesized. Contradicting ids are
evaluated independently — supporting evidence never discards them.
`EvidenceRef` objects pass through into the outcome unchanged, so
`unverified`/`synthetic` trust labels are preserved and never upgraded.
A URL or citation in metadata confers no authority, and no similarity
signal is read as truth; relevance and correctness stay separate.

## Calculation references

An optional `CalculationRef` is accepted without recalculation. When
present, a resolved `LegalBasis` must accompany it and its framework and
ruleset id must match, or the outcome is `unverified`/`invalid_input` —
a mismatched ruleset never supports a claim, and a calculation's
existence never certifies the underlying rules. Non-numerical claims omit
the reference freely. A claim listed in
`policy.require_calculation_for` without a supplied reference yields
`unverified`/`unsupported_operation` rather than an invented calculation.

## Trace integration

`apply_outcome(trace, outcome)` returns a new `EvidenceTrace`: the claim
keeps its ids and role, takes the outcome's conclusion as status and its
message as reason; evidence (including trust) and the legal basis are
untouched. Overall status is recomputed as all-supported → `verified`,
else `partial`. Safeguards: failures can only demote, never promote;
unknown claims and unresolved bases raise instead of applying; envelope
validation still runs on the rebuilt trace. Refusal stays refusal —
verification cannot resolve applicability, and outcomes certify nothing
about registry completeness.

## Synthetic testing

The policy's `fault` switch (`none` by default; `provider_failure` /
`quota_exhausted` / `timeout` for tests) emits failure outcomes after
input validation, so failure paths are exercised without pretending a
provider was called. All fixtures are labeled synthetic. These tests
prove contract mechanics only — not legal correctness, which requires
real verified sources plus a real verifier (a future adapter must map
provider errors to the failure codes and success semantics defined here).
