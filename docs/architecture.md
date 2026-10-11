# TaxGuard Architecture (foundation)

## Decision

Ponytail-first **FastAPI modular monolith**: one deployable service in
`backend/app/`, organized by logical responsibility as modules/packages —
not by premature network boundaries. Chosen over microservices,
serverless-first, RAG-framework-heavy, and monorepo expansion for simpler
initial deployment and fewer dependencies/hops.

Tournament scores behind this choice were design estimates, not benchmarks.

## Current implementation

- `backend/app/main.py`: `create_app()` factory + `app` instance; `GET /health`
  returns `{"status":"ok","app","version"}` with no external calls.
- `backend/app/config.py`: frozen dataclass `Settings` from env vars
  (`TAXGUARD_*`), stdlib only.
- `backend/app/calculation.py` (Phase 1): pure stdlib-only calculation
  primitives (`Decimal`, frozen dataclasses); no FastAPI/DB/network/LLM
  coupling. Contract in `docs/calculation-contract.md`. Kept as a flat
  module until the real engine justifies a `calculation/` package.
- `backend/app/applicability.py` (Phase 2): pure stdlib-only applicability
  resolver (`Resolved`/`Refused`, `resolve_applicability`); no FastAPI/DB/
  network/LLM coupling. Contract in `docs/applicability-contract.md`. Flat
  module until real legal data justifies more.
- `backend/app/evidence.py` (Phase 3): versioned evidence-trace envelope
  (`EvidenceTrace`, schema v1) linking legal basis, evidence refs, claim
  states, and calculation identity. Contract in
  `docs/evidence-trace-contract.md`. Flat stdlib-only module; no retrieval,
  verifier, or persistence.
- `backend/app/verification.py` (Phase 4): claim-verification interface
  (`verify_claim`, `VerificationOutcome`, `apply_outcome`); deterministic,
  stdlib-only, no provider calls. Contract in
  `docs/claim-verification-contract.md`.
- `backend/app/assembly.py` (Phase 5): deterministic answer assembly
  (`assemble_answer`, `AnswerResult` schema v1); composes trace + outcomes
  only. Contract in `docs/answer-assembly-contract.md`.
- `backend/app/registry.py` + `main.py` (Phase 6): explicit in-memory
  inspection registry (one per app instance) with read-only
  `GET /traces/{id}` and `GET /answers/{id}` returning `to_dict()`
  payloads. Contract in `docs/inspection-api-contract.md`. No
  persistence, write endpoints, or new dependencies.
  Phase 22 adds validated `POST /ask` over manifest-pinned passages
  (provenance + abstention; lexical support only), env-configured CORS,
  and `render.yaml` for manual deployment. Contract in
  `docs/question-api-contract.md`.
  Phase 23 renames the match status to honest `retrieved` and adds
  `scripts/build_manifest.py`, so clean checkouts and deploy hosts
  regenerate `corpus/manifest.json` from committed records (no PDFs,
  no network) before serving.
- `backend/app/manifest.py` (Phase 21): versioned corpus manifest
  (canonical snapshot digest) + deterministic passage-index builder
  (`build_passages`) over referenced document records. Inventory only:
  records identities without upgrading their meaning. No persistence,
  endpoints, or new dependencies. Regenerate: load each
  `document.json`, `entry_for()` with corpus-relative paths,
  `build_manifest()`, write `corpus/manifest.json`; validate with
  `pytest -q tests/test_manifest.py -k real_corpus`.
- `backend/app/sources.py` (Phase 7): source registry + deterministic
  local ingestion (txt/HTML, SHA-256 identity, trust-boundary preserving).
  Contract in `docs/source-ingestion-contract.md`. No corpus, network,
  or retrieval.
- `backend/app/source_verification.py` (Phase 10): verification check
  records + fail-closed assessment policy; stdlib-only, never mutates
  source records. Contract in `docs/source-verification-contract.md`.
  No downloads, acquisitions, or manifest changes.
  Phase 14 adds `record_identity` checks, observation provenance, and a
  pure `reconcile()` over measured/receipt/official values.
- `backend/app/acquisition.py` (Phase 11): scoped single-artifact HTTPS
  fetch, atomic storage, provenance receipts; stdlib-only.
  Record in `docs/source-acquisition-record.md`. First real acquisition
  blocked and documented; no artifact, manifest, or new dependencies.
  Phase 14 adds opt-in `fetch_json_metadata` (same fetch policy, small
  bound) for official-record corroboration.
  Phase 12 pilot (`docs/source-fragment-acquisition.md`): one official
  fragment (First Schedule PDF, integrity-checked, ingestion pending);
  corpus under git-ignored `corpus/`.
- `backend/app/retrieval.py` (Phase 8): deterministic BM25 document
  ranking over supplied records; stdlib-only, no stored index.
  Contract in `docs/retrieval-contract.md`. No embeddings, endpoint,
  or measured quality result.
- `backend/app/evaluation.py` (Phase 9): deterministic retrieval
  evaluation (Recall@K, MRR, Precision@K, serializable reports) over
  labeled judgment sets; uses the real retriever.
  Contract in `docs/retrieval-evaluation-contract.md`. Synthetic only.
  Phase 18 adds the separate passage-level contract reusing the same
  metric core; no behavior change to document evaluation.
- `backend/app/chunking.py` (Phase 15): deterministic passage chunking
  (`chunk_document`, `Passage` with offsets and page ranges); stdlib-only,
  never mutates documents. Contract in `docs/source-ingestion-contract.md`.
  No embeddings, stores, or retrieval changes.
- `backend/app/retrieval.py` (Phase 16): `retrieve_passages()` ranking
  passages on the shared BM25 core; `PassageResult` carries verbatim
  provenance. Contract in `docs/retrieval-contract.md`. No embeddings,
  reranking, endpoints, or new dependencies.
- `backend/app/provenance.py` (Phase 17): exact-id passage → document →
  source → assessment bridge (`ResolvedProvenance`, `evidence_ref_for`);
  stdlib-only, unresolved links named explicitly. Contract in
  `docs/evidence-trace-contract.md`. No registries, guessing, or
  verification promotion.
- No middleware, lifespan hooks, DB, or model initialization at startup.

## Future extension points (conceptual only — no stubs created)

Each becomes a module under `backend/app/` only when its phase arrives:

- `ingestion/` — legal corpus import + source provenance (started Phase 7
  as flat `backend/app/sources.py`: source registry, local txt/HTML
  ingestion, SHA-256 identity; synthetic fixtures only, no corpus;
  Phase 10 adds flat `backend/app/source_verification.py`: check records
  and fail-closed assessment, synthetic only, no acquisitions;
  Phase 11 adds flat `backend/app/acquisition.py`: controlled fetch and
  receipts, first real acquisition blocked and documented;
  Phase 12 pilots one official fragment PDF under `corpus/` (preserved,
  ingestion pending, source unverified);
  Phase 13 adds bounded PDF text extraction to `sources.py` via declared
  `pypdf` (page markers, original-byte hashing; real fragment parsed to
  `document.json`, source still unverified);
  Phase 19 adds the Second Schedule the same way (hash-verified, parsed
  to 84 passages, record identity corroborated; both sources unverified);
  Phase 21 adds `corpus/manifest.json` inventory + deterministic builder
  (snapshot 77fdc532…, 2 documents, 91 passages; both unverified).
- `applicability/` — version-aware effective-date logic (started Phase 2 as
  flat `backend/app/applicability.py` with synthetic fixtures only).
- `retrieval/` — hybrid lexical + vector search (started Phase 8 as flat
  `backend/app/retrieval.py`: document-level BM25 baseline, synthetic
  fixtures only; no index or embeddings; Phase 9 adds flat
  `backend/app/evaluation.py`: deterministic Recall@K/MRR/Precision@K
  harness over labeled sets, synthetic only; Phase 15 adds flat
  `backend/app/chunking.py`: deterministic passages with page
  provenance for future display, stdlib-only; Phase 16 adds
  `retrieve_passages()` on the shared BM25 core with verbatim passage
  provenance, no new dependencies; Phase 18 adds passage judgments
  (`PassageBenchmark`, `evaluate_passages`) reusing the same metrics,
  synthetic plus a labeled schedule smoke benchmark; Phase 20 adds a
  two-artifact benchmark and documents extraction anomalies, no parser
  changes).
- `calculation/` — deterministic tax computation (started Phase 1 as flat
  `backend/app/calculation.py`; becomes a package only when the real engine
  needs it).
- `generation/` — LLM answer adapters (replaceable; deterministic assembly
  started Phase 5 as flat `backend/app/assembly.py`, schema v1).
- `verification/` — claim-level checks, contradiction handling (started
  Phase 4 as flat `backend/app/verification.py`: typed conclusions vs.
  operational codes, synthetic fixtures only; no provider).
- `audit/` — evidence-linked traces (started Phase 3 as flat
  `backend/app/evidence.py`, schema v1, synthetic fixtures only).
- `frontend/` — future React + TypeScript client calling this API.

## Database direction

PostgreSQL with pgvector, pending validation of hosting, connection limits,
and cost in the database phase. No SQLAlchemy/Alembic/drivers until then;
health stays DB-independent.

## Performance baseline

No caching, async fan-out, or tuning in foundation. Baseline = health
endpoint latency under local `TestClient`/`uvicorn`; optimize only with
measurements when a real bottleneck appears.
