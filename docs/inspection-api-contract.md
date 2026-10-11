# TaxGuard Inspection API Contract (Phase 6)

Status: read-only prototype. The API inspects already-registered
`EvidenceTrace` and `AnswerResult` objects; it builds nothing, resolves
nothing, verifies nothing, and persists nothing. Anything labelled
`synthetic-` is a test fixture with no legal force.

## Endpoints

All responses are JSON. Trace/answer bodies are exactly the existing
`EvidenceTrace.to_dict()` / `AnswerResult.to_dict()` output — the API
layer defines no schema of its own and renames no fields.

- `GET /health` → `200 {"status":"ok","app":"TaxGuard","version":"0.1.0"}`.
  Unchanged from the foundation; independent of the registry.
- `GET /traces/{trace_id}` → `200` with the registered trace mapping.
- `GET /answers/{trace_id}` → `200` with the registered answer mapping.

## Status behavior

| Situation | Response |
|---|---|
| Registered id | `200`, exact `to_dict()` body |
| Unknown id | `404 {"detail":"trace '...' not found"}` (or `answer ...`); no placeholder body |
| Blank id (`/traces/%20`) | `422 {"detail":"invalid trace id"}` |
| POST/PUT/DELETE/PATCH on any route | `405` — the surface is read-only |

Error bodies carry only the requested id. No secrets, environment values,
stack traces, or provider details are exposed. A `verified` status in a
payload means "every included claim is supported under the stated policy"
— it never certifies legal correctness.

## Internal registration

```python
from backend.app.main import create_app
from backend.app.registry import Registry

registry = Registry()
registry.register_trace(trace)          # EvidenceTrace
registry.register_answer(trace, answer) # answer.trace_id must match; trace first
app = create_app(registry)              # uvicorn backend.app.main:app uses an empty one
```

Rules: exact-id storage; answer requires its trace already registered
with an identical `trace_id`; duplicate trace or answer ids raise
`RegistryError` instead of silently replacing — use a fresh registry for
a new set. Type violations raise `RegistryError`. Lookups return the
object or `None` (mapped to 404).

## Lifecycle and limits

- Ownership: `create_app()` builds one private `Registry` per call
  (stored on `app.state`), unless a caller supplies one. Instances never
  share state implicitly, so tests get isolated registries for free.
- Volatility: process-local, in-memory. Contents disappear on restart;
  separate worker processes do not share registrations. Intentional
  prototype limitation, not production persistence.
- Security: no authentication in this phase. Do not expose records
  publicly without an access-control design; the payloads may reference
  taxpayer-specific claim sets.

## Synthetic example

```http
GET /traces/syn-trace-1        → 200, the trace mapping from tests/_synthetic.py
GET /answers/syn-trace-1       → 200, the answer mapping from tests/_synthetic.py
GET /traces/syn-absent         → 404 {"detail":"trace 'syn-absent' not found"}
```

## Running

```powershell
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
pytest -q                       # full suite, incl. tests/test_api.py + tests/test_registry.py
```
