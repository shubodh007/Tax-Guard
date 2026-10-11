# TaxGuard Authoritative Corpus Foundation (Phase 29)

Status: milestone record, not an acquisition. No artifact was
downloaded, re-fetched, modified, or promoted in this phase. The
corpus is exactly the two previously acquired schedule fragments;
both sources remain `unverified` per the Phase 28 contract. No
general Indian income-tax coverage is claimed. Anything labelled
`synthetic-` is a test fixture with no legal force.

## 1. Supported scope and required source inventory

The narrowest defensible release for the held corpus, per
`initial-coverage-scope.md` Q7+Q4:

- **Supported: Q7 trace/answer inspection.** Needs no legal evidence;
  already implemented via the read-only registry API.
- **Supported with explicit limits: Q4 schedule reading as lexical
  retrieval only.** `POST /ask` may return `retrieved` passages from
  the two fragments with the standing unverified-source limitation
  notice. Such passages must never be cited as legal authority and no
  substantive legal conclusion may rest on them.
- **Unsupported: everything substantive.** Q1 liability, Q2 regime
  choice, Q3 deduction availability, Q5 applicable law, Q6 provision
  change, and any calculation: all abstain or clarify, because every
  required input is missing or unverified (§1.3).

### 1.1 Held inventory (observed, not asserted)

| | First Schedule fragment | Second Schedule fragment |
|---|---|---|
| `source_id` | `india-code-it-act-1961-first-schedule` | `india-code-it-act-1961-second-schedule` |
| Title | `THE FIRST SCHEDULE` | `THE SECOND SCHEDULE.` |
| Publisher | India Code (Ministry of Finance, Department of Revenue) | same |
| Origin (claimed location only) | `https://indiacode.gov.in/` | same |
| Record item | `13868f5c-6f46-47ad-b176-3cdcfc1f94c5` | `ebfba9d1-97da-4f02-873b-d4dcd365b0e9` |
| Artifact | `sch_i_196143.pdf`, 232,490 bytes | `sch_ii_196143.pdf`, 498,597 bytes |
| SHA-256 | `86c3575a…87de09` (full values in receipts) | `f33737ee…18a482` |
| Publication identity | none recorded (`publication: ""`) | none recorded (`publication: ""`) |
| Publication / effective date | unknown; never inferred from filename or fetch date | same |
| Amendment relationships | unestablished | same |
| Completeness expectation | fragment only (note names exclusions) | same |
| Integrity | passed (recomputed hash + size match) | passed |
| Origin | inconclusive (endpoint observed, status unestablished) | same |
| Record identity | no check recorded (older shape) | passed (title corroboration only) |
| Version / completeness / applicability | `not_checked` | `not_checked` |
| Assessment | `incomplete` | `incomplete` |
| Source label | `unverified` | `unverified` |
| Passages (defaults) | 7 | 84 |

Manifest: 2 entries, 91 passages, snapshot
`77fdc5329096755a58d09d6c5e78d52a55685021969e3dd019116f4bb37c4d31`.

### 1.2 Missing source families (nothing below exists in-repo)

Charging provisions, rate schedules, surcharge/cess provisions,
deduction provisions, conduct/assessment rules, notifications,
circulars, Finance Act amendments, commencement/repeal/savings and
1961↔2025 transition instruments, versioned historical texts. Any
question needing any of these abstains.

## 2. Controlled acquisition workflow (existing controls, unchanged)

No new acquisition was performed; the workflow below is the already
implemented and tested machinery, restated as the standing procedure:

1. Fetch one explicit URL under `AcquireConfig` (`.gov.in` scope,
   20 MB / 30 s / 3-redirect bounds) via `fetch_artifact()`.
2. Preserve source URL, publication/record identifier, retrieval
   time, digest, byte length, outcome, and available
   publication/version metadata in an `AcquisitionReceipt`
   (`verification` locked to `unverified`, non-empty `limitations`
   required).
3. Store bytes atomically via `store_artifact()` (identical bytes
   idempotent; different bytes under the same name raise; traversal
   guard); verify via `verify_stored_artifact()`.
4. Write the receipt once via `write_receipt_file()` (identical
   content idempotent; differing content raises — receipts are
   immutable once created).
5. Record checks honestly: `integrity_check()` only from measured
   values; `origin_observation()` stays `inconclusive`; version,
   completeness, and applicability stay `not_checked` unless
   separately evidenced.
6. Re-running acquisition is deterministic and safe: same bytes
   return the same paths; changed bytes, conflicting records,
   missing artifacts, and metadata inconsistencies raise instead of
   overwriting (pinned by `tests/test_acquisition.py`,
   `tests/test_corpus_foundation.py`).

URL presence, official-looking domains, HTTPS, and hash agreement
are observations, never independent proof of authenticity (Phase 28
§3).

## 3. Phase 28 enforcement (unchanged)

`authorize_status()` and `canonical_policy()` (`origin`,
`integrity`, `record_identity`) are untouched. No trust anchor,
signature, registry, or passing origin observation was invented, so
every `verified` promotion still fails closed and both held sources
stay `unverified`. Unverified passages are served only with the
`retrieved`-with-limits framing from `question-api-contract.md`;
`/ask` never displays a stronger assurance than the evidence
supports, and load failures serve the generic 503. No fabricated
default replaces missing evidence.

## 4. Version and completeness metadata (explicit limits)

Represented exactly as the evidence supports, per source:

- `publication`: `""` (unknown) for both — recorded, never guessed.
- Effective period: none established; no date is inferred from
  filenames (`sch_i_196143.pdf`), fetch timestamps
  (`acquired_at`), or accession stamps.
- Amendment/replacement: no relationships recorded.
- Completeness: fragment-only by the source notes; the manifest is
  an inventory of held records, never a completeness claim.
- Open discrepancies preserved verbatim: item metadata sizes
  (227,352 / 440,870) vs observed bytes (232,490 / 498,597);
  unevaluated `act_repealed` metadata; no consolidated-through date.
- Origin, integrity, record identity, version, completeness, and
  applicability are kept separate in every assessment; a satisfied
  category never implies another.

## 5. Ingestion and retrieval integration (reuse, no new infra)

Reused unchanged: `ingest_file()` (pypdf text-based extraction,
original-byte hashing), deterministic `chunk_document()` (defaults
1000/100, `taxguard-chunks-v1`), `build_manifest()` /
`build_passages()` (snapshot-pinned, drift-checked),
`retrieve_passages()` BM25 baseline, and the two-artifact benchmark
(`two-schedule-bench-v1`: 91 passages, 6 queries, 5 eligible,
MRR reported at test time — lexical mechanics only, not legal
quality). No embeddings, vector store, reranking, caching, or
performance tuning was added: the existing benchmark demonstrates
no such need, and no bottleneck was measured.

## 6. Deferred work

Scope/period approval and the §6.3 authenticity-bar decision
(`initial-coverage-scope.md`); any new acquisition; a Stage-1 origin
anchor definition; version/completeness bars; transition
instruments; frontend, calculation, and answer-generation work. None
is started here.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_corpus_foundation.py
```

```powershell
C:\Users\shubodh\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```
