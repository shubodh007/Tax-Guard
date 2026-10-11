# TaxGuard Retrieval Quality, Phase 31

Status: measurement record. No retrieval, evaluation, or application
code was changed: the benchmark is too small to justify any
algorithmic change, and every candidate improvement was rejected as
unjustified (details in §4). No legal materials were acquired, no
source was promoted, and no metric here claims legal-answer accuracy
or production readiness. Judgments are substring-presence engineering
fixtures (`synthetic=True`), not legal review.

## 1. Evaluation set (`schedule-quality-v1`, `tests/test_retrieval_quality.py`)

11 queries over the 91 held passages; relevance resolved dynamically
from anchor-phrase presence (asserted, never rank-derived):

- Supported retrieval-only (6): q-s1…q-s6, distinctive phrases from
  both schedules ("computed separately", "deduction at source",
  "Deposit by purchaser", "Insurance Act, 1938",
  "realisation of investments", "fifteen days").
- Lexical variants (2): q-v1 (case), q-v2 (punctuation) — same
  anchors as q-s1/q-s2; must rank identically to canonical.
- Relevant-set-empty probes (3, excluded from denominators): q-x1
  ("depreciation…", distinctive term absent, generic tokens still
  match — no stop-word list), q-x2 ("surcharge and cess…", same),
  q-x3 ("taxes insurances", plurals absent — no stemming; the only
  probe expected to retrieve nothing).

Coverage and limits: 8 eligible / 3 excluded at cutoffs (1, 3, 5);
judgments cover two fragments of one Act only; substring presence
stands in for relevance, so near-ceiling scores reflect anchor
distinctiveness, not search quality. Version-sensitive and ambiguous
behavior is pinned at the app layer in
`tests/test_evidence_abstention.py`, not by ranking judgments.

## 2. Baseline vs final (same code — no change made)

| Metric (8 eligible) | Baseline = Final |
|---|---|
| Recall@1 / @3 / @5 | 0.833 / 0.875 / 1.000 |
| MRR | 1.000 |
| mean nDCG@5 (binary) | 0.970 |
| Deterministic repeat | identical reports and live rankings |

MRR=1.000 on this tiny set is explicitly not evidence of
readiness — every query contains a near-unique anchor phrase.

## 3. Evidence and abstention enforcement (traced, pinned)

`POST /ask` was traced question → `retrieve_passages` →
`resolve_provenance` → response assembly (`backend/app/main.py`
`ask()`): statuses are only `retrieved`/`abstained` (never
`supported`/`verified`); every retrieved passage carries the
manifest `source_verification` (`unverified` throughout),
`passage_id`/`document_id`/`source_id`, and `unresolved`
(including `"assessment"`); `LIMITATIONS` ships on both outcomes;
the answer text disclaims legal force. Empty retrieval abstains
with `no_matching_evidence`. Malformed inputs 422; corrupt corpus
and unknown ids serve generic bodies with no paths or internals
(pinned in `tests/test_evidence_abstention.py`). Verification gates
unchanged.

## 4. Improvement analysis (examined, all rejected)

- Tokenizer/normalization: case + punctuation variants rank
  identically (pinned); underscore splitting and Unicode handling
  unchanged and unchallenged by the set.
- No stemming (q-x3 empty): adding stemming would merge distinct
  legal forms ("assess/assessment/assessing") on the say-so of one
  probe — rejected as overfitting with legal-text risk.
- No stop-word list (q-x1/q-x2 still retrieve via generic tokens):
  removal would rescore every query with no attributable failure —
  rejected; the labels+limits framing already prevents authority
  laundering.
- BM25 parameters / tie-breaking / positive-only filtering:
  deterministic and at ceiling on this set; tuning without headroom
  is noise-fitting — rejected.
- Embeddings, vector stores, rerankers, model calls: no failure
  attributable to lexical ranking on the evidence; rejected without a
  measured need.

Required data for any future change: a larger judged set with
genuinely ambiguous queries, reviewed (not substring) relevance, and
at least one reproduced failure the candidate fixes without
regressing pinned cases.

## 5. Latency (measured, no optimization claimed)

Repeatable local smoke (`test_retrieval_latency_smoke`, 91
passages): mean **41.691 ms/call** (8 query texts × 50 repetitions,
warmup excluded). Baseline = post-change (identical code). Bound
asserts < 1000 ms/call as pathological-slowdown guard only.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_retrieval_quality.py + tests/test_evidence_abstention.py
```

```powershell
C:\Users\shubodh\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```
