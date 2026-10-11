"""Passage-to-source provenance bridge. Standard library only.

Resolves a retrieved passage (`Passage` or `PassageResult`) through
caller-supplied id-keyed mappings to its `DocumentRecord`, `SourceRecord`,
and source-verification `Assessment`, producing one immutable
`ResolvedProvenance` record plus an `EvidenceRef` conversion for
downstream trace construction. Exact-id lookups only: nothing is
guessed from filenames, titles, or ranks. Unresolvable links are named
explicitly in the result, never fabricated. Verification state is
retained verbatim and never promoted by a match. See the provenance
bridge section of ``docs/evidence-trace-contract.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from backend.app.chunking import Passage
from backend.app.evidence import EVIDENCE_TRUST, EvidenceRef
from backend.app.retrieval import PassageResult
from backend.app.source_verification import Assessment, CategoryVerdict
from backend.app.sources import DocumentRecord, SourceRecord

__all__ = [
    "ProvenanceError",
    "ResolvedProvenance",
    "evidence_ref_for",
    "resolve_provenance",
]


class ProvenanceError(ValueError):
    """Malformed resolver input: wrong types or malformed mappings.
    Distinct from unresolvable links, which are reported inside the
    result rather than raised."""


@dataclass(frozen=True)
class ResolvedProvenance:
    """Provenance for one passage. `unresolved` names, in canonical
    order, every link that could not be established ("document",
    "source", "assessment"); empty means fully resolved. Fields for
    unresolvable links stay empty strings, zero, or None — a bare
    `Passage` input likewise yields `score`/`rank` of None rather than
    invented numbers."""

    passage_id: str
    document_id: str
    source_id: str
    text: str
    chunk_index: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    parser_id: str
    chunking_version: str
    max_chars: int
    overlap_chars: int
    score: float | None
    rank: int | None
    content_hash: str
    source_title: str
    source_publisher: str
    source_origin: str
    assessment_status: str
    verdicts: tuple[CategoryVerdict, ...]
    unresolved: tuple[str, ...]


_K = TypeVar("_K")
_V = TypeVar("_V")


def _require_mapping(name: str, value: Mapping[_K, _V] | object) -> Mapping[_K, _V]:
    if not isinstance(value, Mapping):
        raise ProvenanceError(f"{name} must be an id-keyed mapping")
    return value  # type: ignore[return-value]


def resolve_provenance(
    passage: Passage | PassageResult,
    documents: Mapping[str, DocumentRecord],
    sources: Mapping[str, SourceRecord],
    assessments: Mapping[str, Assessment] | None = None,
) -> ResolvedProvenance:
    """Join a passage to its document, source, and assessment by exact
    ids: passage → document by `document_id`, document → source by its
    `source_id`, source → assessment by the same id. Inputs are never
    mutated. Deterministic: identical inputs always yield the identical
    result."""
    if not isinstance(passage, (Passage, PassageResult)):
        raise ProvenanceError("passage must be a Passage or PassageResult")
    documents = _require_mapping("documents", documents)
    sources = _require_mapping("sources", sources)
    assessment_map: Mapping[str, Assessment] = (
        _require_mapping("assessments", assessments) if assessments is not None else {}
    )

    unresolved: list[str] = []
    document = documents.get(passage.document_id)
    if document is None:
        unresolved.append("document")
    elif not isinstance(document, DocumentRecord):
        raise ProvenanceError(f"documents[{passage.document_id!r}] must be a DocumentRecord")

    source: SourceRecord | None = None
    if document is None:
        unresolved.append("source")
    else:
        source = sources.get(document.source_id)
        if source is None:
            unresolved.append("source")
        elif not isinstance(source, SourceRecord):
            raise ProvenanceError(f"sources[{document.source_id!r}] must be a SourceRecord")

    assessment: Assessment | None = None
    if source is None:
        unresolved.append("assessment")
    else:
        assessment = assessment_map.get(source.source_id)
        if assessment is None:
            unresolved.append("assessment")
        elif not isinstance(assessment, Assessment):
            raise ProvenanceError(
                f"assessments[{source.source_id!r}] must be an Assessment"
            )

    if isinstance(passage, PassageResult):
        score: float | None = passage.score
        rank: int | None = passage.rank
    else:
        score, rank = None, None

    return ResolvedProvenance(
        passage_id=passage.passage_id,
        document_id=passage.document_id,
        source_id=source.source_id if source is not None else "",
        text=passage.text,
        chunk_index=passage.chunk_index,
        char_start=passage.char_start,
        char_end=passage.char_end,
        page_start=passage.page_start,
        page_end=passage.page_end,
        parser_id=passage.parser_id,
        chunking_version=passage.chunking_version,
        max_chars=passage.max_chars,
        overlap_chars=passage.overlap_chars,
        score=score,
        rank=rank,
        content_hash=document.content_hash if document is not None else "",
        source_title=source.title if source is not None else "",
        source_publisher=source.publisher if source is not None else "",
        source_origin=source.origin if source is not None else "",
        assessment_status=assessment.status if assessment is not None else "",
        verdicts=assessment.verdicts if assessment is not None else (),
        unresolved=tuple(unresolved),
    )


def evidence_ref_for(resolved: ResolvedProvenance, trust: str = "unverified") -> EvidenceRef:
    """Build an `EvidenceRef` from resolved provenance for downstream
    trace construction. Identity comes from the linked records
    (`document_id`, `passage_id`, document `content_hash` as digest,
    source origin as uri — empty when unknown). `trust` is an explicit
    caller label, never derived from retrieval score or rank: a match
    confers no authority."""
    if not isinstance(resolved, ResolvedProvenance):
        raise ProvenanceError("resolved must be a ResolvedProvenance")
    if trust not in EVIDENCE_TRUST:
        raise ProvenanceError(f"unknown trust status {trust!r}")
    return EvidenceRef(
        evidence_id=resolved.passage_id,
        document_id=resolved.document_id,
        passage_id=resolved.passage_id,
        uri=resolved.source_origin,
        digest=resolved.content_hash,
        trust=trust,
    )
