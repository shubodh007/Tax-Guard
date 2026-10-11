# TaxGuard Source-Origin Evidence Contract (Phase 28)

Status: specification only. No trust anchor exists, no origin check
passes, no source was acquired, verified, or promoted, and the real
corpus (`corpus/manifest.json`, both PDFs, receipts, `document.json`
files) is unchanged and remains `unverified`. Anything labelled
`synthetic-` is a test fixture with no legal force. This document
closes the Phase 27 specification gap: it states what origin evidence
*would* have to be, why nothing held today qualifies, and how the
system stays fail-closed until a defensible anchor is defined and
reviewed. It introduces no verifier implementation, key, signature,
registry, or passing origin check.

## 1. Trust model: eight separate properties

One property never proves another. Each is established only by its
own evidence under its own rule:

| Property | Question answered | Established by |
|---|---|---|
| Origin / authenticity | Did these bytes come from the claimed publisher or an independently established authoritative source? | Only an accepted origin record per §4 (none exists today) |
| Integrity | Do the stored bytes match a recorded digest? | Passed `integrity` check from locally recomputed SHA-256 + size match (`reconcile`, `integrity_check`) |
| Record identity | Is this the intended publication / source record (right title, identifiers)? | Passed `record_identity` check from title comparison against supplied official metadata (`reconcile`) |
| Version / currency | Which publication, amendment, or consolidation does this represent, and is it current for the claimed period? | Version evidence (not produced by current machinery; always `not_checked`) |
| Completeness | Are the required document and relevant parts present? | Completeness evidence (always `not_checked`) |
| Applicability | Does this material apply to the user's question and period? | Legal analysis (always `not_checked` here; opaque period labels per `applicability-contract.md`) |
| Assessment integrity | Is this the same assessment that was bound at entry creation? | `assessment_digest` equality in `verify_entry_binding` |
| Policy authorization | Does the evidence satisfy a separately trusted policy? | `authorize_status()` under a trusted policy object (never the assessment file) |

In particular: integrity ≠ authenticity; title match ≠
authenticity; origin observation ≠ authenticity; assessment digest
match ≠ origin; policy id/version match ≠ sufficiency (reason codes
are self-reported strings).

## 2. Trust boundaries

- **Inside:** application code (`source_verification.py`,
  `manifest.py`, `acquisition.py`, `main.py` load path), the pinned
  `canonical_policy()` value, locally recomputed hashes/sizes.
- **Outside:** publishers and hosts (including `indiacode.gov.in`),
  network responses, official metadata, clocks, any human claim not
  yet recorded under review.
- **Boundary input (untrusted until gated):** every serialized
  artifact — `verification.json` (assessments + checks), `source.json`,
  `document.json`, `manifest.json`, receipts. `Assessment.from_dict` /
  `VerificationCheck.from_dict` validate structure only; whether a
  label is backed is decided by `authorize_status`, never by
  deserialization. The assessment file never supplies the policy:
  `verify_entry_binding` and `_load_corpus` take it from
  `canonical_policy()` pinned in code.

## 3. Evidence types: producer, tamper, detection, limits

For each type: who/what creates it; inside or outside the boundary;
how it can be tampered with; how the verifier detects or limits
tampering; what it does and does not prove.

- **Local JSON files** (`verification.json`, receipts, manifests).
  Created by whoever wrote the file (test fixture author, operator,
  or attacker with write access) — outside until gated. Tamper:
  arbitrary fabrication of `passed` outcomes, reason codes, digests.
  Detection: schema validation + `assessment_digest` recomputation +
  `authorize_status` under the trusted policy + load-time fail-closed
  503. Proves: nothing by itself; only gated assessments authorize.
- **URLs and origin strings** (`SourceRecord.origin`, record URLs).
  Created by the registrant — outside. Tamper: any string can be
  typed, including `https://indiacode.gov.in/`. Detection: policy
  never inspects them (`assess` reads only supplied check records;
  `test_gov_looking_origin_never_auto_verifies`). Proves: claimed
  location only; never authenticity, currency, or completeness.
- **HTTP responses and acquisition logs** (status, redirect chain,
  content type, timestamps). Created by the serving host + local
  clock — outside. Tamper: host serves substitute bytes; logs can be
  edited; clock can be wrong. Detection: size/hash bounds, redirect
  scope policy, atomic-write + `verify_stored_artifact`; logs stay
  observations, never verdicts. Proves: what was observed on the
  wire; never official status.
- **TLS (HTTPS, `.gov.in` host).** Created by the network endpoint
  and CA ecosystem — outside. Tamper: compromised/misleading host,
  stale or partial content over a valid session. Detection: none in
  this system beyond recording the endpoint; `origin_observation()`
  is `inconclusive` by construction. Proves: a TLS session with the
  named host at fetch time; never authenticity, completeness, or
  currency.
- **Hashes and digests** (SHA-256, `assessment_digest`,
  `snapshot_id`). Created by whoever hashes whatever bytes they hold
  — outside until recomputed locally. Tamper: hash of substitute
  bytes; copied digest strings. Detection: local recomputation from
  stored bytes (`verify_stored_artifact`, `reconcile`,
  `assessment_digest`). Proves: content identity only; never
  authenticity, and a digest of a self-authored assessment is not
  independent evidence.
- **Official-domain metadata** (India Code titles, act labels, sizes
  via `fetch_json_metadata` / `OfficialRecord`). Created by the
  remote metadata endpoint — outside. Tamper: stale fields (observed:
  227,352 vs 232,490 bytes), unevaluated claims (`act_repealed`).
  Detection: title comparison drives at most `record_identity`;
  size discrepancies are reported verbatim without changing the
  verdict; version/completeness/applicability stay `not_checked`.
  Proves: at most identity corroboration; never authenticity,
  currency, completeness, or applicability.
- **Acquisition receipts.** Created by `acquisition.py` from observed
  values — inside machinery, outside trust (observations, not
  verdicts). Tamper: receipt file edited after writing.
  Detection: no-silent-overwrite write policy + hash re-verification;
  `verification` locked to `unverified`; non-empty `limitations`
  required. Proves: provenance observations; never verification.
- **Human review records** (future `verifier`, `checked_at`,
  signed-off observations). No accepted producer, format, or roster
  exists today — see §7. Any `verifier` string in a current file is
  an unsupplied/legacy label, not authorization. Proves: nothing
  until a review responsibility is defined, staffed, and evidenced.

Conclusion of this section: none of the above, alone or combined as
currently produced, is sufficient for origin. The system therefore
remains fail-closed per §5.

## 4. Acceptance and rejection rules (origin)

- **Acceptance:** no origin evidence type is currently accepted.
  There is deliberately no rule of the form "X suffices for origin"
  in code, config, or documentation. A future acceptance rule must
  name its producer, trust anchor, tamper response, and reviewer
  (see §7) and be approved before any code enforces it.
- **Rejection (any one rejects):** `origin` check with outcome other
  than `passed`; `passed` origin record produced inside the test
  fixture, the assessment file, or any machinery without a defined
  anchor (fabricated `origin: passed`); origin satisfaction inferred
  from URL, TLS, hash, digest, title match, or log presence;
  assessment whose policy id/version/required-checks differ from the
  trusted policy; reason codes naming `satisfied:origin` without a
  backing accepted record; missing, unreadable, or malformed
  `verification.json`; stale `assessment_digest`; foreign
  `source_id`; duplicate check ids.
- **Enforcement point:** `authorize_status()` rejects `verified`
  whenever `origin` is required (which the canonical policy always
  is) with `origin satisfaction cannot be established from supplied
  records`. `entry_for_verified()` and `verify_entry_binding()`
  propagate this as `ManifestError("verification not authorized:
  ...")`; `_load_corpus` converts any failure into a generic 503.
  This restriction must not be removed or weakened while no anchor
  exists.

## 5. Status transitions and fail-closed behavior

- `unverified` is the default and needs no evidence; it is always
  authorized and always servable.
- `unverified → verified`: currently impossible through any
  accepted path (no accepted origin evidence exists). Any attempt
  fails closed: build/load refusal or generic 503 (`corpus
  unavailable`) on `/ask` and `/ready`, with the detail logged
  server-side only.
- `verified → unverified`: any stale digest, changed assessment,
  missing sibling file, or policy mismatch re-fails the entry at
  load; the whole corpus load fails rather than serving weaker
  assurance.
- `→ disputed`: requires a failed/conflicting assessment under the
  trusted policy; origin plays no authorizing role there and this
  contract adds none.
- Corrupt, absent, or bypass-constructed entries (direct
  `ManifestEntry(...)`, hand-written manifests) never reach serve:
  `build_passages` identity/count checks plus `verify_entry_binding`
  reject them first.

## 6. Retention and audit requirements

- Preserve original artifacts byte-for-byte; never modify PDFs,
  receipts, or committed manifests to satisfy a check.
- Preserve every `VerificationCheck` with `description`,
  `observation`, `limitations`, `observation_source`, and (when
  measured) `content_hash`; observations stay pointers (≤4000
  chars), never document dumps or secrets.
- Preserve the binding: entry `assessment_digest` + assessment
  `check_ids` + trusted policy id/version, so any change
  invalidates the reference.
- Keep failures auditable: refused builds raise with the entry
  identity; refused loads log server-side and serve only the generic
  503 body.
- Test fixtures proving refusal must be synthetic, temporary
  (`tmp_path`), and must never touch the real corpus.

## 7. Staged path toward a future independently trusted origin verifier

- **Stage 0 (current, enforced):** no anchor; canonical policy
  requires origin; every `verified` promotion fails closed; corpus
  stays unverified. Done — this contract pins it.
- **Stage 1 (next, documentation + review only):** define the anchor
  candidate (e.g. gazette confirmation procedure, registry, or
  signature scheme — none chosen here), its producer and roster,
  its tamper model, the exact `VerificationCheck` shape it yields
  (including `observation_source` and reviewer identity), and the
  acceptance rule to be enforced. Requires product + legal approval
  per `initial-coverage-scope.md` §6.3. No code yet.
- **Stage 2 (later, minimal code):** implement only the
  Stage-1-approved record producer and its `authorize_status`
  acceptance branch, with negative controls for each §4 rejection
  rule. No retrieval, corpus, or answer-path changes bundled in.
- **Stage 3 (last):** re-verify the two held fragments (or their
  approved successors) through Stages 1–2 with real evidence; only
  then may a label change, with receipts and review records
  committed.

No stage invents keys, signatures, registries, or successful checks
in advance.

## 8. Non-goals and unresolved decisions

- Non-goals: acquiring or inventing legal-source evidence; expanding
  the corpus; redesigning retrieval; frontend work; choosing 1961 vs
  2025 coverage; defining version/completeness bars (owned by
  `initial-coverage-scope.md` §6.3–6.4 + legal review); any
  origin-verifier implementation.
- Unresolved: what counts as authenticity/currency evidence (§6.3
  decision pending); whether India Code fragments or gazette texts
  are canonical targets; whether the two held schedules can ever
  reach `verified`; the authoritative transition instruments;
  reviewer roster and rotation. All await the approvals named in
  `initial-coverage-scope.md` §6. This contract must not be read as
  resolving any of them.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_origin_evidence.py and tests/test_evidence_binding.py
```

```powershell
C:\Users\shubodh\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```
