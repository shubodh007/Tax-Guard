# TaxGuard Evidence-Trace Contract (Phase 3)

Status: envelope schema only. There is no retrieval pipeline, no verifier,
no persistence, and no verified legal data. Anything labelled `synthetic-`
is a test fixture with no legal force and must never be presented as tax
advice.

## Purpose

`EvidenceTrace` (`backend/app/evidence.py`) is the shared auditable record
a future answer will carry: which legal basis was resolved, which evidence
was cited, what each claim's verification state is, and which calculation
was used. It stores references and identities — never full documents,
conversations, or taxpayer profiles.

## Schema version

`TRACE_SCHEMA_VERSION = 1`. Readers accept exactly this version and reject
anything else with `TraceError`. A future version must define its own
migration; silent acceptance of unknown versions is forbidden. The same
rule applies to `CalculationRef.contract_version` against
`CALCULATION_SCHEMA_VERSION`.

## Sections and required fields

- **Identity**: `trace_id` (non-empty), `schema_version` (= 1), `status`
  (see below), `created_at` (caller-supplied label, e.g. ISO-8601).
  `created_at` is informational ordering metadata only — it is never read
  by validation or verification logic, and the module never reads the clock.
- **Legal basis** (`LegalBasis`): built only via `from_resolved(Resolved)`
  or `from_refusal(Refused)`, reusing applicability terminology verbatim
  (framework, target ruleset id, rule id/version, period, records
  id/version, provenance; or refusal code/message with all identity fields
  empty). There is no constructor path that encodes a refusal as resolved.
- **Evidence** (`EvidenceRef`): required `evidence_id`; optional
  `document_id`, `document_version`, `section`, `passage_id`, `uri`,
  `effective`, `digest`; `trust` in `unverified` / `verified` / `synthetic`.
- **Claims** (`ClaimRef`): required `claim_id`, `role`, machine-readable
  `reason`; `supporting_ids` / `contradicting_ids`; `status` (see below).
- **Calculation** (`CalculationRef`, optional): `calculation_id`,
  `contract_version`, `framework`, `ruleset_id`, optional `result_digest`.
  Built via `from_result(CalculationResult, id)`. It points at a
  separately preserved calculation; amounts are not duplicated here.

## Status semantics

Claim statuses: `unverified`, `supported`, `contradicted`,
`insufficient_evidence`, `verification_failed`. A claim is `supported`
only with ≥ 1 cited supporting id, and `contradicted` only with ≥ 1 cited
contradicting id — but cited identifiers alone never *confer* support;
support is a future verifier's judgment. `verification_failed`
(provider/quota/process failure) and `insufficient_evidence` (missing
evidence) are never successful verification.

Overall trace statuses and their permission conditions:

| Status | Permitted when |
|---|---|
| `draft` | legal basis resolved; claims unrestricted |
| `verified` | basis resolved, ≥ 1 claim, **all** claims `supported` |
| `partial` | basis resolved, ≥ 1 claim, at least one claim not `supported` |
| `refused` | if and only if the legal basis is unresolved |
| `verification_failed` | basis resolved; claims unrestricted (failed process ≠ failed evidence) |

## Provenance and identity constraints

- Evidence ids and claim ids must each be unique within the trace.
- Every id a claim cites must exist in the envelope's evidence collection.
  Missing evidence stays missing — validation reports the unknown id and
  never synthesizes a citation.
- A calculation reference requires a resolved basis and must match its
  `framework` and target ruleset id exactly.
- Trust labels (`unverified`/`verified`/`synthetic`) are informational.
  Unverified or synthetic sources never silently become authoritative:
  synthetic fixtures stay labeled through serialization, and no rule
  upgrades trust implicitly.

## Validation failures

All violations raise `TraceError` (a `ValueError`): unsupported schema
versions, blank/malformed identifiers, duplicates, unknown evidence
references, empty support for `supported`/`contradicted` claims,
contradictory overall statuses, calculation identity conflicts, and
refused-basis mismatches. Malformed records are thus separated from valid
records describing unsuccessful processes (`partial`, `refused`,
`verification_failed`, `insufficient_evidence`), which must remain
representable.

## Serialization

`to_dict()` emits JSON-compatible mappings (tuples → lists, `None` →
null); `from_dict()` rebuilds and revalidates every invariant, rejecting
malformed input instead of coercing it. Round trip preserves equality.

## What this contract does not prove

A trace reference does not prove its legal source is correct, its evidence
is genuine, or its calculation used legally valid rates. It proves only
that the recorded identities, references, and statuses are internally
consistent. Real authority requires verified legal data, which does not
exist in this phase.

## Provenance bridge (Phase 17)

`resolve_provenance(passage, documents, sources, assessments=None)`
(`backend/app/provenance.py`, stdlib-only) joins a retrieved passage to
its records by exact ids — passage → document by `document_id`,
document → source by its `source_id`, source → assessment by the same
id — into one immutable `ResolvedProvenance` record. Passage text,
offsets, pages, and chunking provenance are echoed verbatim; score and
rank are echoed from `PassageResult` hits and stay `None` for bare
`Passage` inputs rather than invented. Each hop may miss: `unresolved`
names the failed links (`document`, `source`, `assessment`) in
canonical order while resolved fields stay populated — unknown stays
unknown, never guessed from filenames or titles. Verification status
and per-category verdicts are retained verbatim and never promoted by
a match.

`evidence_ref_for(resolved, trust="unverified")` builds the trace-ready
`EvidenceRef`: `evidence_id`/`passage_id` from the passage,
`document_id` from the document, `digest` from the document hash,
`uri` from the source origin (empty when unknown). `trust` is an
explicit caller label — matching confers no authority, so it is never
derived from score or rank.

## Future references

Retrieval will contribute `EvidenceRef` entries; calculation will
contribute `CalculationRef` via `from_result`; verification will advance
`ClaimRef.status` and the overall status within the permission table;
persistence may store `to_dict()` output keyed by `trace_id`.
