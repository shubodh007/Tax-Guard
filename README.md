# TaxGuard

Indian income-tax assistant (foundation phase): version-aware legal retrieval,
deterministic tax calculations, and evidence-grounded answers. This repo
currently contains only the minimal FastAPI foundation.

## Scope (foundation + Phase 1)

- `GET /health` returning a small predictable JSON response.
- Typed stdlib-only configuration; no database, LLM, or vector dependencies.
- Deterministic calculation contract (`docs/calculation-contract.md`) with a
  pure stdlib-only example (`backend/app/calculation.py`, synthetic fixture
  only — not tax law).
- Applicability resolver skeleton (`docs/applicability-contract.md`,
  `backend/app/applicability.py`): explicit versioned records, typed
  resolve/refuse outcomes, fail-closed. Synthetic fixtures only.
- Evidence-trace envelope (`docs/evidence-trace-contract.md`,
  `backend/app/evidence.py`): versioned audit record linking legal basis,
  evidence refs, claim states, and calculation identity. No retrieval,
  verifier, or persistence.
- Claim-verification interface (`docs/claim-verification-contract.md`,
  `backend/app/verification.py`): typed conclusions vs. operational codes
  (incl. provider failure / quota / timeout), deterministic policy checks,
  trace integration. Synthetic fixtures only; no LLM or provider.
- Answer assembly (`docs/answer-assembly-contract.md`,
  `backend/app/assembly.py`): deterministic composition of trace + outcomes
  into versioned answers with precedence, safe abstention, no new facts.
- Inspection API (`docs/inspection-api-contract.md`): read-only
  `GET /traces/{id}` and `GET /answers/{id}` over an explicit in-memory
  registry (`backend/app/registry.py`). No persistence or write endpoints.
- Source registry & local ingestion (`docs/source-ingestion-contract.md`,
  `backend/app/sources.py`): explicit source metadata with verification
  states; deterministic UTF-8 text/HTML ingestion with SHA-256 identity.
  Synthetic fixtures only; no corpus, network, or retrieval.
- Bounded PDF text extraction (`pypdf`, declared): text-based PDFs
  ingest to `DocumentRecord` with page markers, original-byte hashing,
  and clear failures for encrypted/image-only/oversize inputs. The
  acquired First Schedule PDF parsed to 5,223 characters (`document.json`
  beside the artifact); source stays unverified.
- Deterministic passage chunking (`backend/app/chunking.py`): ordered
  passages with exact offsets, stable IDs, and PDF page ranges for
  future retrieval display. First Schedule smoke: 7 passages, 317–1000
  chars, both PDF pages represented; source still unverified.
- Passage-level BM25 retrieval (`retrieve_passages` on the shared BM25
  core): ranked `PassageResult` records with verbatim offsets, pages,
  and chunking provenance. First Schedule smoke (7 passages):
  "insurance" → 3 hits (top p.2), "schedule"/"surplus" → 2 hits each;
  all offsets recover exactly; lexical only, no quality claims.
- Provenance bridge (`backend/app/provenance.py`): exact-id resolution
  of passages to documents, source records, and verification
  assessments, with explicit unresolved links and `EvidenceRef`
  conversion. Verification state retained verbatim, never promoted.
- Source-verification evidence (`docs/source-verification-contract.md`,
  `backend/app/source_verification.py`): auditable check records and a
  fail-closed assessment policy separating authenticity, integrity,
  version, completeness, and applicability. No downloads or acquisitions.
- Origin/metadata re-verification (`reconcile`, opt-in metadata fetch):
  record-identity corroboration and discrepancy reporting for the First
  Schedule fragment (integrity passed, identity passed, origin still
  inconclusive; source stays unverified).
- Controlled acquisition machinery (`docs/source-acquisition-record.md`,
  `backend/app/acquisition.py`): scoped HTTPS fetch, atomic storage,
  provenance receipts. First real acquisition (Income-tax Act, 1961 via
  India Code) is blocked and documented — no artifact acquired.
- Fragment pilot (`docs/source-fragment-acquisition.md`): one official
  fragment acquired (First Schedule, Act 43 of 1961: 232,490-byte PDF,
  SHA-256 recorded, integrity-checked, ingestion pending as unsupported
  format). Corpus lives under `corpus/` (git-ignored); source stays
  unverified, applicability unestablished.
- Second fragment (`docs/source-second-schedule-acquisition.md`): Second
  Schedule acquired (498,597-byte PDF, hash-verified, parsed to 67,418
  chars / 84 passages, record identity corroborated live). Both sources
  stay unverified; no completeness or applicability claims.
- BM25 lexical retrieval baseline (`docs/retrieval-contract.md`,
  `backend/app/retrieval.py`): deterministic document-level ranking over
  supplied records with provenance-preserving results. No index,
  embeddings, endpoint, or measured quality result.
- Retrieval evaluation harness (`docs/retrieval-evaluation-contract.md`,
  `backend/app/evaluation.py`): deterministic Recall@K / MRR /
  Precision@K over labeled judgment sets with serializable reports.
  Synthetic fixtures only; no real-corpus quality claims.
- Passage benchmark (`PassageBenchmark`, `evaluate_passages`): separate
  passage-judgment contract sharing the metric core, with text-grounded
  labels. Schedule smoke (3 eligible + 1 excluded): MRR 1.000; no
  broad retrieval-quality claims.
- Two-artifact benchmark (91 passages, both schedules): 5 eligible +
  1 unjudged queries, MRR 1.000. Documents pypdf spacing splits and the
  genuine absence of off-topic vocabulary; no parser changes, sources
  stay unverified.
- Corpus manifest (`backend/app/manifest.py`, `corpus/manifest.json`):
  canonical snapshot digest over inventoried artifacts plus a
  deterministic passage-index builder (2 documents → 91 passages,
  both unverified). Regenerate locally; inventory only, no authority
  claims.
- Question API (`docs/question-api-contract.md`, `POST /ask`):
  validated questions answered from manifest-pinned passages with
  provenance and abstention; lexical support only, never verified
  claims. `render.yaml` + CORS config prepare manual deployment.

See `docs/architecture.md` for the modular-monolith direction and
`AGENTS.md` for engineering rules.

## Requirements

- Python 3.11+

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
```

## Run

```powershell
python scripts/build_manifest.py  # no-op if corpus/manifest.json is current
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/health` → `{"status":"ok","app":"TaxGuard","version":"0.1.0"}`.

Ask the corpus (needs `corpus/manifest.json`; otherwise `/ask` returns 503):

```powershell
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" `
  -d '{"question": "life insurance business profits", "limit": 3}'
```

## Test

```powershell
pytest -q
```

## Deploy (manual; not performed from here)

1. Commit the small corpus JSON records (source/receipt/verification/
   document/manifest); PDFs stay git-ignored by `corpus/**/*.pdf`.
2. Push to GitHub; create a Render web service from `render.yaml`
   (or set build `pip install -e .`, start
   `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT` manually).
3. Set `TAXGUARD_CORS_ORIGINS` to the real frontend origin and
   `TAXGUARD_ENVIRONMENT=production`.
4. Verify `/health` returns 200 and `/ask` answers from the deployed URL.
   Deployment was not performed here — no public URL exists yet.
