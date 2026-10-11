"""Minimal deterministic BM25 lexical retrieval baseline. Standard library only.

Ranks explicitly supplied `DocumentRecord` objects at document level
and `Passage` objects at passage level using the same Okapi BM25 core
over the full indexed text. No database, no network, no embeddings,
no reranking, no LLM, no persistent index, no mutable global state.
See `docs/retrieval-contract.md` for the formula, parameters, and
limits of lexical matching.

A result means only that a document or passage matched a query under
this ranking function. Scores are raw, unbounded BM25 sums — not
probabilities, confidence estimates, or proof of legal correctness.
Source verification state is independent of ranking and is never
altered by a match.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from backend.app.chunking import Passage
from backend.app.sources import DocumentRecord

__all__ = [
    "BM25_B",
    "BM25_K1",
    "EXCERPT_MAX_CHARS",
    "PassageResult",
    "RetrievalError",
    "RetrievalResult",
    "retrieve",
    "retrieve_passages",
    "tokenize",
]

# Okapi BM25 saturation and length-normalization parameters.
BM25_K1 = 1.5
BM25_B = 0.75

# Upper bound on excerpt length in characters (ellipsis markers extra).
EXCERPT_MAX_CHARS = 240

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


class RetrievalError(ValueError):
    """Malformed retrieval input: wrong types, duplicate document ids,
    or invalid result limits. Distinct from valid queries that simply
    match nothing (which return an empty tuple)."""


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase Unicode word tokens; punctuation and underscores split.
    Deterministic: same input always yields the same tuple."""
    return tuple(_TOKEN_PATTERN.findall(text.lower()))


@dataclass(frozen=True)
class RetrievalResult:
    """One ranked hit. `score` is the raw BM25 sum over the full
    document text. `excerpt` is a bounded query-anchored window for
    display only and never influences scoring."""

    rank: int
    document_id: str
    source_id: str
    filename: str
    content_hash: str
    score: float
    excerpt: str


@dataclass(frozen=True)
class PassageResult:
    """One ranked passage hit. `score` is the raw BM25 sum over the full
    passage text (same formula and parameters as document ranking).
    Identity, offsets, pages, and chunking provenance are copied
    verbatim from the passage — never constructed or guessed. A passage
    carries no source verification state, so none is reported; joining
    back to source records is future work, not done here."""

    rank: int
    passage_id: str
    document_id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    parser_id: str
    chunking_version: str
    max_chars: int
    overlap_chars: int
    score: float


def _ranked(
    query_terms: frozenset[str], ids: list[str], token_lists: list[tuple[str, ...]]
) -> list[tuple[float, int]]:
    """Shared Okapi BM25 core. Scores each token list against the query
    terms with corpus statistics from the supplied collection, and
    returns (score, index) pairs for positive totals, ordered by
    descending score with ties broken by ascending id. Empty when
    nothing matches or the corpus has no tokens."""
    doc_freq: Counter[str] = Counter()
    for tokens in token_lists:
        doc_freq.update(set(tokens))
    total_length = sum(len(t) for t in token_lists)
    avg_length = total_length / len(token_lists) if token_lists else 0
    if avg_length == 0:
        return []
    scored: list[tuple[float, str, int]] = []
    for index, tokens in enumerate(token_lists):
        term_freq = Counter(tokens)
        total = 0.0
        for term in query_terms:
            freq = term_freq.get(term, 0)
            if freq == 0:
                continue
            idf = math.log(1.0 + (len(token_lists) - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            total += idf * freq * (BM25_K1 + 1.0) / (
                freq + BM25_K1 * (1.0 - BM25_B + BM25_B * len(tokens) / avg_length)
            )
        if total > 0.0:
            scored.append((total, ids[index], index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(score, index) for score, _, index in scored]


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise RetrievalError("limit must be a positive integer")
    return limit


def _excerpt(text: str, query_terms: frozenset[str]) -> str:
    lowered = text.lower()
    anchor = -1
    for term in sorted(query_terms):
        pos = lowered.find(term)
        if pos != -1 and (anchor == -1 or pos < anchor):
            anchor = pos
    if anchor == -1:
        window = text[:EXCERPT_MAX_CHARS]
        return window + ("..." if len(text) > EXCERPT_MAX_CHARS else "")
    start = max(0, anchor - EXCERPT_MAX_CHARS // 3)
    end = min(len(text), start + EXCERPT_MAX_CHARS)
    start = max(0, end - EXCERPT_MAX_CHARS)
    window = text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{window}{suffix}"


def retrieve(
    query: str,
    documents: tuple[DocumentRecord, ...] | list[DocumentRecord],
    *,
    limit: int = 10,
) -> tuple[RetrievalResult, ...]:
    """Rank `documents` for `query`, best first.

    Scoring (per query term t in document D, corpus of N documents):

        idf(t)  = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
        term(t) = idf(t) * tf(t,D) * (k1 + 1) / (tf(t,D) + k1 * (1 - b + b * |D| / avgdl))

    Query terms are deduplicated (set semantics): repeating a term does
    not change the score. Only documents with a positive total are
    returned, ordered by descending score with ties broken by ascending
    `document_id`. At most `limit` results are returned.
    """
    if not isinstance(query, str):
        raise RetrievalError("query must be a string")
    if not isinstance(documents, (tuple, list)):
        raise RetrievalError("documents must be a tuple or list of DocumentRecord")
    if any(not isinstance(d, DocumentRecord) for d in documents):
        raise RetrievalError("documents must contain only DocumentRecord items")
    _validate_limit(limit)

    ids = [d.document_id for d in documents]
    if len(set(ids)) != len(ids):
        raise RetrievalError("duplicate document identifiers")

    query_terms = frozenset(tokenize(query))
    if not query_terms or not documents:
        return ()

    ranked = _ranked(query_terms, ids, [tokenize(d.normalized_text) for d in documents])
    results: list[RetrievalResult] = []
    for rank, (score, index) in enumerate(ranked[:limit], start=1):
        doc = documents[index]
        results.append(
            RetrievalResult(
                rank=rank,
                document_id=doc.document_id,
                source_id=doc.source_id,
                filename=doc.filename,
                content_hash=doc.content_hash,
                score=score,
                excerpt=_excerpt(doc.normalized_text, query_terms),
            )
        )
    return tuple(results)


def retrieve_passages(
    query: str,
    passages: tuple[Passage, ...] | list[Passage],
    *,
    limit: int = 10,
) -> tuple[PassageResult, ...]:
    """Rank `passages` for `query`, best first, using the same BM25 core
    as document retrieval (identical formula, parameters, tokenizer,
    and tie-breaking by ascending id — here `passage_id`).

    Only passages with a positive total are returned. All identity,
    offset, page, and chunking fields are copied verbatim from each
    passage. Scores are lexical signals, not legal assessments.
    """
    if not isinstance(query, str):
        raise RetrievalError("query must be a string")
    if not isinstance(passages, (tuple, list)):
        raise RetrievalError("passages must be a tuple or list of Passage")
    if any(not isinstance(p, Passage) for p in passages):
        raise RetrievalError("passages must contain only Passage items")
    _validate_limit(limit)

    ids = [p.passage_id for p in passages]
    if len(set(ids)) != len(ids):
        raise RetrievalError("duplicate passage identifiers")

    query_terms = frozenset(tokenize(query))
    if not query_terms or not passages:
        return ()

    ranked = _ranked(query_terms, ids, [tokenize(p.text) for p in passages])
    results: list[PassageResult] = []
    for rank, (score, index) in enumerate(ranked[:limit], start=1):
        passage = passages[index]
        results.append(
            PassageResult(
                rank=rank,
                passage_id=passage.passage_id,
                document_id=passage.document_id,
                chunk_index=passage.chunk_index,
                text=passage.text,
                char_start=passage.char_start,
                char_end=passage.char_end,
                page_start=passage.page_start,
                page_end=passage.page_end,
                parser_id=passage.parser_id,
                chunking_version=passage.chunking_version,
                max_chars=passage.max_chars,
                overlap_chars=passage.overlap_chars,
                score=score,
            )
        )
    return tuple(results)
