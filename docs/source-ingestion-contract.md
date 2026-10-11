# TaxGuard Source & Ingestion Contract (Phase 7)

Status: registry + local ingestion foundation only. There is no legal
corpus, no verified source, no retrieval, no embeddings, no network
fetching, and no chunking. Anything labelled `synthetic-` is a test
fixture with no legal force. PDF text extraction (Phase 13) covers
ordinary text-based PDFs via pypdf; it is not OCR and proves nothing
about authenticity or legal correctness.

## Source records (`SourceRecord` in `backend/app/sources.py`)

Required: stable `source_id`, human-readable `title`. Optional (empty
means unknown, never guessed): `publisher`, `origin` (recorded URL or
origin text), `source_type`, `jurisdiction`, `publication` (recorded
publication/amendment metadata), `acquired_at` (recorded retrieval date),
`note` (provenance notes / explicit uncertainty).

Verification states: `unverified` (default, fail-closed), `verified`,
`disputed`. Any non-`unverified` state must carry a `note` stating its
basis. A government-domain URL never auto-verifies; a local file is
never automatically official; dates and filenames never imply
applicability; a record never claims corpus completeness.

## Registration (`SourceRegistry`)

`register(source)` / `get(source_id)` (exact id, `None` when absent).
Duplicate ids raise `SourceError` — no silent overwrite; malformed
records (blank ids/titles, unknown states, verified-without-note,
non-string fields) raise at construction. No acquisition manifest
exists in this repo and none was created or modified.

## Ingestion (`ingest_file(registry, source_id, path, *, format=None, max_bytes=5_000_000)`)

Local file I/O only: reads the supplied path, writes nothing, fetches
nothing, modifies no manifests. The source must be registered first
(unknown id → `IngestionError`).

- Formats: `txt` (explicit UTF-8 decode; `UnicodeDecodeError` → typed
  error, never silent replacement), `html` (stdlib `html.parser`:
  script/style content dropped, scripts never executed, block tags
  delimit paragraphs, entities decoded, tag soup parsed leniently),
  and `pdf` (text-based PDFs via pypdf — see below).
  Format comes from an explicit argument or `.txt`/`.html`/`.htm`/`.pdf`
  suffix; anything else (e.g. extensionless paths) is rejected.
- Normalization: CRLF→LF, surrounding whitespace stripped; HTML blocks
  joined with blank lines, inner whitespace collapsed. Deterministic.
- Integrity: SHA-256 over the actual input bytes; `document_id` is
  `{source_id}:{hash[:32]}` — filename is metadata only.
- `DocumentRecord`: document/source ids, filename, format, parser id
  (`taxguard-sources-v1:{format}`), hash, byte size, normalized text,
  extraction status (`ok`/`empty`), and a snapshot of the source's
  verification state at ingest time. JSON round-trips via
  `to_dict`/`from_dict` (hashes carried, never recomputed on load).
- Bounds & errors: missing/unreadable/non-file inputs, zero-byte files,
  oversize inputs (default 5 MB, per-call override), and bad `max_bytes`
  all raise `IngestionError` naming the file, never its contents. No
  managed corpus directory exists, so no traversal surface was added.

## PDF text extraction (Phase 13)

Stdlib parsing cannot read PDFs (object model, content streams,
font encodings), so the declared `pypdf>=4,<7` dependency (pure
Python, no native binaries; verified as pypdf 6.11.0 in this
environment) performs text extraction. It is imported lazily inside
the PDF branch only.

- Bytes flow: the SHA-256, document ID, size, and provenance describe
  the original PDF bytes; only the text is derived. Parser identity is
  `taxguard-sources-v1:pdf:pypdf-<version>`.
- Order and boundaries: pages in deterministic order, joined with
  `--- page N ---` markers (kept even for text-free pages, so mixed
  documents preserve boundaries); per-page text stripped, whole text
  normalized as usual.
- Limits: the existing byte limit applies first; page count is capped
  at `PDF_MAX_PAGES` (2000 — far above plausible single-document legal
  PDFs, bounding CPU/memory). No passwords are requested, stored, or
  guessed: any encrypted PDF is rejected.
- Failures, all typed `IngestionError`: unreadable/malformed PDFs,
  zero-page PDFs, over-limit page counts, unreadable pages (the whole
  ingest fails rather than silently skipping), and text-free PDFs
  ("no extractable text; OCR or an alternative source may be needed" —
  no OCR here). A successful parse never alters source-verification
  state and never implies authenticity, completeness, or applicability.

## Provenance guarantees

Every record traces to its registered source (`source_id` + verification
snapshot) and its exact input bytes (SHA-256). Future retrieval can cite
`document_id`/`source_id` without inventing metadata. Registration ≠
authenticity, parse success ≠ legal correctness, hash ≠ authenticity,
presence ≠ completeness, publication date ≠ applicability.

## Passage chunking (Phase 15)

`chunk_document(document, config)` (`backend/app/chunking.py`, stdlib
only) splits exact normalized text into ordered `Passage` records for
future retrieval display. No tokenizers, stores, or concurrency.

- Configuration: `ChunkConfig{max_chars=1000, overlap_chars=100}`
  (characters, explicitly not tokens; ~a screen of legal text with ~10%
  overlap for clause continuity). `0 <= overlap < max` is enforced.
- Algorithm: greedy paragraph packing (fitting paragraphs are never
  broken); oversized paragraphs fall back to hard character windows;
  consecutive passages overlap by paragraph-snapped `overlap_chars`
  (effective overlap ≤ configured) with minimum one-paragraph progress,
  so termination is guaranteed. Text is preserved byte-for-byte —
  never paraphrased or re-normalized.
- Identity: `passage_id` is `{document_id}:c{index:04d}:{sha12}` over
  document id, zero-based index, algorithm version, and text — stable
  across reruns, distinct across documents. `CHUNKING_VERSION`
  (`taxguard-chunks-v1`) is embedded per passage; each passage also
  echoes its effective `max_chars`/`overlap_chars` and the source
  `parser_id`.
- Offsets: end-exclusive `char_start`/`char_end` with
  `text == source[start:end]` always; empty input yields no passages;
  no passage is empty or exceeds the maximum.
- Page provenance: full-line `--- page N ---` markers map spans to
  1-based `(page_start, page_end)` (equal for single-page, explicit
  range when crossing); markers are included in passage text so offsets
  recover exactly. Non-increasing marker sequences are treated as
  unreliable → `(None, None)`; TXT/HTML without markers are always
  `(None, None)`. Page numbers are never invented.
- Chunking measures text only: it establishes nothing about source
  authenticity, currency, completeness, or applicability, and never
  alters the source document or its verification state.

## What this does not establish

Authenticity, currency, applicability, completeness, or legal meaning of
any document. Real corpus acquisition and source verification remain
future work with independently checked origins.

## Synthetic example

```python
registry = SourceRegistry()
registry.register(SourceRecord(source_id="syn-source-1", title="Synthetic test source"))
record = ingest_file(registry, "syn-source-1", "provision.txt")
# record.format == "txt", record.content_hash == sha256(bytes), status "ok"
```

## Running

```powershell
pytest -q   # full suite, incl. tests/test_sources.py + tests/test_ingestion.py
```
