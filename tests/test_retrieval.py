"""BM25 retrieval tests using SYNTHETIC documents only.

Nothing here is a real legal source or measures real retrieval quality.
Ranking behavior is proven on fixtures; no corpus, network, or external
service is involved.
"""

import math

import pytest

from backend.app.retrieval import (
    EXCERPT_MAX_CHARS,
    RetrievalError,
    retrieve,
    tokenize,
)
from backend.app.sources import DocumentRecord


def syn_doc(doc_id: str, text: str, source_id: str = "syn-source-1") -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        source_id=source_id,
        filename=f"{doc_id}.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash=f"syn-hash-{doc_id}",
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification="unverified",
    )


def corpus() -> tuple[DocumentRecord, ...]:
    return (
        syn_doc("syn-doc-tax",
                "synthetic income tax deduction provisions for salaried individuals"),
        syn_doc("syn-doc-agri",
                "synthetic agricultural crop yield monsoon report"),
        syn_doc("syn-doc-fee",
                "synthetic income tax filing fee and penalty provisions"),
    )


def test_exact_match_ranks_first() -> None:
    results = retrieve("salaried deduction", corpus())
    assert [r.document_id for r in results] == ["syn-doc-tax"]
    assert results[0].rank == 1


def test_repeated_query_terms_follow_set_semantics() -> None:
    assert retrieve("tax tax tax", corpus()) == retrieve("tax", corpus())


def test_case_insensitive_ranking() -> None:
    assert retrieve("TAX DEDUCTION", corpus()) == retrieve("tax deduction", corpus())
    assert tokenize("Tax, DEDUCTION!") == ("tax", "deduction")


def test_punctuation_and_unicode_deterministic() -> None:
    assert retrieve("tax, deduction!", corpus()) == retrieve("tax deduction", corpus())
    docs = (syn_doc("syn-doc-uni", "synthetic caf\u00e9 आयकर provisions"),) + corpus()
    first = retrieve("caf\u00e9 \u0906\u092f\u0915\u0930", docs)
    assert [r.document_id for r in first] == ["syn-doc-uni"]
    assert retrieve("caf\u00e9 \u0906\u092f\u0915\u0930", docs) == first


def test_empty_queries_return_empty() -> None:
    assert retrieve("", corpus()) == ()
    assert retrieve("   \t\n ", corpus()) == ()


def test_empty_corpus_returns_empty() -> None:
    assert retrieve("tax", ()) == ()


def test_tokenless_documents_excluded() -> None:
    docs = (
        syn_doc("syn-doc-blank", "   "),
        syn_doc("syn-doc-empty", ""),
        syn_doc("syn-doc-tax", "synthetic income tax provisions"),
    )
    results = retrieve("tax", docs)
    assert [r.document_id for r in results] == ["syn-doc-tax"]
    assert retrieve("tax", docs[:2]) == ()


def test_no_lexical_match_excluded() -> None:
    assert retrieve("quantum zebra", corpus()) == ()


def test_limits() -> None:
    docs = corpus()
    top = retrieve("synthetic", docs, limit=1)
    assert len(top) == 1 and top[0].rank == 1
    assert len(retrieve("synthetic", docs, limit=99)) == 3
    for bad in (0, -1, True, "2", 2.0):
        with pytest.raises(RetrievalError, match="limit"):
            retrieve("synthetic", docs, limit=bad)  # type: ignore[arg-type]


def test_deterministic_repeated_calls() -> None:
    assert retrieve("income tax", corpus()) == retrieve("income tax", corpus())


def test_ties_break_by_document_id() -> None:
    docs = (
        syn_doc("syn-doc-m2", "synthetic identical provisions text"),
        syn_doc("syn-doc-m1", "synthetic identical provisions text"),
    )
    results = retrieve("identical", docs)
    assert [r.document_id for r in results] == ["syn-doc-m1", "syn-doc-m2"]
    assert results[0].score == results[1].score


def test_duplicate_document_ids_rejected() -> None:
    docs = (syn_doc("syn-doc-x", "synthetic tax text"), syn_doc("syn-doc-x", "other text"))
    with pytest.raises(RetrievalError, match="duplicate"):
        retrieve("tax", docs)


def test_invalid_input_types_rejected() -> None:
    with pytest.raises(RetrievalError):
        retrieve(42, corpus())  # type: ignore[arg-type]
    with pytest.raises(RetrievalError):
        retrieve("tax", "syn-doc-tax")  # type: ignore[arg-type]
    with pytest.raises(RetrievalError):
        retrieve("tax", [corpus()[0], "not-a-record"])  # type: ignore[arg-type]


def test_provenance_preserved() -> None:
    doc = syn_doc("syn-doc-tax", "synthetic income tax provisions", source_id="syn-source-9")
    (result,) = retrieve("income", (doc,))
    assert result.document_id == "syn-doc-tax"
    assert result.source_id == "syn-source-9"
    assert result.content_hash == "syn-hash-syn-doc-tax"
    assert result.filename == "syn-doc-tax.txt"


def test_excerpt_bounded_anchored_and_scoreneutral() -> None:
    padding = "synthetic filler words " * 40
    text = padding + "rareterm anchor passage " + padding
    doc = syn_doc("syn-doc-long", text)
    (result,) = retrieve("rareterm", (doc,) + corpus())

    assert len(result.excerpt) <= EXCERPT_MAX_CHARS + 6
    assert "rareterm" in result.excerpt.lower()
    assert result.excerpt.startswith("...")
    assert result.excerpt.replace("...", "").strip() in text
    # Excerpt is display-only, derived after ranking: same corpus in,
    # identical score and excerpt out.
    again = retrieve("rareterm", (doc,) + corpus())
    assert (again[0].score, again[0].excerpt) == (result.score, result.excerpt)


def test_inputs_not_mutated() -> None:
    docs = corpus()
    before = [d.to_dict() for d in docs]
    retrieve("income tax provisions", docs)
    assert [d.to_dict() for d in docs] == before


def test_scores_are_raw_magnitudes_not_probabilities() -> None:
    results = retrieve("income tax provisions salaried", corpus())
    assert results
    for result in results:
        assert isinstance(result.score, float)
        assert math.isfinite(result.score) and result.score > 0
    assert [r.score for r in results] == sorted([r.score for r in results], reverse=True)
    narrow = retrieve("salaried", corpus())
    assert narrow[0].score != results[0].score  # scale depends on the query
