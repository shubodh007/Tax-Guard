"""Passage-evaluation tests using SYNTHETIC fixtures, plus an opt-in
smoke benchmark over the acquired First Schedule passages (skipped
where the artifact is absent).

The synthetic benchmark below is fully hand-computable. Corpus
(max_chars=15, overlap 0):
- syn-pb-doc-1 "alpha beta\\n\\ngamma delta" -> pA0 "alpha beta", pA1 "gamma delta"
- syn-pb-doc-2 "alpha gamma\\n\\ndelta zeta" -> pB0 "alpha gamma", pB1 "delta zeta"

K=(1,2). Hand-computed expectations:
- pq1 "beta" relevant {pA0}: only pA0 matches -> RR=1, R=(1,1), P=(1,1/2).
- pq2 "alpha" relevant {pA0,pB0}: both tf=1 len 2, tie -> doc-1 passage
  first -> RR=1, R=(1/2,1), P=(1,1).
- pq3 "omega" relevant {withheld passage about omega}: nothing matches ->
  RR=0, R=(0,0), P=(0,0). Eligible (genuine label, absent from index).
- pq4 unjudged -> excluded.
Aggregates over pq1-3: MRR=2/3, R@1=(1+1/2+0)/3=1/2, R@2=(1+1+0)/3=2/3,
P@1=(1+1+0)/3=2/3, P@2=(1/2+1+0)/3=1/2.

Relevance labels were authored from fixture text, never from rankings.
Nothing here measures real legal retrieval quality.
"""

import json
import os

import pytest

from backend.app.chunking import ChunkConfig, chunk_document
from backend.app.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    EvaluationError,
    EvaluationSet,
    PassageBenchmark,
    PassageQuery,
    evaluate,
    evaluate_passages,
    score_ranking,
)
from backend.app.retrieval import retrieve_passages
from backend.app.sources import DocumentRecord

PASSAGE_BUDGET = ChunkConfig(max_chars=15, overlap_chars=0)


def syn_doc(text: str, doc_id: str) -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        source_id="syn-source-1",
        filename=f"{doc_id}.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash="a" * 64,
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification="unverified",
    )


def synthetic_corpus():
    doc1 = syn_doc("alpha beta\n\ngamma delta", "syn-pb-doc-1")
    doc2 = syn_doc("alpha gamma\n\ndelta zeta", "syn-pb-doc-2")
    passages = chunk_document(doc1, PASSAGE_BUDGET) + chunk_document(doc2, PASSAGE_BUDGET)
    by_text = {p.text: p.passage_id for p in passages}
    assert set(by_text) == {"alpha beta", "gamma delta", "alpha gamma", "delta zeta"}

    withheld = chunk_document(syn_doc("omega particle", "syn-pb-doc-9"), PASSAGE_BUDGET)[0]
    benchmark = PassageBenchmark(
        dataset_id="synthetic-passage-bench-v1",
        version="v1",
        synthetic=True,
        queries=(
            PassageQuery("syn-pq-1", "beta", (by_text["alpha beta"],),
                         "passage states the queried beta proposition"),
            PassageQuery("syn-pq-2", "alpha", (by_text["alpha beta"], by_text["alpha gamma"]),
                         "both passages state queried alpha propositions"),
            PassageQuery("syn-pq-3", "omega", (withheld.passage_id,),
                         "withheld passage states the queried omega proposition; "
                         "absent from the evaluated index by design"),
            PassageQuery("syn-pq-4", "delta", ()),
        ),
    )
    return passages, benchmark


def approx(values) -> list:
    return [pytest.approx(v) for v in values]


def test_synthetic_benchmark_metrics() -> None:
    passages, benchmark = synthetic_corpus()
    report = evaluate_passages(passages, benchmark, k_values=(1, 2))

    assert report.schema_version == EVALUATION_SCHEMA_VERSION
    assert report.dataset_id == "synthetic-passage-bench-v1"
    assert report.synthetic is True
    assert report.k_values == (1, 2)
    assert (report.query_count, report.included_count, report.excluded_count) == (4, 3, 1)

    by_id = {q.query_id: q for q in report.queries}
    assert list(by_id["syn-pq-1"].recall_at) == approx((1.0, 1.0))
    assert list(by_id["syn-pq-1"].precision_at) == approx((1.0, 1 / 2))
    assert by_id["syn-pq-1"].reciprocal_rank == pytest.approx(1.0)
    assert list(by_id["syn-pq-2"].recall_at) == approx((1 / 2, 1.0))
    assert list(by_id["syn-pq-2"].precision_at) == approx((1.0, 1.0))
    assert by_id["syn-pq-2"].reciprocal_rank == pytest.approx(1.0)
    assert by_id["syn-pq-3"].reciprocal_rank == 0.0
    assert tuple(by_id["syn-pq-3"].recall_at) == (0.0, 0.0)
    assert by_id["syn-pq-4"].included is False

    assert report.mrr == pytest.approx(2 / 3)
    assert list(report.recall_at_k) == approx((1 / 2, 2 / 3))
    assert list(report.precision_at_k) == approx((2 / 3, 1 / 2))


def test_deterministic_aggregates() -> None:
    passages, benchmark = synthetic_corpus()
    first = evaluate_passages(passages, benchmark, k_values=(1, 2))
    second = evaluate_passages(passages, benchmark, k_values=(2, 1))
    assert first == second
    assert json.loads(json.dumps(first.to_dict()))["mrr"] == pytest.approx(2 / 3)


def test_contract_validation() -> None:
    passages, benchmark = synthetic_corpus()
    with pytest.raises(EvaluationError, match="duplicate query"):
        PassageBenchmark("d", (PassageQuery("q", "alpha beta", ()),
                               PassageQuery("q", "gamma", ())))
    with pytest.raises(EvaluationError, match="duplicate relevant"):
        PassageQuery("q", "alpha", ("p1", "p1"), basis="synthetic")
    with pytest.raises(EvaluationError, match="without a basis"):
        PassageQuery("q", "alpha", ("p1",))
    with pytest.raises(EvaluationError):
        PassageBenchmark("d", ("not-a-query",))  # type: ignore[arg-type]
    for bad_k in ((), (0,), (True,), ("2",)):
        with pytest.raises(EvaluationError):
            evaluate_passages(passages, benchmark, k_values=bad_k)  # type: ignore[arg-type]
    with pytest.raises(EvaluationError, match="PassageBenchmark"):
        evaluate_passages(passages, EvaluationSet("d", ()))  # type: ignore[arg-type]


def test_all_ineligible_rejected_and_separation() -> None:
    passages, _ = synthetic_corpus()
    empty_bench = PassageBenchmark("d", (PassageQuery("q", "alpha", ()),))
    with pytest.raises(EvaluationError, match="no queries with relevance"):
        evaluate_passages(passages, empty_bench)

    docs = (syn_doc("alpha beta", "syn-pb-doc-1"),)
    with pytest.raises(EvaluationError, match="EvaluationSet"):
        evaluate(docs, PassageBenchmark("d", ()))  # type: ignore[arg-type]


def test_inputs_not_mutated() -> None:
    passages, benchmark = synthetic_corpus()
    before = [(q.query_id, q.query_text, q.relevant_passage_ids) for q in benchmark.queries]
    evaluate_passages(passages, benchmark, k_values=(1, 2))
    assert [(q.query_id, q.query_text, q.relevant_passage_ids) for q in benchmark.queries] == before


REAL_DOCUMENT = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "india-code",
    "income-tax-act-1961-first-schedule", "document.json",
)


def _containing(passages, phrase: str) -> tuple[str, ...]:
    return tuple(p.passage_id for p in passages if phrase in p.text)


@pytest.mark.skipif(
    not os.path.exists(REAL_DOCUMENT),
    reason="acquired artifact absent from this checkout",
)
def test_first_schedule_smoke_benchmark() -> None:
    with open(REAL_DOCUMENT, encoding="utf-8") as handle:
        document = DocumentRecord(**json.load(handle))
    passages = chunk_document(document)
    assert len(passages) == 7

    # Judgments authored from the extracted text (substring presence),
    # never from rankings.
    benchmark = PassageBenchmark(
        dataset_id="first-schedule-smoke-v1",
        version="v1",
        synthetic=False,
        queries=(
            PassageQuery("sch-q1", "life insurance business profits computed separately",
                         _containing(passages, "computed separately"),
                         "passage states profits of life insurance business computed separately"),
            PassageQuery("sch-q2", "deduction at source adjustment",
                         _containing(passages, "deduction at source"),
                         "passage states the rule on adjustment of tax paid by deduction at source"),
            PassageQuery("sch-q3", "investments securities shares interpretation",
                         _containing(passages, "investments"),
                         "passage Interpretation clause defines investments"),
            PassageQuery("sch-q4", "quantum zebra", ()),
        ),
    )
    report = evaluate_passages(passages, benchmark, k_values=(1, 3))
    judged = {q.query_id: q for q in benchmark.queries}

    lines = [f"smoke: {report.included_count} eligible, "
             f"{report.excluded_count} excluded, MRR={report.mrr:.3f}"]
    for query in report.queries:
        short = lambda pid: pid.split(":")[-2] + ":" + pid.split(":")[-1][:8]
        lines.append(
            f"  {query.query_id} eligible={query.included} "
            f"relevant={[short(i) for i in judged[query.query_id].relevant_passage_ids]} "
            f"ranked={[short(i) for i in query.retrieved_ids]} "
            f"R@1={query.recall_at[0]:.2f} P@1={query.precision_at[0]:.2f} RR={query.reciprocal_rank:.2f}"
        )
    print("\n" + "\n".join(lines))

    assert (report.query_count, report.included_count, report.excluded_count) == (4, 3, 1)
    assert 0.0 <= report.mrr <= 1.0
    for query in report.queries:
        live = tuple(r.passage_id for r in retrieve_passages(
            judged[query.query_id].query_text, passages, limit=3))
        assert query.retrieved_ids == live
