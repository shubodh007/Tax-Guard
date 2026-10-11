"""Two-artifact benchmark over the First + Second Schedule passages.

Passages come from the deterministic pipeline (never hardcoded IDs);
relevance judgments are authored from extracted-text substring presence
(text-grounded, never rank-derived), each with an explicit basis. The
corpus is tiny and both sources unverified: this measures lexical
mechanics, not legal retrieval quality. Skipped where the artifacts
are absent.
"""

import json
import os

import pytest

from backend.app.chunking import chunk_document
from backend.app.evaluation import PassageBenchmark, PassageQuery, evaluate_passages
from backend.app.retrieval import retrieve_passages
from backend.app.sources import DocumentRecord

CORPUS = os.path.join(os.path.dirname(__file__), "..", "corpus", "india-code")
FIRST = os.path.join(CORPUS, "income-tax-act-1961-first-schedule", "document.json")
SECOND = os.path.join(CORPUS, "income-tax-act-1961-second-schedule", "document.json")

JUDGED = (
    # (query_id, query_text, distinctive phrase, basis)
    ("two-q1",
     "life insurance business profits computed separately",
     "computed separately",
     "passage states profits of life insurance business computed separately"),
    ("two-q2",
     "adjustment of tax paid by deduction at source",
     "deduction at source",
     "passage states the rule on adjustment of tax paid by deduction at source"),
    ("two-q3",
     "deposit by purchaser resale in default",
     "Deposit by purchaser",
     "passage states the purchaser-deposit rule for sales in default"),
    ("two-q4",
     "Insurance Act 1938 profit and loss account",
     "Insurance Act, 1938",
     "passage computes insurance profits by reference to that Act"),
    ("two-q5",
     "realisation of investments gains added back",
     "realisation of investments",
     "passage states the rule on gains on realisation of investments"),
)


def load_passages():
    documents = []
    for path in (FIRST, SECOND):
        with open(path, encoding="utf-8") as handle:
            documents.append(DocumentRecord(**json.load(handle)))
    passages = []
    for document in documents:
        passages.extend(chunk_document(document))
    return documents, passages


def build_benchmark(passages):
    queries = []
    for query_id, query_text, phrase, basis in JUDGED:
        relevant = tuple(p.passage_id for p in passages if phrase in p.text)
        assert relevant, f"judgment phrase {phrase!r} matches nothing; judgment invalid"
        queries.append(PassageQuery(query_id, query_text, relevant, basis))
    queries.append(PassageQuery("two-q6", "depreciation annuity pension", ()))
    return PassageBenchmark(
        dataset_id="two-schedule-bench-v1", version="v1", synthetic=False,
        queries=tuple(queries),
    )


@pytest.mark.skipif(
    not (os.path.exists(FIRST) and os.path.exists(SECOND)),
    reason="acquired artifacts absent from this checkout",
)
def test_two_artifact_benchmark() -> None:
    documents, passages = load_passages()
    assert len(documents) == 2
    assert len(passages) == 7 + 84
    benchmark = build_benchmark(passages)
    report = evaluate_passages(passages, benchmark, k_values=(1, 3, 5))

    judged = {q.query_id: q for q in benchmark.queries}
    lines = [f"two-artifact: {len(passages)} passages, "
             f"{report.included_count} eligible, {report.excluded_count} excluded, "
             f"MRR={report.mrr:.3f}"]
    for query in report.queries:
        short = lambda pid: pid.split(":")[-2] + ":" + pid.split(":")[-1][:8]
        lines.append(
            f"  {query.query_id} eligible={query.included} "
            f"relevant={[short(i) for i in judged[query.query_id].relevant_passage_ids]} "
            f"top3={[short(i) for i in query.retrieved_ids[:3]]} "
            f"R@1={query.recall_at[0]:.2f} RR={query.reciprocal_rank:.2f}"
        )
    print("\n" + "\n".join(lines))

    assert (report.query_count, report.included_count, report.excluded_count) == (6, 5, 1)
    assert all(q.basis for q in benchmark.queries if q.relevant_passage_ids)
    # Per-query audit matches live retrieval; relevance spans both artifacts.
    artifacts = set()
    for query in report.queries:
        live = tuple(r.passage_id for r in retrieve_passages(
            judged[query.query_id].query_text, passages, limit=5))
        assert query.retrieved_ids == live
        for pid in judged[query.query_id].relevant_passage_ids:
            artifacts.add("first" if "first-schedule" in pid else "second")
    assert artifacts == {"first", "second"}
    assert 0.0 <= report.mrr <= 1.0


def load_documents():
    with open(FIRST, encoding="utf-8") as handle:
        first = DocumentRecord(**json.load(handle))
    with open(os.path.join(CORPUS, "income-tax-act-1961-second-schedule",
                           "document.json"), encoding="utf-8") as handle:
        second = DocumentRecord(**json.load(handle))
    return first, second


@pytest.mark.skipif(
    not os.path.exists(FIRST),
    reason="acquired artifact absent from this checkout",
)
def test_extraction_anomalies_documented() -> None:
    first, second = load_documents()
    first_text, second_text = first.normalized_text, second.normalized_text
    # Known pypdf word-spacing artifacts coexist with intact forms in the
    # same document: retrieval must still reach these passages via intact
    # terms. Pinned here so any parser change revisits the diagnosis.
    assert "compu ted" in first_text and "computed" in first_text
    assert "insuranc e" in first_text and first_text.count("insurance") >= 2
    hits = retrieve_passages("insurance", chunk_document(first), limit=7)
    assert any("insuranc e" in h.text for h in hits)

    # Genuine absence, not a parser defect: the schedule concerns tax
    # recovery procedure and never uses the word in any letter case.
    assert "depreciation" not in second_text.lower()
    assert retrieve_passages("depreciation", chunk_document(second), limit=5) == ()
