# TaxGuard Initial Scope Finalization (Phase 30, Workstream 1)

Status: scope decision recorded. Q7 source inspection and Q4
retrieval-only over the two held schedule fragments are the initial
scope. Every substantive tax-law answer stays abstention-only until
the listed evidence exists. No applicable law is inferred from
filenames or years. Unresolved legal-policy choices are recorded in
§4, not silently decided. No corpus, code, or configuration was
changed by this decision.

## 1. What users can inspect or retrieve

- **Q7 — inspect stored traces and answers.** `GET /traces/{id}`,
  `GET /answers/{id}` over already-built objects. Needs no legal
  evidence; unknown ids return 404 and never invent content.
- **Q4 — retrieve schedule-fragment passages.** `POST /ask` returns
  `retrieved` lexical matches (or `abstained` on no match) from the
  First and Second Schedule fragments, each passage carrying
  `source_verification: "unverified"` and the standing limitation
  notice (two unverified fragments, lexical matching only,
  informational, not professional tax advice). Retrieved text must
  never be presented or cited as legal authority.

## 2. Abstention-only questions

Q1 total liability, Q2 regime choice, Q3 deduction availability, Q5
applicable law / assessment-year routing, Q6 provision change, and
any deterministic calculation: the answer path must clarify or
abstain with a stated reason. The required inputs (charging
provisions, rate schedules, deduction provisions, election
conditions, commencement/repeal/savings and transition instruments,
versioned historical texts) do not exist in-repo, and the two held
fragments are unverified regardless.

## 3. Minimum official instruments for this scope

The finalized scope needs no new instruments: Q7 needs none, and Q4
retrieval-only is already served by the two held fragments named in
`authoritative-corpus-foundation.md` §1.1. Any future substantive
scope additionally requires, per question, the §4 matrix of
`initial-coverage-scope.md` (period-bound charging provisions, rate
schedules, claimed deduction provisions, commencement records) plus
the §6 approvals — none of which is granted here.

## 4. Unresolved legal-policy choices (recorded, not decided)

1. Covered tax periods and the 1961 vs 2025 regime (§6.2) — undecided.
2. What counts as authenticity/currency evidence (§6.3) — undecided;
   see `trust-anchor-feasibility.md`.
3. Completeness bar per source type (§6.4) — undecided.
4. Whether the held fragments can ever reach `verified` — open.
5. Authoritative transition instruments — unknown.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_corpus_foundation.py
```
