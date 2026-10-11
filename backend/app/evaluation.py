"""Deterministic retrieval evaluation harness. Standard library only.

Scores ranked id listings against human-authored relevance judgments
with Recall@K, reciprocal rank / MRR, and Precision@K, then evaluates
the existing BM25 retrievers over labeled query sets into serializable
reports. Document-level (`EvaluationSet`/`evaluate`) and passage-level
(`PassageBenchmark`/`evaluate_passages`) contracts are separate types
sharing metric definitions — judgments for one must never be mixed
into the other. No LLM, database, network, frontend, or measured
real-corpus claims. Missing relevance labels mean unknown — never
irrelevant. Scores are never read: only ranks and ids matter. See
``docs/retrieval-evaluation-contract.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.chunking import Passage
from backend.app.retrieval import retrieve, retrieve_passages
from backend.app.sources import DocumentRecord

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "EvalQuery",
    "EvaluationError",
    "EvaluationReport",
    "EvaluationSet",
    "PassageBenchmark",
    "PassageQuery",
    "QueryResult",
    "RankingScores",
    "evaluate",
    "evaluate_passages",
    "score_ranking",
]

EVALUATION_SCHEMA_VERSION = 1

_DEFAULT_K_VALUES = (1, 3, 5)


class EvaluationError(ValueError):
    """Malformed evaluation input: bad judgments, duplicate ids, invalid
    K values, duplicate ranked ids, or a dataset with no judged query.
    Distinct from valid evaluations that simply measure zero."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_id_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise EvaluationError(f"{name} must be a tuple of non-empty strings")
    return tuple(values)


def _canonical_k_values(k_values: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    if not isinstance(k_values, (tuple, list)) or not k_values:
        raise EvaluationError("k_values must be a non-empty tuple or list of positive integers")
    for k in k_values:
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise EvaluationError(f"invalid K value {k!r}")
    return tuple(sorted(set(k_values)))


@dataclass(frozen=True)
class EvalQuery:
    """One labeled case: stable id, query text, and the human-authored
    relevant document ids. An empty relevant set is valid construction —
    such queries are excluded from metric denominators with a reported
    count, never scored as perfect recall."""

    query_id: str
    query_text: str
    relevant_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_label("query_id", self.query_id)
        _require_label("query_text", self.query_text)
        _require_id_tuple("relevant_ids", self.relevant_ids)
        if len(set(self.relevant_ids)) != len(self.relevant_ids):
            raise EvaluationError(f"duplicate relevant ids for query {self.query_id!r}")


@dataclass(frozen=True)
class EvaluationSet:
    """Versioned, labeled judgment set. `synthetic` defaults True so
    unlabeled data can never pass as real measurements."""

    dataset_id: str
    queries: tuple[EvalQuery, ...]
    version: str = "v1"
    synthetic: bool = True

    def __post_init__(self) -> None:
        _require_label("dataset_id", self.dataset_id)
        _require_label("version", self.version)
        if not isinstance(self.synthetic, bool):
            raise EvaluationError("synthetic must be a boolean")
        if any(not isinstance(q, EvalQuery) for q in self.queries):
            raise EvaluationError("queries must be a tuple of EvalQuery")
        ids = [q.query_id for q in self.queries]
        if len(set(ids)) != len(ids):
            raise EvaluationError("duplicate query ids")


@dataclass(frozen=True)
class PassageQuery:
    """One labeled passage case: stable id, query text, human-authored
    relevant passage ids, and a `basis` stating briefly why each passage
    is labeled relevant. `basis` is required whenever relevant passages
    exist; judgments must be authored from source text, never derived
    from the ranking being evaluated."""

    query_id: str
    query_text: str
    relevant_passage_ids: tuple[str, ...] = ()
    basis: str = ""

    def __post_init__(self) -> None:
        _require_label("query_id", self.query_id)
        _require_label("query_text", self.query_text)
        _require_id_tuple("relevant_passage_ids", self.relevant_passage_ids)
        if len(set(self.relevant_passage_ids)) != len(self.relevant_passage_ids):
            raise EvaluationError(f"duplicate relevant ids for query {self.query_id!r}")
        if not isinstance(self.basis, str):
            raise EvaluationError("basis must be a string")
        if self.relevant_passage_ids and not self.basis.strip():
            raise EvaluationError(f"query {self.query_id!r} labels relevant passages without a basis")


@dataclass(frozen=True)
class PassageBenchmark:
    """Versioned, labeled passage-judgment set. Deliberately distinct
    from `EvaluationSet` so document and passage judgments cannot mix."""

    dataset_id: str
    queries: tuple[PassageQuery, ...]
    version: str = "v1"
    synthetic: bool = True

    def __post_init__(self) -> None:
        _require_label("dataset_id", self.dataset_id)
        _require_label("version", self.version)
        if not isinstance(self.synthetic, bool):
            raise EvaluationError("synthetic must be a boolean")
        if any(not isinstance(q, PassageQuery) for q in self.queries):
            raise EvaluationError("queries must be a tuple of PassageQuery")
        ids = [q.query_id for q in self.queries]
        if len(set(ids)) != len(ids):
            raise EvaluationError("duplicate query ids")


@dataclass(frozen=True)
class RankingScores:
    """Metric values for one ranking. `first_relevant_rank` is 1-based,
    or 0 when no relevant document appears in the evaluated ranking."""

    recall_at: tuple[float, ...]
    precision_at: tuple[float, ...]
    reciprocal_rank: float
    first_relevant_rank: int


@dataclass(frozen=True)
class QueryResult:
    """Auditable per-query record: inputs echoed plus metric values.
    `included` is False for queries excluded from denominators."""

    query_id: str
    relevant_total: int
    retrieved_ids: tuple[str, ...]
    first_relevant_rank: int
    recall_at: tuple[float, ...]
    precision_at: tuple[float, ...]
    reciprocal_rank: float
    included: bool


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate report. Means run over included queries only; excluded
    queries are counted, never silently scored."""

    schema_version: int
    dataset_id: str
    dataset_version: str
    synthetic: bool
    k_values: tuple[int, ...]
    query_count: int
    included_count: int
    excluded_count: int
    recall_at_k: tuple[float, ...]
    precision_at_k: tuple[float, ...]
    mrr: float
    queries: tuple[QueryResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping with deterministic key order."""
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "synthetic": self.synthetic,
            "k_values": list(self.k_values),
            "query_count": self.query_count,
            "included_count": self.included_count,
            "excluded_count": self.excluded_count,
            "recall_at_k": list(self.recall_at_k),
            "precision_at_k": list(self.precision_at_k),
            "mrr": self.mrr,
            "queries": [
                {
                    "query_id": q.query_id,
                    "relevant_total": q.relevant_total,
                    "retrieved_ids": list(q.retrieved_ids),
                    "first_relevant_rank": q.first_relevant_rank,
                    "recall_at": list(q.recall_at),
                    "precision_at": list(q.precision_at),
                    "reciprocal_rank": q.reciprocal_rank,
                    "included": q.included,
                }
                for q in self.queries
            ],
        }


def score_ranking(
    relevant_ids: tuple[str, ...] | frozenset[str],
    ranked_ids: tuple[str, ...] | list[str],
    k_values: tuple[int, ...] | list[int],
) -> RankingScores:
    """Score one ranked id listing against a relevant set, independently
    of any retriever. Definitions (relevant total R > 0):

        Recall@K    = |relevant ∩ top-K| / R
        Precision@K = |relevant ∩ top-K| / K   (standard denominator K,
                      even when fewer than K ids were returned)
        RR          = 1 / first relevant rank, or 0 when absent

    Duplicate ranked ids are rejected: the same document must never
    count as multiple hits.
    """
    if not isinstance(relevant_ids, (tuple, frozenset)):
        raise EvaluationError("relevant_ids must be a tuple or frozenset of strings")
    relevant = frozenset(relevant_ids)
    if any(not isinstance(v, str) for v in relevant):
        raise EvaluationError("relevant_ids must contain only strings")
    if not isinstance(ranked_ids, (tuple, list)):
        raise EvaluationError("ranked_ids must be a tuple or list of strings")
    if any(not isinstance(v, str) for v in ranked_ids):
        raise EvaluationError("ranked_ids must contain only strings")
    if len(set(ranked_ids)) != len(ranked_ids):
        raise EvaluationError("duplicate document ids in ranking")
    canonical = _canonical_k_values(k_values)

    first_rank = 0
    for position, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant:
            first_rank = position
            break
    recall: list[float] = []
    precision: list[float] = []
    for k in canonical:
        hits = len([d for d in ranked_ids[:k] if d in relevant])
        recall.append(hits / len(relevant) if relevant else 0.0)
        precision.append(hits / k)
    return RankingScores(
        recall_at=tuple(recall),
        precision_at=tuple(precision),
        reciprocal_rank=1.0 / first_rank if first_rank else 0.0,
        first_relevant_rank=first_rank,
    )


def _aggregate(
    dataset_id: str,
    version: str,
    synthetic: bool,
    canonical: tuple[int, ...],
    results: list[QueryResult],
) -> EvaluationReport:
    """Mean metrics over included queries; excluded queries counted,
    never silently scored. Shared by document and passage evaluation."""
    included = [r for r in results if r.included]
    if not included:
        raise EvaluationError("dataset has no queries with relevance judgments")
    count = len(included)
    mean = lambda values: sum(values) / count
    return EvaluationReport(
        schema_version=EVALUATION_SCHEMA_VERSION,
        dataset_id=dataset_id,
        dataset_version=version,
        synthetic=synthetic,
        k_values=canonical,
        query_count=len(results),
        included_count=count,
        excluded_count=len(results) - count,
        recall_at_k=tuple(mean([r.recall_at[i] for r in included]) for i in range(len(canonical))),
        precision_at_k=tuple(
            mean([r.precision_at[i] for r in included]) for i in range(len(canonical))
        ),
        mrr=mean([r.reciprocal_rank for r in included]),
        queries=tuple(results),
    )


def _excluded(query_id: str, ranked_ids: tuple[str, ...], canonical: tuple[int, ...]) -> QueryResult:
    return QueryResult(
        query_id=query_id,
        relevant_total=0,
        retrieved_ids=ranked_ids,
        first_relevant_rank=0,
        recall_at=tuple(0.0 for _ in canonical),
        precision_at=tuple(0.0 for _ in canonical),
        reciprocal_rank=0.0,
        included=False,
    )


def _scored(
    query_id: str,
    relevant_ids: tuple[str, ...],
    ranked_ids: tuple[str, ...],
    canonical: tuple[int, ...],
) -> QueryResult:
    scores = score_ranking(relevant_ids, ranked_ids, canonical)
    return QueryResult(
        query_id=query_id,
        relevant_total=len(relevant_ids),
        retrieved_ids=ranked_ids,
        first_relevant_rank=scores.first_relevant_rank,
        recall_at=scores.recall_at,
        precision_at=scores.precision_at,
        reciprocal_rank=scores.reciprocal_rank,
        included=True,
    )


def evaluate(
    documents: tuple[DocumentRecord, ...] | list[DocumentRecord],
    dataset: EvaluationSet,
    *,
    k_values: tuple[int, ...] | list[int] = _DEFAULT_K_VALUES,
) -> EvaluationReport:
    """Run the existing BM25 retriever over every dataset query and
    aggregate metrics. Retrieval runs once per query at limit max(K);
    per-K values are prefixes of that single ranking. Inputs are never
    mutated. Queries without judgments are excluded from denominators
    and reported via `excluded_count`."""
    if not isinstance(dataset, EvaluationSet):
        raise EvaluationError("dataset must be an EvaluationSet")
    canonical = _canonical_k_values(k_values)
    top = canonical[-1]

    results: list[QueryResult] = []
    for query in dataset.queries:
        ranked = retrieve(query.query_text, documents, limit=top)
        ranked_ids = tuple(r.document_id for r in ranked)
        if not query.relevant_ids:
            results.append(_excluded(query.query_id, ranked_ids, canonical))
            continue
        results.append(_scored(query.query_id, query.relevant_ids, ranked_ids, canonical))

    return _aggregate(dataset.dataset_id, dataset.version, dataset.synthetic, canonical, results)


def evaluate_passages(
    passages: tuple[Passage, ...] | list[Passage],
    benchmark: PassageBenchmark,
    *,
    k_values: tuple[int, ...] | list[int] = _DEFAULT_K_VALUES,
) -> EvaluationReport:
    """Run the passage BM25 retriever over every benchmark query and
    aggregate metrics with the same definitions as document evaluation
    (identical denominators, eligibility, and exclusion accounting).
    Retrieval runs once per query at limit max(K); per-K values are
    prefixes of that single ranking. Inputs are never mutated. Document
    and passage judgments stay in their separate contracts: passing an
    `EvaluationSet` here (or a `PassageBenchmark` to `evaluate()`)
    raises."""
    if not isinstance(benchmark, PassageBenchmark):
        raise EvaluationError("benchmark must be a PassageBenchmark")
    canonical = _canonical_k_values(k_values)
    top = canonical[-1]

    results: list[QueryResult] = []
    for query in benchmark.queries:
        ranked = retrieve_passages(query.query_text, passages, limit=top)
        ranked_ids = tuple(r.passage_id for r in ranked)
        if not query.relevant_passage_ids:
            results.append(_excluded(query.query_id, ranked_ids, canonical))
            continue
        results.append(_scored(query.query_id, query.relevant_passage_ids, ranked_ids, canonical))

    return _aggregate(
        benchmark.dataset_id, benchmark.version, benchmark.synthetic, canonical, results
    )
