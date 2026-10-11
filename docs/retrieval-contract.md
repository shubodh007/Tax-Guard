# TaxGuard Retrieval Contract (Phases 8 + 16)

Status: document-level and passage-level BM25 baselines. There is no
vector search, hybrid fusion, reranking, persistent index, retrieval
endpoint, or measured quality result against a verified legal corpus.
Anything labelled `synthetic-` is a test fixture with no legal force.

## Inputs and outputs

`retrieve(query, documents, *, limit=10)` (`backend/app/retrieval.py`):
- `query`: non-empty user string (empty/whitespace → empty result).
- `documents`: tuple/list of existing `DocumentRecord` objects (empty →
  empty result; duplicates or non-records → `RetrievalError`).
- `limit`: positive integer (anything else → `RetrievalError`).

Returns `tuple[RetrievalResult, ...]` with `rank` (1-based), `document_id`,
`source_id`, `filename`, `content_hash`, `score`, `excerpt`. Every result
retains the ingested record's identities verbatim; trust and verification
states are never modified by matching, and results are never converted
into `EvidenceRef`/`ClaimRef`/`AnswerResult` here.

## BM25 formula and parameters

Fixed constants `BM25_K1 = 1.5`, `BM25_B = 0.75`. Per query term `t` in
document `D` (corpus of `N` documents):

    idf(t)  = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
    term(t) = idf(t) * tf(t,D) * (k1+1) / (tf(t,D) + k1*(1-b+b*|D|/avgdl))

Score is the sum over the query's unique terms (set semantics: repeats
change nothing). Document length and average length are token counts;
statistics are computed per call from the supplied collection — no
mutable global index, no caching.

## Tokenization

Lowercased Unicode word tokens (`[^\W_]+`): case-insensitive,
punctuation- and underscore-splitting, Unicode-aware, deterministic.
No stop-word list, no stemming.

## Ordering, limits, edge behavior

Descending score; ties broken by ascending `document_id`; only positive
scores returned (no-match documents excluded); at most `limit` results.
Tokenless documents score nothing; an all-tokenless corpus (zero average
length) returns empty rather than dividing by zero.

## Excerpts

`excerpt` is a ≤240-character window anchored at the first
case-insensitive query-term occurrence (`...` markers when trimmed),
derived after ranking from the full text. Scoring always uses the
complete `normalized_text` — excerpts are display-only and never
truncated for scoring.

## Provenance and error guarantees

Inputs are never mutated. Error messages name identifiers, never source
text. Malformed inputs raise `RetrievalError`; valid-but-empty outcomes
return `()`.

## Passage retrieval (Phase 16)

`retrieve_passages(query, passages, *, limit=10)` ranks `Passage`
objects from `chunk_document()` with the same BM25 core: identical
formula, parameters, tokenizer, positive-only results, and tie-breaking
by ascending id (here `passage_id`). The scoring code is shared, not
duplicated — document-level behavior is unchanged.

Returns `tuple[PassageResult, ...]` with `rank`, `passage_id`,
`document_id`, `chunk_index`, full passage `text`, `char_start` /
`char_end`, `page_start` / `page_end`, `parser_id`,
`chunking_version`, `max_chars`, `overlap_chars`, and `score`.
Passage text is already bounded by chunking, so no separate excerpt is
needed. Every field is copied verbatim — pages, offsets, and chunking
provenance are never constructed or guessed. Passages carry no source
verification state, so none is reported; a high rank never promotes an
unverified source, and joining results back to source records is future
work. The existing evaluation harness scores passage-id rankings
through `score_ranking()` unchanged; document and passage judgments
must not be mixed.

## Limitations (read carefully)

Lexical matching cannot resolve synonyms, paraphrases, negation, or
legal meaning; relevance ≠ legal correctness. Scores are raw unbounded
sums — not probabilities, confidence, or proof. Fixture rankings prove
mechanics only: no Recall@K, MRR, or latency numbers exist, and none are
claimed. A labeled evaluation set with real-corpus metrics is future
work; no optimization (caching, concurrency, persistence) without
measured need.

## Synthetic example

```python
results = retrieve("salaried deduction", documents, limit=5)
# results[0] -> RetrievalResult(rank=1, document_id=..., source_id=...,
#                               score=..., excerpt=...)
```

Run with `pytest -q` (includes `tests/test_retrieval.py`,
`tests/test_passage_retrieval.py`).
