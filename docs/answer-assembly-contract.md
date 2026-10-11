# TaxGuard Answer-Assembly Contract (Phase 5)

Status: assembly contract only. There is no LLM generation, retrieval,
persistence, or frontend. Anything labelled `synthetic-` is a test
fixture with no legal force and must never be presented as tax advice.

## Purpose

`assemble_answer(trace, outcomes, policy)` (`backend/app/assembly.py`)
composes a validated `EvidenceTrace` plus exactly one
`VerificationOutcome` per claim into a versioned `AnswerResult`
(`ANSWER_SCHEMA_VERSION = 1`). It composes supplied information only:
no applicability resolution, no claim verification, no arithmetic, no
new facts. Answer text states identifiers, statuses, and counts — never
tax conclusions invented by assembly.

## Inputs and 1:1 rule

- `trace`: the validated envelope supplying identities (trace id, claims,
  evidence, legal basis, calculation reference).
- `outcomes`: exactly one outcome per trace claim — duplicates, unknown
  claim ids, missing claims, and mixed verification policies raise
  `AssemblyError`. Every outcome's evidence ids must exist in the trace;
  every non-empty calculation id must equal the trace's calculation id.
  Identity conflicts are caller bugs (rejected), not abstentions: the
  envelope already guarantees basis/calculation consistency, and assembly
  never re-derives it.
- `policy` (`AssemblyPolicy`): `policy_id`/`version` plus two fail-closed
  switches — `allow_partial` (default False) and `tolerate_unverified`
  (default False).

## Final statuses and precedence

`verified` / `partial` / `refused` / `verification_failed` / `draft`,
decided in this fixed order (safest accurate representation wins):

1. Unresolved legal basis → `refused`, reason = the basis refusal code.
   Refusal never converts, even with supported outcomes.
2. Any `verification_failed` conclusion → `verification_failed`, reason =
   the first failure code in trace order (`provider_failure`,
   `quota_exhausted`, `timeout` stay distinct). Failures are never
   outvoted by supported claims, and partial-on-failure is refused: the
   conservative boundary is fail-closed until a disclosure design exists.
3. Any `contradicted` conclusion → `refused`
   (`unresolved_contradiction`). A contradiction is never described as
   support and never silently omitted; answering around it requires a
   future policy, not silence.
4. Any `unverified` conclusion → `refused` (`unverified_claims_present`)
   unless tolerated; all-unverified and tolerated → `draft`
   (`verification_incomplete`), clearly not verified.
5. All `supported` → `verified` (`all_supported`), only with a resolved
   basis, consistent calculation reference, and no failures.
6. Otherwise (supported mixed with insufficient/ tolerated unverified) →
   `partial` (`partial_coverage`) iff allowed and ≥ 1 claim supported;
   else `refused` (`partial_not_permitted` / `no_supported_claims`).

Promotion is one-directional by construction: nothing in this policy
upgrades refused, failed, contradicted, or draft inputs.

## Claim and evidence selection

`verified` includes every claim; `partial` includes supported claims
only, names each excluded claim with its reason code, and marks itself
incomplete. `evidence_ids` is the union of included claims' cited
evidence ids (from the trace, not the verification subsets). Excluded
claims stay traceable (`included=False`) and never leak into the text.

## Abstention text

Deterministic templates keyed by reason code (missing facts, uncovered
period, no match, conflicts, unverified records, provider failure, quota,
timeout, contradiction, refusals, incomplete verification), with a neutral
generic fallback. Texts carry codes and counts only — no legal advice,
amounts, references, secrets, or exception internals. Machine-readable
`reason_code` travels separately from the human-readable `text`.

## What this contract does not prove

Assembly certifies internal consistency of supplied records, not legal
correctness, source genuineness, or rate validity. A `verified` answer
means "every included claim is supported under the stated policy and
identities" — nothing more.

## Future use

Generation adapters will fill text around (never instead of) this
structure; retrieval will supply the evidence the outcomes cite. Both
must preserve the precedence, the 1:1 rule, and the abstention map.
`to_dict`/`from_dict` (strict, JSON-compatible) serve future API and
frontend payloads.
