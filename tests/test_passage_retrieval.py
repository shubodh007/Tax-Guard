"""Passage-retrieval tests using SYNTHETIC fixtures, plus an opt-in
smoke test over the acquired First Schedule passages (skipped where
the artifact is absent). Scores are lexical signals only; nothing here
proves legal relevance or source authority.
"""

import dataclasses
import json
import os

import pytest

from backend.app.chunking import ChunkConfig, Passage, chunk_document
from backend.app.evaluation import score_ranking
from backend.app.retrieval import (
    PassageResult,
    RetrievalError,
    retrieve,
    retrieve_passages,
)
from backend.app.sources import DocumentRecord


def syn_doc(text: str, doc_id: str = "syn-doc-1") -> DocumentRecord:
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


def chunk(text: str, doc_id: str = "syn-doc-1", **config) -> tuple[Passage, ...]:
    return chunk_document(syn_doc(text, doc_id), ChunkConfig(**config))


def test_exact_and_partial_matches() -> None:
    passages = chunk(
        "Alpha beta gamma provisions.\n\nDelta epsilon zeta clauses.\n\nEta theta iota notes.",
        max_chars=35,
        overlap_chars=0,
    )
    assert len(passages) == 3
    exact = retrieve_passages("delta epsilon", passages)
    assert [r.passage_id for r in exact] == [passages[1].passage_id]
    partial = retrieve_passages("provisions clauses", passages)
    assert {r.passage_id for r in partial} == {passages[0].passage_id, passages[1].passage_id}


def test_no_match_and_empty_inputs() -> None:
    passages = chunk("Alpha beta.\n\nGamma delta.", max_chars=60, overlap_chars=0)
    assert retrieve_passages("quantum zebra", passages) == ()
    assert retrieve_passages("", passages) == ()
    assert retrieve_passages("   ", passages) == ()
    assert retrieve_passages("alpha", ()) == ()


def test_determinism_and_tie_break() -> None:
    first = chunk("Identical synthetic passage text here.", doc_id="syn-doc-m2")
    second = chunk("Identical synthetic passage text here.", doc_id="syn-doc-m1")
    results = retrieve_passages("identical", first + second)
    assert [r.passage_id for r in results] == [second[0].passage_id, first[0].passage_id]
    assert results[0].score == results[1].score
    assert retrieve_passages("identical", first + second) == results


def test_scoring_consistent_with_document_level() -> None:
    text = "synthetic alpha beta gamma"
    (doc_hit,) = retrieve("alpha", (syn_doc(text),))
    (passage_hit,) = retrieve_passages("alpha", chunk(text))
    assert passage_hit.score == doc_hit.score


def test_limits() -> None:
    passages = chunk(
        "One synthetic.\n\nTwo synthetic.\n\nThree synthetic.", max_chars=29, overlap_chars=0
    )
    top = retrieve_passages("synthetic", passages, limit=1)
    assert len(top) == 1 and top[0].rank == 1
    assert len(retrieve_passages("synthetic", passages, limit=99)) == 3
    for bad in (0, -1, True, "2"):
        with pytest.raises(RetrievalError, match="limit"):
            retrieve_passages("synthetic", passages, limit=bad)  # type: ignore[arg-type]


def test_provenance_preserved_verbatim() -> None:
    passages = chunk(
        "--- page 4 ---\n\nProvision text here.\n\n--- page 5 ---\n\nFurther text here.",
        max_chars=500,
        overlap_chars=0,
    )
    assert len(passages) == 1
    (result,) = retrieve_passages("provision", passages)
    source = passages[0]
    assert result.passage_id == source.passage_id
    assert result.document_id == source.document_id
    assert result.chunk_index == source.chunk_index
    assert result.text == source.text
    assert (result.char_start, result.char_end) == (source.char_start, source.char_end)
    assert (result.page_start, result.page_end) == (4, 5)
    assert result.parser_id == source.parser_id
    assert result.chunking_version == source.chunking_version
    assert (result.max_chars, result.overlap_chars) == (source.max_chars, source.overlap_chars)


def test_multiple_passages_and_documents() -> None:
    first = chunk("Alpha one.\n\nAlpha two.\n\nUnrelated.", max_chars=40, overlap_chars=0)
    second = chunk("Alpha three.\n\nOther.", doc_id="syn-doc-2", max_chars=40, overlap_chars=0)
    results = retrieve_passages("alpha", first + second)
    assert {r.document_id for r in results} == {"syn-doc-1", "syn-doc-2"}
    assert [r.rank for r in results] == list(range(1, len(results) + 1))


def test_duplicates_and_malformed_rejected() -> None:
    passages = chunk("Alpha beta.", max_chars=60, overlap_chars=0)
    with pytest.raises(RetrievalError, match="duplicate"):
        retrieve_passages("alpha", passages + passages)
    with pytest.raises(RetrievalError):
        retrieve_passages(42, passages)  # type: ignore[arg-type]
    with pytest.raises(RetrievalError):
        retrieve_passages("alpha", "not-a-collection")  # type: ignore[arg-type]
    with pytest.raises(RetrievalError):
        retrieve_passages("alpha", [passages[0], "not-a-passage"])  # type: ignore[arg-type]


def test_inputs_not_mutated() -> None:
    document = syn_doc("Alpha beta.\n\nGamma delta.")
    passages = chunk_document(document)
    before_doc = document.to_dict()
    before_passages = [dataclasses.asdict(p) for p in passages]
    retrieve_passages("alpha", passages)
    assert document.to_dict() == before_doc
    assert [dataclasses.asdict(p) for p in passages] == before_passages


def test_result_fields_exactly_documented() -> None:
    assert {f.name for f in dataclasses.fields(PassageResult)} == {
        "rank", "passage_id", "document_id", "chunk_index", "text",
        "char_start", "char_end", "page_start", "page_end", "parser_id",
        "chunking_version", "max_chars", "overlap_chars", "score",
    }


def test_evaluation_compatible_without_schema_change() -> None:
    passages = chunk("Alpha one.\n\nBeta two.", max_chars=40, overlap_chars=0)
    results = retrieve_passages("alpha", passages)
    ranked_ids = tuple(r.passage_id for r in results)
    scores = score_ranking((passages[0].passage_id,), ranked_ids, (1, 2))
    assert scores.reciprocal_rank == pytest.approx(1.0)
    assert list(scores.recall_at) == [pytest.approx(1.0), pytest.approx(1.0)]


REAL_DOCUMENT = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "india-code",
    "income-tax-act-1961-first-schedule", "document.json",
)


@pytest.mark.skipif(
    not os.path.exists(REAL_DOCUMENT),
    reason="acquired artifact absent from this checkout",
)
def test_first_schedule_passage_smoke() -> None:
    with open(REAL_DOCUMENT, encoding="utf-8") as handle:
        document = DocumentRecord(**json.load(handle))
    passages = chunk_document(document)
    assert len(passages) == 7
    lines = []
    for query in ("insurance", "schedule", "surplus"):
        results = retrieve_passages(query, passages, limit=3)
        top = results[0] if results else None
        lines.append(
            f"query={query!r} hits={len(results)} "
            + (
                f"top={top.passage_id} pages=({top.page_start},{top.page_end}) "
                f"recovered={top.text == document.normalized_text[top.char_start:top.char_end]}"
                if top
                else "no match"
            )
        )
        for result in results:
            assert result.text == document.normalized_text[result.char_start:result.char_end]
    print("\nsmoke:\n" + "\n".join(lines))
    assert retrieve_passages("insurance", passages, limit=3)
