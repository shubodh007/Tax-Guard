# TaxGuard Source-Verification Contract (Phase 10)

Status: verification-evidence contract only. No source was downloaded,
no URL was fetched, no manifest was created or modified, and no record
here verifies a real source. Anything labelled `synthetic-` is a test
fixture with no legal force.

## The six separate assertions

Registration, claimed origin, checked origin, checked content
integrity, checked publication/version identity, and established legal
applicability are different claims. One label never implies the others:
verified integrity is not verified authenticity, and neither is legal
applicability. The policy below enforces the separation structurally.

## Record schema (`VerificationCheck`)

Required: `check_id`, `source_id`, `check_type`, `outcome`,
`description` (what was checked and its scope), `observation`
(recorded evidence or finding — explicit text even when nothing was
done). Optional, empty when unsupplied: `verifier`, `checked_at` (no
default timestamps are ever invented), `content_hash` (SHA-256 hex of
checked bytes when relevant — content identity only, never proof of
official authenticity), `limitations`. Observations are capped at 4000
characters: pointers, not document dumps or secrets.

Categories: `origin`, `integrity`, `version`, `completeness`,
`applicability`, and (Phase 14) `record_identity` — whether official
record metadata (title and identifiers) corresponds to the assessed
artifact. The original five keep their canonical order, so existing
reason codes are unaffected. Outcomes: `passed`, `failed`,
`inconclusive`, `not_checked`. Each check also records
`observation_source`: `local` (measured on this machine), `network`
(retrieved remotely), or empty (unsupplied, e.g. older records).
Malformed records (blank ids, unknown types/outcomes/sources, missing
description/observation, bad hashes, oversized observations) raise
`SourceVerificationError`. Strict `to_dict`/`from_dict` round-trips
preserve unsupplied fields as empty, and old payloads without the new
field still load.

## Policy and assessment (`assess`)

`VerificationPolicy{policy_id, version, required_checks}` (non-empty).
`assess(source_id, checks, policy)` reads only the supplied records —
never the registry, URLs, files, or network — and returns an
`Assessment{source_id, status, policy_id, policy_version,
reason_codes, check_ids, verdicts}` with a `to_dict()` mapping.

Per-category verdicts (canonical category order): `satisfied` (≥1
passed, no failed/inconclusive), `conflicting` (passed and failed),
`failed` (failed, none passed), `uncertain` (inconclusive without
failure), `missing` (nothing evaluated, required), `not_required`
(non-required, no records). Reason codes name every required verdict
as `<verdict>:<category>`. Status, fail-closed:
`conflicting` > `failed` > `incomplete`, else `verified` — and
`verified` additionally requires zero failed records anywhere
considered, so an unreviewed failure elsewhere still blocks it.
`verified` therefore covers exactly the required categories, and the
verdict table states what remains unknown.

Structural guarantees: gov-looking origins and successful hashes
cannot promote anything (the policy never inspects them — only
`passed` records satisfy); mixed-source or duplicate check ids raise;
existing `SourceRecord` objects and registries are never mutated.

## Reconciliation (`reconcile`, Phase 14)

`reconcile(source, observed, expected, official=None, document=None,
...)` compares locally measured file facts (`ObservedArtifact`),
receipt expectations (`ExpectedArtifact`), an optional parsed
`DocumentRecord`, and optional official metadata (`OfficialRecord`)
into one check per decidable category, with deterministic ids:

- integrity (always `local`): passed on full hash+size match
  (a matching parsed record is noted; a mismatching one fails the
  chain), failed on mismatch, inconclusive when either side is missing.
- record_identity: passed on official-title match (record URL and act
  label cited), failed on title conflict, not_checked without official
  metadata, inconclusive when metadata has no title. An official size
  differing from the observed size is reported verbatim in the
  observation without changing a title-driven verdict — discrepancies
  stay visible, never silently resolved.
- version, completeness, applicability: not_checked (this evidence
  establishes none of them).

`fetch_json_metadata()` (acquisition module) reuses the fetch policy
for small opt-in metadata reads; nothing calls it implicitly, and unit
tests never require the network. A `verified` assessment still covers
exactly its required categories — corroborated identity is not
authenticity, currency, completeness, or applicability.

## What this cannot establish

Official authenticity without actually performed and recorded checks;
version identity without version evidence; completeness; legal
applicability from metadata, hashes, or authenticity verdicts. A
`verified` assessment is a statement about recorded evidence under a
stated policy — not a legal opinion.

## Evidence-backed promotion (Phase 27)

A non-`unverified` source/document label is authorized only by
`authorize_status()` against a `verified` (or, for `disputed`, failed /
conflicting) assessment for the same source, under the same policy,
with a passed integrity record binding the exact content digest.
Manifest entries carry the authorizing assessment's canonical digest
(`assessment_digest`, empty for unverified entries); `entry_for()`
refuses non-unverified records without evidence, `entry_for_verified()`
authorizes them, and `verify_entry_binding()` revalidates the reference
so changed assessments invalidate stale entries. Revalidation takes its
policy from a trusted configuration — `canonical_policy()`, pinned in
code — never from the assessment file: reason codes are self-reported
strings, and rebuilding requirements from them would let a serialized
assessment choose or weaken its own policy. Finally, origin
satisfaction is never accepted from supplied records — the system's own
machinery reports origin inconclusive by construction, so no
independently trusted origin evidence exists: a policy requiring origin
(which the canonical policy does) keeps the source `unverified` rather
than weakly verifying it. A hash of a self-authored assessment is not
independent evidence. The digest joins the
snapshot only when present, so pre-binding manifests reproduce their
snapshots unchanged. Application startup revalidates every
non-`unverified` entry against its sibling `verification.json` under
the canonical policy and fails closed (generic 503) on absent, stale,
or mismatched evidence.

## Future real verification

Record one `VerificationCheck` per performed check against a real,
inspected source (India Code is the first planned source to
investigate for the Income-tax Act, 1961 — not yet acquired), with
verifier identity, timestamp, observations, and limitations filled
from actual evidence. Never manufacture such records in tests.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_source_verification.py
```
