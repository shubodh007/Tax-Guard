# TaxGuard Question API Contract (Phase 22)

Status: minimal vertical slice. One endpoint answers lexical questions
from the manifest-pinned corpus. No LLM, verification outcomes, or
claim judgments are involved: `retrieved` below means "passages
lexically matched", never "claim verified" or "legally supported".

## Request

`POST /ask` with JSON body:
- `question`: required string, 1–500 characters after trimming
  (blank → 422).
- `limit`: optional integer 1–10, default 3 (anything else → 422).

## Response

- `request_id`: opaque per-request identifier (16 hex chars from the
  standard library). Reproducibility comes from `snapshot_id`, never
  from the question text.
- `status`: `retrieved` (passages lexically matched) or `abstained`
  (none found). There is intentionally no `supported` or `verified`
  status here: a keyword match does not prove a passage answers the
  question or supports a legal claim, and nothing in this path verifies
  claims.
- `reason_code`: `lexical_match` or `no_matching_evidence`.
- `answer`: deterministic template text naming match counts, the top
  passage, its pages, and source verification state — or a plain
  abstention statement. No generated legal conclusions.
- `passages[]`: `passage_id`, `document_id`, `source_id`,
  `chunk_index`, full `text`, `char_start`/`char_end`,
  `page_start`/`page_end`, `score`, `rank`, `content_hash`,
  `source_title` ("" when the source companion is absent),
  `source_verification` (manifest snapshot, always present),
  `unresolved` (links the resolver could not establish, e.g.
  assessment — claim verification does not run here).
- `snapshot_id`: the manifest snapshot the answer came from.
- `limitations`: fixed notice — two unverified fragments, lexical
  matching only, informational, not professional tax advice.

Corpus missing or invalid → `503 {"detail": "corpus unavailable"}`;
health and inspection endpoints are unaffected. The 503 body is
intentionally generic: the cause is logged server-side, never echoed
with filesystem paths or exception text.

Readiness: `GET /ready` → `200 {"status":"ready","snapshot_id":"…"}` when
the manifest loaded, else `503 {"detail": "corpus unavailable"}`.
Liveness stays on `GET /health`, which never touches the corpus.

## Corpus lifecycle

`create_app()` validates `corpus/manifest.json` eagerly via
`load_manifest_file()` + `build_passages()` and retains passages,
documents, source records (best-effort `source.json` companions), and
entry snapshots in app state. Failures are stored and served as 503 —
never a silent empty corpus. Each `create_app()` call is isolated.
Source `source.json` companions are optional; verification display
falls back to the manifest snapshot.
A non-`unverified` entry additionally requires its authorizing
assessment in the sibling `verification.json` beside its artifact
(digest-matched to the entry and satisfying the code-pinned canonical
policy, under which file-supplied origin satisfaction is never
accepted); absent, stale, or mismatched evidence fails the load with
the same generic 503, so `/ask` never displays a stronger assurance
than the evidence supports.

The manifest is never assumed to exist: on a clean checkout (or the
deploy host) regenerate it from the committed `document.json` records
— no PDFs or network needed — then start the app:

```powershell
python scripts/build_manifest.py
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

The script scans `corpus/**/document.json`, derives one entry per
record (artifact path from the record's own filename), and writes a
validated `corpus/manifest.json`. Regeneration is deterministic: the
same records always yield the same snapshot ID.

## Deployment notes

- Python ≥3.11 (verified on 3.12.10); `pip install -e .`;
  `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`.
- Commit the small corpus JSON records (source/receipt/verification/
  document/manifest); PDFs stay git-ignored. The builder needs only
  `document.json` files, so the service runs without binaries.
- `render.yaml` declares the Render service with no CORS value shipped:
  `TAXGUARD_CORS_ORIGINS` is `sync: false`, so the operator must set the
  real https frontend origin in the Render dashboard before exposing the
  service. Until then production allows no origins (never localhost,
  wildcards, or placeholder domains); development defaults to localhost.
- No deployment has been performed from here; see README for the
  remaining manual steps.

Run with `pytest -q` (includes `tests/test_ask.py`).
