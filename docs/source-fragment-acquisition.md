# TaxGuard Fragment Acquisition Pilot (Phase 12)

Status: **successful pilot** — exactly one official fragment acquired
and preserved. This supplements, and does not overwrite,
`docs/source-acquisition-record.md`, which records that no Act-level
artifact was acquired in Phase 11.

## Selected fragment and exact scope

- Fragment: **THE FIRST SCHEDULE** of The Income-tax Act, 1961.
- Parent Act (per India Code record): The Income-tax Act, 1961, Act 43
  of 1961; Ministry of Finance, Department of Revenue; CENTRAL;
  collection SCHEDULE, order number 1, act year 1961.
- Source record: `india-code-it-act-1961-first-schedule`, title exactly
  `THE FIRST SCHEDULE`, `source_type` schedule, jurisdiction IN,
  verification `unverified`. The record note names the parent Act and
  states the fragment is not the complete Act.
- Scope boundary: this artifact covers the First Schedule text only as
  served. Neighboring provisions, definitions, exceptions, provisos,
  other schedules, sections, and amendments are not included and were
  not checked. Nothing here claims completeness for any legal question.

## Official record and artifact URLs (established)

- Record (human-facing, verified HTTP 200):
  `https://indiacode.gov.in/entities/publication/13868f5c-6f46-47ad-b176-3cdcfc1f94c5`
- Record metadata (API item `13868f5c-…`): title, act name/number/year,
  ministry/department, schedule identifiers, accession stamp
  2026-07-10 (recorded, not verified as publication fact).
- Artifact (ORIGINAL bundle file `sch_i_196143.pdf`):
  `https://indiacode.gov.in/server/api/core/bitstreams/0c9617d8-abe3-4691-9a20-a5b1b595b860/content`
- Origin evidence: `indiacode.gov.in` (GOV.IN, TLS) served the bytes;
  official status beyond that is not independently established.

## Observed acquisition details

- Timestamp: 2026-10-09T08:33:11+00:00 (environment clock at fetch).
- HTTP 200, no redirects, content type `application/pdf;charset=UTF-8`.
- Byte size: **232,490** (item metadata lists 227,352 — stale; the
  observed value governs).
- SHA-256:
  `86c3575a49be2d8b77a571556a8ea43e67aae40e8e7d709d2c2bbe87de09`
- Stored at
  `corpus/india-code/income-tax-act-1961-first-schedule/sch_i_196143.pdf`
  via atomic write; hash recomputed from stored bytes and matched.
- Existing `acquisition.py` contracts sufficed unchanged (default
  20 MB / 30 s / 3-redirect / `.gov.in` policy).

## Corpus contents

- `sch_i_196143.pdf` — original artifact, unmodified.
- `source.json` — the registered source record.
- `india-code-it-act-1961-first-schedule-86c3575a49be2d8b77a571556a8ea43e.receipt.json`
  — provenance receipt (`parsed: false`, `document_id` empty,
  `verification: unverified`, five explicit limitations).
- `verification.json` — assessment plus the two performed checks.

## Verification checks performed

- Integrity `passed`: SHA-256 recomputed from the stored 232,490
  bytes matches the receipt (content identity only).
- Origin `inconclusive`: HTTPS fetch from the official host observed;
  official status not independently established (never auto-passed).
- Version, completeness, applicability: `not_checked`, recorded as such.
- Assessment under policy requiring origin+integrity: `incomplete`
  (`uncertain:origin`, `satisfied:integrity`). Source stays
  `unverified`. No `passed` check exists without measured evidence.

## Ingestion

The artifact is PDF: existing `ingest_file()` rejects it
(`unsupported file format`), and the original is preserved byte for
byte. Storing the PDF is not ingestion — no `DocumentRecord` was
created. No PDF parser, OCR, or dependency was added.

## Phase 13 parse result (appended; acquisition facts above unchanged)

The receipt and original bytes were re-verified from file (size
232,490 and SHA-256 both match the receipt; values compared in code,
never retyped). PDF support was added (`pypdf`, declared dependency)
and the artifact ingested through the public `ingest_file()`:

- 2 pages, not encrypted, every page yielded text.
- 5,223 extracted characters, status `ok`, zero U+FFFD replacement
  characters; fragment title wording ("FIRST SCHEDULE") present —
  structurally plausible for the stated scope.
- Document ID
  `india-code-it-act-1961-first-schedule:86c3575a49be2d8b77a571556a8ea43e`,
  content hash identical to the receipt (original-byte identity
  preserved), parser `taxguard-sources-v1:pdf:pypdf-6.11.0`.
- Normalized record persisted as `document.json` beside the artifact.
  The acquisition receipt is untouched (it records acquisition-time
  state: `parsed: false`). Source remains `unverified`; version,
  completeness, and applicability remain unestablished. Extraction
  proves nothing about legal correctness or currency.

## Unknown / unresolved

Consolidated-through date, version identity, completeness of the
schedule text, authenticity beyond observed provenance, currency, and
legal applicability to any tax period. The recorded
`act_repealed: false` metadata is an unevaluated record claim, not a
verified fact.

## Why this is not a corpus

One schedule of one Act: no sections, no other schedules, no
amendments, rules, circulars, or notifications. No manifest exists or
was created; no plan entries were modified.

## Phase 14 re-verification (appended; history above unchanged)

- Bytes recomputed from disk: 232,490 bytes, SHA-256 matches the
  receipt. Original PDF, receipt, source record, and prior
  verification file untouched; results written to new file
  `reverification-2026-10-09.json`.
- Live India Code item metadata re-fetched (opt-in, HTTP 200, ~5 KB):
  title `THE FIRST SCHEDULE`, parent Act `The Income-tax Act, 1961`
  → record-identity check `passed` (network-observed).
- New assessment (origin+integrity+record_identity policy):
  `incomplete` (`missing:origin`, `satisfied:integrity`,
  `satisfied:record_identity`). Combined with the Phase 12 origin
  record (`inconclusive`), the source stays `unverified`.
- Discrepancies that remain open: item metadata lists 227,352 bytes
  vs 232,490 observed (stale metadata, unresolved); recorded
`act_repealed: false` is unevaluated metadata, not a fact; no
publication or consolidated-through date in the metadata; version,
completeness, and applicability still `not_checked`.

## Reproduce / audit

Re-fetch the artifact URL with the default `AcquireConfig`, compare
SHA-256, and re-run `verify_stored_artifact`. Single read-only probes
only; no crawling was performed.
