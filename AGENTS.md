# AGENTS.md — TaxGuard Permanent Engineering Rules

## Ponytail rules (apply to every change)

1. Ask whether the functionality is necessary now.
2. Reuse existing code and installed dependencies when possible.
3. Prefer standard-library functionality when adequate.
4. Choose the simplest implementation satisfying all acceptance criteria.
5. Avoid unnecessary abstractions, duplicated code, wrappers, speculative interfaces, premature generalization.
6. No new dependency without identifying the specific problem it solves.
7. Minimize code and operational complexity without compromising security, correctness, tests, maintainability.
8. Measure performance; do not claim improvements from intuition.
9. No placeholder modules for unimplemented features.
10. Keep changes small, focused, independently verifiable.

## Project constraints

- FastAPI modular monolith in `backend/app/`; entry point `backend.app.main:app`.
- No database in foundation phase (direction: PostgreSQL + pgvector, to be validated later).
- No LangChain/LangGraph, vector DB clients, embedding libs, model SDKs, Redis, or frontend packages until their phase requires them.
- Startup must be lightweight: no model/embedding loading, no LLM connections, no index construction.
- `GET /health` must not depend on external services.
- Tax math must eventually be deterministic; LLM text is never legal evidence by itself.
- Distinguish verified vs. missing-evidence vs. provider-failure vs. quota-exhaustion (future verification layer).
- Never commit secrets; `.env.example` holds placeholders only.

## Testing requirements

- Every behavior change needs a focused automated test; tests run with `pytest -q`.
- Tests must not require external APIs, paid services, credentials, or a production database.
- Do not mark acceptance criteria complete without evidence (command output).

## Workflow (per phase)

1. Inspect workspace; preserve existing content.
2. Implement smallest coherent scope.
3. Review diff for duplication, unneeded deps, security issues, inconsistent commands.
4. Run relevant tests; fix and rerun.
5. Report actual results, limitations, and the single recommended next task. Stop; do not auto-continue.
