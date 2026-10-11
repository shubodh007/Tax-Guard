# TaxGuard Source-Acquisition Record (Phase 11)

Status: acquisition machinery implemented and tested; the first real
acquisition is **blocked** — no artifact was downloaded, no receipt was
issued, no manifest was created or modified, and no source was marked
acquired or verified. This document records the blocked attempt with
its evidence.

## Target and why

Intended record: the **Income-tax Act, 1961** (Act 43 of 1961, enacted
13-Sep-1961 per India Code listings), via **India Code** as the first
official source to investigate. Intended source metadata (recorded,
not verified):

- `source_id`: `india-code-income-tax-act-1961`
- title: `The Income-tax Act, 1961` (as stated by India Code listings)
- publisher: India Code / National Informatics Centre (site footer:
  content by Government ministries; site by NIC)
- origin: `https://indiacode.gov.in/`
- `source_type`: `act`, jurisdiction `IN`, verification `unverified`

No acquisition plan or manifest exists in this repository; none was
created (no demonstrated need — a single receipt file per artifact is
the record, and there is no artifact yet).

## Pre-acquisition investigation (observed, 2026-10-09)

1. India Code search confirms the Act listing: "The Income-tax Act,
   1961", Act Number 43, Enactment Date 13-Sep-1961, former handle
   `123456789/2435`.
2. The old handle is dead: item pages return "Invalid URL or
   Argument(s)"; the old API times out; handle resolution on the new
   system returns 404. India Code announced migration from
   `www.indiacode.nic.in` to `indiacode.gov.in`.
3. The new site (`indiacode.gov.in`, live, DSpace 7 REST API) holds Act
   43 of 1961 only as fragments: act grouping
   `AC_CEN_2_2_00039_196143_1524045010860` contains exactly the 14
   schedules; sections/schedules are individual items (some with PDF
   bitstreams, e.g. First Schedule `sch_i_196143.pdf`). A 100-hit title
   scan surfaced no single Act-level downloadable item.
4. A search-cached Income Tax Department PDF URL
   (`incometaxindia.gov.in/documents/income-tax-act-1961-...-2024.pdf`)
   302-redirects to `www.` and returns **404** — stale, not acquirable.
5. The live `incometaxindia.gov.in` site returns **403** to programmatic
   access (bot mitigation). Bypassing it would violate the acquisition
   policy, so no further attempts were made there.

## Blocker and decision

No single Act-level artifact for the target record can be established
from an official endpoint: India Code serves fragments only, the
departmental PDF link is dead, and the departmental site blocks
programmatic access. Acquiring one schedule PDF and presenting it as
the Act would misrepresent completeness. Per policy, the acquisition
operation is **stopped and reported as blocked** — preferable to
fabricated success. No file was created under any corpus directory
(none exists; the intended layout for a future success is
`corpus/india-code/<source-id>/`, ignored by git).

## Machinery implemented (`backend/app/acquisition.py`, stdlib only)

- `AcquireConfig{max_bytes=20MB, timeout_seconds=30, max_redirects=3,
  allowed_host_suffixes=(".gov.in",), user_agent}` — origin scope,
  size, timeout, and redirect bounds.
- `fetch_artifact(url, config)` → `(AcquiredArtifact, bytes)` with
  recorded source/final URL, redirect chain, HTTP status, content
  type, size, SHA-256, and environment timestamp. Requires HTTPS,
  rejects credentials, malformed URLs, out-of-scope origins, prohibited
  DNS destinations (loopback/private/link-local/reserved), timeouts,
  HTTP errors, HTTPS-to-HTTP downgrade redirects, redirect
  violations/loops/off-scope targets, oversize and empty bodies. Every
  redirect and the final URL receive the same validation before content
  is accepted.
- `store_artifact` (atomic temp+rename; identical bytes idempotent,
  different bytes raise; filename traversal guard),
  `verify_stored_artifact` (hash-mismatch detection),
  `AcquisitionReceipt` (observed-values-only, `verification` locked to
  `unverified`, non-empty `limitations` required, `parsed`/`document_id`
  explicit) with strict `to_dict`/`from_dict` and `write_receipt_file`
  under the same no-silent-overwrite policy.
- `integrity_check()` builds a `passed` integrity record only from
  measured values; `origin_observation()` is `inconclusive` by
  construction — retrieval never auto-verifies.

## Verification state

No `passed` checks exist for any real source. Had acquisition
succeeded, the honest records would have been: integrity `passed`
(recomputed hash), origin `inconclusive` (host observed, status
unestablished), version/completeness/applicability `not_checked` —
an `incomplete` assessment, source remaining `unverified`. No
applicability to any tax period is established; the 1961 Act must
never be assumed to cover a period, substitute for the 2025 Act, or
constitute a complete corpus.

## Tests and running

`tests/test_acquisition.py` (9 tests, local 127.0.0.1 server only):
receipt-from-observed-metadata, hash-mismatch detection, size limits,
timeout/404/500, redirect chain/loop/off-scope policy, origin+scheme
rejection, no-silent-overwrite, unknown-preservation with
verified-lock, unsupported-format preservation via existing ingestion.

```powershell
pytest -q   # full suite, incl. tests/test_acquisition.py
```

## Audit

Probes above were read-only metadata/API checks plus one dead-URL
fetch returning 404 — no crawling, no bypass attempts, no downloads
retained. Reproduce via the URLs and API paths named here.
