"""Retrieval-quality evaluation set and BM25 baseline (Phase 31).

Judgments are substring-presence engineering fixtures authored from
extracted text — never rank-derived (each asserts a match, failing
loudly if the anchor vanishes) and never legal review. The benchmark
is marked synthetic for exactly that reason. Metrics measure lexical
mechanics on 91 passages, not legal correctness or production
readiness.
"""

import math

from backend.app.evaluation import (
    PassageBenchmark,
    PassageQuery,
    evaluate_passages,
)
from backend.app.manifest import build_passages, load_manifest_file
from backend.app.retrieval import retrieve_passages

# (query_id, query_text, anchor phrase or None, basis)
# Anchor None means the query is expected to match nothing: the
# relevant set is empty and the query is excluded from denominators.
JUDGED = (
    ("q-s1", "profits of life insurance business computed separately",
     "computed separately", "passage states the separate-computation rule"),
    ("q-s2", "adjustment of tax paid by deduction at source",
     "deduction at source", "passage states the deduction-at-source rule"),
    ("q-s3", "deposit by purchaser on resale in default",
     "Deposit by purchaser", "passage states the purchaser-deposit rule"),
    ("q-s4", "Insurance Act 1938 profit and loss account",
     "Insurance Act, 1938", "passage computes profits by reference to that Act"),
    ("q-s5", "gains on realisation of investments added back",
     "realisation of investments", "passage states the realisation rule"),
    ("q-s6", "notice requiring payment within fifteen days of service",
     "fifteen days", "passage states the fifteen-day payment rule"),
    # Lexical variants: same anchors as q-s1 / q-s2 (normalization probes).
    ("q-v1", "PROFITS OF LIFE INSURANCE BUSINESS COMPUTED SEPARATELY",
     "computed separately", "case variant of q-s1; tokenizer lowercases"),
    ("q-v2", "profits, of life insurance business: computed separately!",
     "computed separately", "punctuation variant of q-s1; punctuation splits"),
    # Relevant-set-empty probes (excluded from denominators by
    # construction). Note q-x1/q-x2 still lexically match via generic
    # tokens ("rates", "and") — there is no stop-word list — so empty
    # relevance never implies empty retrieval. Only q-x3 is expected
    # to retrieve nothing at all.
    ("q-x1", "depreciation rates for machinery", None,
     "distinctive term 'depreciation' occurs nowhere in any letter case"),
    ("q-x2", "surcharge and cess applicability", None,
     "distinctive terms 'surcharge'/'cess' occur nowhere"),
    ("q-x3", "taxes insurances", None,
     "plurals occur nowhere; tokenizer does not stem (known limitation)"),
)


def load_passages():
    manifest = load_manifest_file("corpus/manifest.json")
    return build_passages(manifest, ".")


def build_benchmark(passages):
    queries = []
    for query_id, query_text, anchor, basis in JUDGED:
        relevant = tuple(p.passage_id for p in passages if anchor is not None and anchor in p.text)
        if anchor is not None:
            assert relevant, f"judgment anchor {anchor!r} matches nothing; judgment invalid"
        queries.append(PassageQuery(query_id, query_text, relevant, basis))
    return PassageBenchmark(
        dataset_id="schedule-quality-v1", version="v1", synthetic=True, queries=tuple(queries),
    )


def binary_ndcg(relevant: frozenset, ranked_ids: tuple, k: int) -> float:
    """Binary-relevance nDCG@K: gains are 0/1, so DCG sums 1/log2(i+1)
    over hits and IDCG over min(|relevant|, K). Pure scoring helper;
    the production evaluator is untouched."""
    hits = [1.0 if pid in relevant else 0.0 for pid in ranked_ids[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(hits))
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def test_quality_baseline() -> None:
    passages = load_passages()
    assert len(passages) == 91
    benchmark = build_benchmark(passages)
    assert benchmark.synthetic is True
    first = evaluate_passages(passages, benchmark, k_values=(1, 3, 5))
    second = evaluate_passages(passages, benchmark, k_values=(1, 3, 5))
    assert first == second  # deterministic repeatability

    by_id = {q.query_id: q for q in benchmark.queries}
    assert (first.query_count, first.included_count, first.excluded_count) == (11, 8, 3)
    # Only q-x3 retrieves nothing: q-x1/q-x2 match via generic tokens.
    assert retrieve_passages(by_id["q-x3"].query_text, passages, limit=5) == ()
    lines = [f"quality-v1: {len(passages)} passages, "
             f"{first.included_count} eligible, {first.excluded_count} excluded, "
             f"R@1={first.recall_at_k[0]:.3f} R@3={first.recall_at_k[1]:.3f} "
             f"R@5={first.recall_at_k[2]:.3f} MRR={first.mrr:.3f}"]
    ndcgs = []
    for query in first.queries:
        judged = by_id[query.query_id]
        live = tuple(r.passage_id for r in retrieve_passages(
            judged.query_text, passages, limit=5))
        assert query.retrieved_ids == live  # report matches live retrieval
        if not query.included:
            continue
        ndcg = binary_ndcg(frozenset(judged.relevant_passage_ids), query.retrieved_ids, 5)
        ndcgs.append(ndcg)
        lines.append(
            f"  {query.query_id} R@1={query.recall_at[0]:.2f} "
            f"R@5={query.recall_at[2]:.2f} RR={query.reciprocal_rank:.2f} "
            f"nDCG@5={ndcg:.2f}"
        )
    mean_ndcg = sum(ndcgs) / len(ndcgs)
    lines.append(f"mean nDCG@5={mean_ndcg:.3f} over {len(ndcgs)} eligible queries")
    print("\n" + "\n".join(lines))

    # Baseline record (BM25, 2026-10-11, 91 passages). These pin the
    # measured values; they assert mechanics, not readiness.
    assert first.mrr == 1.0
    assert mean_ndcg > 0.9
    assert all(q.basis for q in benchmark.queries if q.relevant_passage_ids)


def test_variants_match_canonical_ranking() -> None:
    """Case/punctuation variants must rank identically to their
    canonical query: normalization probe, not a quality claim."""
    passages = load_passages()
    canonical = tuple(r.passage_id for r in retrieve_passages(
        "profits of life insurance business computed separately", passages, limit=5))
    for variant in (
        "PROFITS OF LIFE INSURANCE BUSINESS COMPUTED SEPARATELY",
        "profits, of life insurance business: computed separately!",
    ):
        assert tuple(r.passage_id for r in retrieve_passages(variant, passages, limit=5)) == canonical
