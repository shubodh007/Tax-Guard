# TaxGuard Neon Persistence (Phase 32)

Status: implemented, tested against fakes only. **No live Neon
connection was established in this environment** (no credentials, no
server): every database test in `tests/test_neon_store.py` runs
against an in-process fake DBAPI connection and is labelled as such.
File-backed mode remains the default and is unchanged. Both legal
sources remain `unverified`; retrieval metrics measure lexical
mechanics only and establish no legal accuracy.

## 1. Setup (Neon)

1. Create a Neon project (PostgreSQL 16+) with a pooled connection
   string for the app and keep the unpooled host for migrations.
2. Copy `.env.example` to `.env` (git-ignored) and fill the
   `TAXGUARD_DATABASE_URL*` placeholders — never commit real values.
3. Install a driver for database-backed mode only:
   `pip install "psycopg[binary]"` (preferred) — `psycopg2` also
   works. File-backed mode and the test suite need neither.
4. Apply migrations, then import (dry-run first):
   `python scripts/import_corpus.py --dry-run` →
   `python scripts/import_corpus.py --apply`.

## 2. Environment reference (names only, never values)

`TAXGUARD_DATABASE_URL` (required for db mode; pooled app URL),
`TAXGUARD_DATABASE_URL_UNPOOLED` (migrations; defaults to the pooled
URL — do not assume the pooled URL suits migrations),
`TAXGUARD_DB_POOL_MAX` (1–20, default 5),
`TAXGUARD_DB_CONNECT_TIMEOUT` (1–60 s, default 10),
`TAXGUARD_DB_STATEMENT_TIMEOUT_MS` (default 30000),
`TAXGUARD_DB_REQUIRE_SSL` (default true; Neon hosts require
`sslmode=require|verify-ca|verify-full`). Missing/invalid settings
fail fast with `DbConfigError` before any connection is opened.
Without `TAXGUARD_DATABASE_URL`, the app stays file-backed.

## 3. Connections and pooling

`backend/app/db.py`: lazy driver import (`psycopg`, fallback
`psycopg2`); per-operation connections with connect + statement
timeouts; `ConnectionPool` is caller-owned, bounded (`pool_max`),
lazily filled, explicitly closed — no globals, no background
reconnects, broken connections discarded on error. Every error is
raised as `DbConnectionError` with the DSN password masked
(`redact_dsn`); driver error text is never trusted into messages,
logs, responses, or test output.

## 4. Schema and migrations

`backend/app/migrations/0001_corpus.sql`: `sources`, `documents`,
`passages` (UNIQUE per document/chunk), `assessments` (PK =
assessment digest), `checks`, `corpus_meta` (snapshot identity),
`ingestion_runs` (audit), plus FKs, CHECKs, and by-source/by-document
indexes. `apply_migrations()` runs pending `*.sql` in order, one
transaction each, recording `schema_migrations`; re-runs are no-ops.
`0001_corpus.down.sql` drops everything — inherently destructive
(data loss): back up with `pg_dump` first, stop traffic, apply in one
transaction; the file-backed corpus under `corpus/` is untouched and
remains the re-import source of truth. Never drop/truncate shared
tables to fit a schema.

## 5. Corpus import

`python scripts/import_corpus.py [--root DIR] [--dry-run|--apply]`
(default dry-run). The importer validates every record first
(receipt + document hashes recomputed by streaming the artifact,
byte lengths, id links), then writes once in a single transaction:
identical rows are skipped (safe retries), conflicting rows raise
`ImportConflictError` without overwriting, and any failure rolls
back. Stored snapshot must equal the file snapshot or the import is
refused — provenance is preserved verbatim, including the stale
size-metadata discrepancies (recorded, never repaired) and the
second document's 32-char id suffix quirk.

## 6. Retrieval and verification invariants

Database-backed `/ask` (`create_app(db=...)`) loads rows into the
identical corpus shape and runs the identical BM25 core and answer
path: same rankings, same passages, same `unverified` labels,
limitations, and abstention. Parity is proven in
`test_file_db_retrieval_parity` (identical reports on the Phase 31
quality queries). Non-unverified rows are re-authorized through
`verify_entry_binding` under the pinned canonical policy — a stored
`verified` label, fabricated `origin: passed`, unknown policy, or
missing/broken links all fail the load into the same generic 503.
Readiness reflects corpus-load success only, never bare database
reachability. Importing records never authorizes them.

## 7. Running tests

```powershell
pytest -q   # full suite; DB tests are fake-backed units, no server needed
```

```powershell
C:\Users\shubodh\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```

Live-Neon integration tests do not exist yet: add them only with a
disposable non-production schema, never destructively, never with
credentials in the report. Local-PostgreSQL tests likewise did not
run (no server here).

## 8. Limitations and risks

Untested: real driver wire behavior, migration SQL against a live
server (reviewed, not executed), concurrent imports, pool behavior
under load, cold-start/latency beyond the Phase 31 BM25 figure.
`verdicts`/`receipt` JSONB columns assume PostgreSQL. The fake
connection does not enforce CHECK/FK constraints — schema safety
rests on review, not tests. Both sources stay `unverified`; no
origin anchor exists (see `trust-anchor-feasibility.md`).
