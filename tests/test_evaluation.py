"""Evaluation harness tests using SYNTHETIC fixtures only.

Metric expectations are hand-calculated below; they prove arithmetic
correctness, not real-world retrieval quality. No external services.
"""

import json

import pytest

from backend.app.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    EvalQuery,
    EvaluationError,
    EvaluationSet,
    evaluate,
    score_ranking,
)
from backend.app.retrieval import retrieve
from backend.app.sources import DocumentRecord


def syn_doc(doc_id: str, text: str) -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        source_id="syn-source-1",
        filename=f"{doc_id}.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash=f"syn-hash-{doc_id}",
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification="unverified",
    )


def approx(values: tuple[float, ...]) -> list[float]:
    return [pytest.approx(v) for v in values]


def test_recall_precision_rr_first_rank() -> None:
    scores = score_ranking(("a", "b", "c"), ("a", "x", "y"), (1, 2, 3))
    assert list(scores.recall_at) == approx((1 / 3, 1 / 3, 1 / 3))
    assert list(scores.precision_at) == approx((1.0, 1 / 2, 1 / 3))
    assert scores.reciprocal_rank == pytest.approx(1.0)
    assert scores.first_relevant_rank == 1


def test_relevant_at_later_rank() -> None:
    scores = score_ranking(("a", "b", "c"), ("x", "a", "y"), (1, 2, 3))
    assert list(scores.recall_at) == approx((0.0, 1 / 3, 1 / 3))
    assert list(scores.precision_at) == approx((0.0, 1 / 2, 1 / 3))
    assert scores.reciprocal_rank == pytest.approx(1 / 2)
    assert scores.first_relevant_rank == 2


def test_no_relevant_retrieved() -> None:
    scores = score_ranking(("a", "b"), ("x", "y", "z"), (1, 2))
    assert tuple(scores.recall_at) == (0.0, 0.0)
    assert tuple(scores.precision_at) == (0.0, 0.0)
    assert scores.reciprocal_rank == 0.0
    assert scores.first_relevant_rank == 0


def test_multiple_relevant_within_k() -> None:
    scores = score_ranking(("a", "b"), ("a", "b"), (1, 2))
    assert list(scores.recall_at) == approx((1 / 2, 1.0))
    assert list(scores.precision_at) == approx((1.0, 1.0))
    assert scores.reciprocal_rank == pytest.approx(1.0)


def test_fewer_returned_than_k_uses_k_denominator() -> None:
    scores = score_ranking(("a",), ("a", "b"), (1, 5))
    assert list(scores.recall_at) == approx((1.0, 1.0))
    assert list(scores.precision_at) == approx((1.0, 1 / 5))


def test_ranking_validation() -> None:
    with pytest.raises(EvaluationError, match="duplicate"):
        score_ranking(("a",), ("a", "a"), (1,))
    for bad_k in ((), (0,), (-1,), (True,), ("2",), "1"):
        with pytest.raises(EvaluationError, match="[Kk]"):
            score_ranking(("a",), ("a",), bad_k)  # type: ignore[arg-type]
    with pytest.raises(EvaluationError):
        score_ranking("a", ("a",), (1,))  # type: ignore[arg-type]


def synthetic_corpus() -> tuple[DocumentRecord, ...]:
    return (
        syn_doc("syn-doc-1", "synthetic alpha beta gamma provisions"),
        syn_doc("syn-doc-2", "synthetic alpha delta provisions"),
        syn_doc("syn-doc-3", "synthetic epsilon zeta provisions"),
    )


def synthetic_dataset() -> EvaluationSet:
    return EvaluationSet(
        dataset_id="synthetic-eval-v1",
        version="v1",
        synthetic=True,
        queries=(
            EvalQuery("syn-q-1", "alpha beta", ("syn-doc-1",)),
            EvalQuery("syn-q-2", "delta", ("syn-doc-2",)),
            EvalQuery("syn-q-3", "omega", ("syn-doc-3",)),
            EvalQuery("syn-q-4", "alpha", ()),
        ),
    )


def test_integration_with_retriever() -> None:
    docs, dataset = synthetic_corpus(), synthetic_dataset()
    report = evaluate(docs, dataset, k_values=(1, 2))

    assert report.schema_version == EVALUATION_SCHEMA_VERSION
    assert report.dataset_id == "synthetic-eval-v1"
    assert report.synthetic is True
    assert report.k_values == (1, 2)
    assert (report.query_count, report.included_count, report.excluded_count) == (4, 3, 1)

    by_id = {q.query_id: q for q in report.queries}
    assert by_id["syn-q-1"].reciprocal_rank == pytest.approx(1.0)
    assert by_id["syn-q-1"].recall_at == (pytest.approx(1.0), pytest.approx(1.0))
    assert by_id["syn-q-2"].reciprocal_rank == pytest.approx(1.0)
    assert by_id["syn-q-3"].reciprocal_rank == 0.0
    assert by_id["syn-q-3"].recall_at == (0.0, 0.0)
    assert by_id["syn-q-4"].included is False

    # Aggregates are exact means over included queries only.
    assert report.mrr == pytest.approx((1 + 1 + 0) / 3)
    assert list(report.recall_at_k) == approx(((1 + 1 + 0) / 3, (1 + 1 + 0) / 3))
    # Per-query audit matches the retriever's actual ranking.
    assert by_id["syn-q-1"].retrieved_ids == tuple(
        r.document_id for r in retrieve("alpha beta", docs, limit=2)
    )


def test_judgment_validation() -> None:
    with pytest.raises(EvaluationError, match="duplicate query"):
        EvaluationSet("syn-ds", (EvalQuery("q", "alpha", ("d1",)), EvalQuery("q", "beta", ("d2",))))
    with pytest.raises(EvaluationError, match="duplicate relevant"):
        EvalQuery("q", "alpha", ("d1", "d1"))
    with pytest.raises(EvaluationError):
        EvalQuery("", "alpha", ("d1",))
    with pytest.raises(EvaluationError):
        EvalQuery("q", "  ", ("d1",))
    with pytest.raises(EvaluationError):
        EvaluationSet("syn-ds", ("not-a-query",))  # type: ignore[arg-type]
    with pytest.raises(EvaluationError, match="no queries with relevance"):
        evaluate(synthetic_corpus(), EvaluationSet("syn-empty", (EvalQuery("q", "alpha", ()),)))


def test_deterministic_ordering_and_serialization() -> None:
    report = evaluate(synthetic_corpus(), synthetic_dataset(), k_values=(2, 1))
    assert report.k_values == (1, 2)
    first = json.dumps(report.to_dict(), sort_keys=True)
    second = json.dumps(evaluate(synthetic_corpus(), synthetic_dataset(), k_values=(1, 2)).to_dict(), sort_keys=True)
    assert first == second
    assert json.loads(first)["synthetic"] is True


def test_inputs_not_mutated() -> None:
    docs, dataset = synthetic_corpus(), synthetic_dataset()
    before_docs = [d.to_dict() for d in docs]
    before_queries = [(q.query_id, q.query_text, q.relevant_ids) for q in dataset.queries]
    evaluate(docs, dataset, k_values=(1, 2))
    assert [d.to_dict() for d in docs] == before_docs
    assert [(q.query_id, q.query_text, q.relevant_ids) for q in dataset.queries] == before_queries
    assert dataset == synthetic_dataset()
