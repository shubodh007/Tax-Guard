# TaxGuard Second-Schedule Acquisition (Phase 19)

Status: **successful pilot** — exactly one additional official fragment
acquired and preserved. Supplements `docs/source-fragment-acquisition.md`
(First Schedule); no earlier record was altered.

## Why this artifact

Second Schedule of The Income-tax Act, 1961: same official source,
collection, and pipeline as the proven First Schedule acquisition
(predictable `sch_ii_196143.pdf` artifact pattern); directly relevant
as the neighboring schedule of the same Act; small (~0.5 MB);
fragment scope stays honest. Preferred over a large download whose
identity could not be established, and over re-retrieving the
Act-level record that Phase 11 showed does not exist as one artifact.

## Official record and artifact URLs (established)

- Record: `https://indiacode.gov.in/entities/publication/ebfba9d1-97da-4f02-873b-d4dcd365b0e9`
  (title `THE SECOND SCHEDULE.`, parent Act `The Income-tax Act, 1961`
  No. 43, Ministry of Finance / Department of Revenue, CENTRAL,
  order number 2, act year 1961; same act group
  `AC_CEN_2_2_00039_196143_…` as the First Schedule record).
- Artifact (ORIGINAL bundle file): `https://indiacode.gov.in/server/api/core/bitstreams/3e358de7-c01b-405b-a3ba-34f6408d98c4/content`
- Origin evidence: GOV.IN TLS host served the bytes; official status
  beyond that is not independently established.

## Observed acquisition details

- Timestamp: 2026-10-09T10:28:34+00:00 (environment clock at fetch).
- HTTP 200, no redirects, content type `application/pdf;charset=UTF-8`.
- Byte size: **498,597** (item metadata lists 440,870 — stale, kept as
  an open discrepancy; the observed value governs).
- SHA-256:
  `f33737ee2a0335887dd32ea73e5f1eb7fca23d213c89ac69cc28d6bd5c18a482`
- Stored at
  `corpus/india-code/income-tax-act-1961-second-schedule/sch_ii_196143.pdf`
  via existing atomic-write policy; hash recomputed from stored bytes
  and matched. Default fetch policy (20 MB / 30 s / 3 redirects /
  `.gov.in` scope); no code changes needed.

## Corpus contents

`sch_ii_196143.pdf` (unmodified), `source.json` (source
`india-code-it-act-1961-second-schedule`, title exactly
`THE SECOND SCHEDULE.`, verification `unverified`, fragment scope in
note), `*.receipt.json` (`parsed: false`, five explicit limitations),
`verification.json`, and — after successful parsing — `document.json`.

## Verification outcomes by category

- Integrity **passed**: recomputed hash/size match the receipt
  (content identity only); the parsed record carries the identical hash.
- Origin **inconclusive**: HTTPS endpoint observed; official status
  unestablished (never auto-passed).
- Record identity **passed**: live item metadata re-fetched (HTTP 200)
  shows title `THE SECOND SCHEDULE.` and parent Act
  `The Income-tax Act, 1961`, matching the source record; recorded in
  `reverification-2026-10-09.json` (identity corroboration only).
- Version, completeness, applicability: **not_checked**.
- Assessment: `incomplete` (`missing:origin`, `satisfied:integrity`,
  `satisfied:record_identity`); source stays `unverified`. No `passed`
  check lacks measured evidence.

## Parsing, chunking, smoke retrieval

- Ingested via existing pipeline: 19 pages, unencrypted,
  **67,418 characters**, parser `taxguard-sources-v1:pdf:pypdf-6.11.0`,
  doc ID `…:f33737ee2a0335887dd32ea73e5f1eb7`, hash identical to receipt.
- Chunked with defaults: **84 passages, 76–1000 chars**, all single-page
  (pages 1–19 all represented, no crossings).
- Smoke queries from extracted text: `schedule` → 3 hits (top c0081,
  p.19); `written down value` → 3 hits (top c0023, p.6); all offsets
  recover exactly. `depreciation` → 0 hits (genuine observation —
  possibly extraction word-splitting as seen in Phase 16; not
  investigated further here).

## Unknowns and non-claims

Consolidated-through date, version identity, schedule-text
completeness, authenticity beyond observed provenance, currency, and
applicability to any period. The 1961 and 2025 Acts remain distinct;
nothing here states which governs any taxpayer. Two fragments are not
a corpus and prove no retrieval quality.

## Reproduce / audit

Re-fetch the artifact URL with the default `AcquireConfig`, compare
SHA-256, and re-run `verify_stored_artifact`. Single read-only probes
only; no crawling was performed.
