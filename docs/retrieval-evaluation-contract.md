# TaxGuard Retrieval-Evaluation Contract (Phases 9 + 18)

Status: metric harness plus a passage-level benchmark. There is still
no labeled legal benchmark, no measured real-corpus quality, and no
retrieval endpoint. Anything labelled `synthetic-` is a test fixture
with no legal force.

## Case and report schemas

- `EvalQuery{query_id, query_text, relevant_ids}` — human-authored
  judgments. Blank ids/text, duplicate relevant ids, and duplicate
  query ids (at the set level) raise `EvaluationError`.
- `EvaluationSet{dataset_id, queries, version="v1", synthetic=True}` —
  the `synthetic` flag defaults True so unlabeled data can never pass
  as real measurements.
- `EvaluationReport` (schema version 1): dataset id/version/synthetic
  flag, canonical ascending `k_values`, query/included/excluded counts,
  mean `recall_at_k`, `precision_at_k`, `mrr`, and per-query
  `QueryResult` records (ranked ids, first-relevant rank, per-K values,
  inclusion flag) in dataset order. Strict `to_dict()` for JSON output.

## Judgment semantics

Relevant sets are explicit human labels. Missing labels mean unknown,
never irrelevant. Relevance is never inferred from BM25 scores — the
evaluator reads only document ids and ranks. Queries with empty
judgment sets are valid cases but excluded from every denominator and
reported via `excluded_count`; a dataset with no judged query raises
instead of reporting a misleading zero.

## Metric definitions

For relevant total R > 0, ranking prefix top-K:

    Recall@K    = |relevant ∩ top-K| / R
    Precision@K = |relevant ∩ top-K| / K   (standard denominator K, even
                  when fewer than K ids were returned)
    RR          = 1 / first relevant rank, or 0 when absent

MRR is the arithmetic mean of RR over included queries only. Duplicate
ranked ids are rejected — one document can never count twice.
`score_ranking()` computes these for any id listing independently of
the retriever; `evaluate()` runs the real `retrieve()` once per query
at `limit=max(K)` and scores prefixes of that single ranking.

## Passage-level evaluation (Phase 18)

Separate types, same math: `PassageQuery{query_id, query_text,
relevant_passage_ids, basis}` (a `basis` rationale is required whenever
relevant passages exist, so every label carries its justification) and
`PassageBenchmark{dataset_id, queries, version, synthetic}` — deliberately
distinct from `EvaluationSet`/`EvalQuery` so document and passage
judgments cannot mix. `evaluate_passages()` runs the real
`retrieve_passages()` once per query at `limit=max(K)` and aggregates
through the shared definitions into the same `EvaluationReport` shape
(same denominators, eligibility, exclusion accounting, and K
canonicalization).

Judgments must be authored from source text, never derived from the
ranking under test; a missing judgment is unknown, not negative.
Relevant ids absent from the index stay in the denominator (retrievable
in principle, unretrieved in fact). Scores remain unread throughout.

## Two-artifact smoke benchmark (Phase 20)

`two-schedule-bench-v1` (in `tests/test_two_artifact_benchmark.py`,
skipped where the artifacts are absent): 91 passages from both
schedules, 5 eligible text-grounded queries + 1 unjudged, K=(1,3,5),
measured MRR=1.000. Queries target each schedule and span both;
relevance phrases were located by substring presence in extracted
text, never by rank. This exercises cross-artifact mechanics only —
two unverified fragments prove no broad retrieval quality.

## Corpus manifest relation (Phase 21)

The same 91 passages (7 + 84) are reproducible from
`corpus/manifest.json` via `build_passages()`: identical passage IDs,
ordering, offsets, and metadata as direct chunking. The benchmark does
not depend on the manifest at runtime; the manifest pins the corpus
snapshot the benchmark numbers were measured against (snapshot
`77fdc532…`, both sources unverified). Counts above are regenerated
outputs, not hardcoded claims.

## Extraction anomalies on record

- First Schedule: pypdf word-spacing splits (`compu ted`,
  `insuranc e`, one occurrence each) coexist with intact forms of the
  same words in the same document. Passages remain reachable via
  intact terms. No parser change: fixing would require destructive
  normalization or a new text representation that collides with
  byte-derived document identity.
- Second Schedule: `depreciation` is genuinely absent (all letter
  cases) — the schedule concerns tax-recovery procedure. Zero-hit
  queries on absent vocabulary are correct behavior, not a defect.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_evaluation.py, tests/test_passage_evaluation.py
```

## Future real evaluation sets

Build from verified ingested documents with human-authored relevance
judgments, recorded as a versioned `EvaluationSet` with a non-synthetic
`dataset_id`, explicit `version`, and `synthetic=False`, preserving
judgment provenance (who labeled, from which sources, at which corpus
snapshot). Fixture numbers here prove arithmetic only: they establish
no recall, MRR, accuracy, or legal correctness on real tax-law
questions, amendments, or transitional provisions.
