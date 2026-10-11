# TaxGuard Acquisition Pilot, Phase 30 (Workstreams 3 + 4)

Status: bounded pilot record. No artifact was stored, replaced, or
deleted; no receipt, manifest, or record was written. All probes were
read-only (single GETs through the existing `AcquireConfig` policy;
hash comparisons in memory). Both held sources remain `unverified`.

## 1. Pilot performed (2026-10-11, live probes)

Repeat-acquisition stability check of the exact held bitstreams:

| Bitstream | HTTP | Bytes served | Redirects | SHA-256 vs receipt |
|---|---|---|---|---|
| `0c9617d8-…/content` (First Schedule) | 200 | 232,490 | 0 | match (`86c3575a…87de09`) |
| `3e358de7-…/content` (Second Schedule) | 200 | 498,597 | 0 | match (`f33737ee…18a482`) |

Repeated acquisition is deterministic: same endpoints serve
byte-identical content with identical digests. Nothing needed
storing (bytes already held and verified), so nothing was written —
repeat output confirmed in memory only.

## 2. New-item acquisition: stopped with reasons

Candidate follow-on items (further schedules) were probed at the
metadata level only. The DSpace search surface returns mixed
notification items (e.g. "Notification of Sections 55 to 58, Second
Schedule, Third Schedule and Fourth Schedule",
`4d2dfd8d-8c0e-44f4-b749-2b6b583efc6e`) rather than clean
per-schedule fragments, so a candidate's scope identity, bitstream
identity, and version/currency cannot be resolved safely from
available records. Per the stop rule, acquisition of new items is
stopped: expanding the corpus now would add unreviewed surface with
zero scope benefit (Q7+Q4 needs no new materials; anything new would
land `unverified` under the Phase 28 contract anyway).

## 3. Provenance and version controls (integration, no new infra)

- Pilot observations integrate with the existing pipeline only:
  `fetch_artifact` → hash compare → stop. No ingestion, chunking,
  manifest, or retrieval changes were needed or made.
- Provenance chain source → document → passage → assessment stays
  intact and is pinned by `tests/test_pilot_controls.py`: real-corpus
  passages resolve to their documents and sources by exact id;
  the absent assessment link is named explicitly (`unresolved`
  contains `"assessment"`) rather than fabricated, and
  `evidence_ref_for` defaults to `trust="unverified"`.
- Version/currency representation is unchanged from
  `authoritative-corpus-foundation.md` §4: publication unknown,
  effective periods unestablished, fragment-only completeness, stale
  size metadata preserved verbatim. No defaults were invented for
  missing fields.
- No embeddings, vector stores, rerankers, services, or dependencies
  were added: the BM25 baseline plus the two-artifact benchmark
  (MRR=1.000, lexical mechanics only) demonstrate no measured need.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_pilot_controls.py
```

```powershell
C:\Users\shubodh\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```
