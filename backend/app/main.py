"""FastAPI entry point. Keeps startup lightweight: no DB, no models, no LLM clients."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.app.config import settings
from backend.app.corpus_store import LoadError, load_corpus_from_db
from backend.app.db import DbConfig, DbConnectionError, connect
from backend.app.manifest import (
    ManifestError,
    build_passages,
    load_manifest_file,
    resolve_within_root,
    verify_entry_binding,
)
from backend.app.provenance import resolve_provenance
from backend.app.registry import Registry
from backend.app.retrieval import retrieve_passages
from backend.app.source_verification import Assessment, VerificationCheck, canonical_policy
from backend.app.sources import DocumentRecord, SourceRecord

logger = logging.getLogger(__name__)

CORPUS_UNAVAILABLE = "corpus unavailable"

LIMITATIONS = (
    "TaxGuard's corpus holds only two unverified schedule fragments of the "
    "Income-tax Act, 1961. Retrieval is lexical keyword matching, not legal "
    "analysis; matches do not prove correctness, currency, or applicability. "
    "This is informational only and not professional tax advice."
)


class AskRequest(BaseModel):
    """Validated question payload. Blank questions and out-of-range
    limits fail with 422 before any retrieval runs."""

    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


def _load_verification_assessment(
    base_dir: str, artifact_path: str
) -> tuple[Assessment, list[VerificationCheck]]:
    """Load the binding assessment for a non-unverified entry from the
    sibling verification.json beside its artifact. Missing, unreadable,
    or malformed records raise: a verified label without its evidence
    fails the whole corpus load instead of serving weaker assurance."""
    sibling_rel = os.path.join(
        os.path.dirname(artifact_path), "verification.json"
    ).replace(os.sep, "/")
    sibling = resolve_within_root(base_dir, sibling_rel)
    with open(sibling, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("verification record must be a mapping")
    raw_assessment = data.get("assessment")
    if not isinstance(raw_assessment, dict):
        raise ValueError("verification record assessment must be a mapping")
    assessment = Assessment.from_dict(raw_assessment)
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list):
        raise ValueError("verification record checks must be a list")
    return assessment, [VerificationCheck.from_dict(item) for item in raw_checks]


def _load_corpus(manifest_path: str, base_dir: str) -> dict[str, Any]:
    """Validate the manifest and build the passage collection plus the
    record maps the answer path needs. Raises on any problem; callers
    convert failures into a controlled unavailable state, never a
    silent empty corpus."""
    manifest = load_manifest_file(manifest_path)
    passages = build_passages(manifest, base_dir)
    if not passages:
        raise ValueError("manifest built an empty passage collection")
    documents: dict[str, DocumentRecord] = {}
    sources: dict[str, SourceRecord] = {}
    entries = {entry.document_id: entry for entry in manifest.entries}
    for entry in manifest.entries:
        document_path = resolve_within_root(base_dir, entry.document_path)
        with open(document_path, encoding="utf-8") as handle:
            documents[entry.document_id] = DocumentRecord.from_dict(json.load(handle))
        if entry.source_verification != "unverified":
            assessment, checks = _load_verification_assessment(base_dir, entry.artifact_path)
            verify_entry_binding(entry, assessment, checks, policy=canonical_policy())
        companion_rel = os.path.join(
            os.path.dirname(entry.artifact_path), "source.json"
        ).replace(os.sep, "/")
        try:
            companion = resolve_within_root(base_dir, companion_rel)
        except ManifestError:
            continue
        try:
            with open(companion, encoding="utf-8") as handle:
                source = SourceRecord.from_dict(json.load(handle))
            if source.source_id == entry.source_id:
                sources[source.source_id] = source
        except (OSError, ValueError):
            # Companion is optional and best-effort per the question-API
            # contract: missing or malformed companions fall back to the
            # manifest snapshot, never fail the whole corpus. (SourceError
            # subclasses ValueError; JSON errors are ValueErrors too.)
            pass
    return {"manifest": manifest, "entries": entries, "passages": passages,
            "documents": documents, "sources": sources}


def _pages_text(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return "pages unknown"
    if page_end is None or page_end == page_start:
        return f"page {page_start}"
    return f"pages {page_start}-{page_end}"


def _load_corpus_from_db(config: DbConfig) -> dict[str, Any]:
    """Load the corpus from PostgreSQL/Neon rows into the same dict
    shape as :func:`_load_corpus`, so the answer path below is
    identical for both backends. Connection and load failures raise;
    messages are already redacted (no DSN, no secrets) by construction
    in :mod:`backend.app.db` and :mod:`backend.app.corpus_store`."""
    conn = connect(config)
    try:
        return load_corpus_from_db(conn)
    except LoadError:
        raise
    except DbConnectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - generic, never served
        raise DbConnectionError(f"database-backed corpus load failed: {type(exc).__name__}") from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass


def create_app(
    registry: Registry | None = None,
    *,
    manifest_path: str = "corpus/manifest.json",
    base_dir: str = ".",
    db: DbConfig | None = None,
) -> FastAPI:
    """Build the application with its own inspection registry and corpus.

    Each call creates isolated state unless supplied. Corpus loading is
    validated eagerly: failures are stored and served as 503 from
    `/ask`, so the app never runs on a silently empty corpus. Health
    and inspection endpoints are unaffected by corpus state.

    The default file-backed corpus is unchanged. Passing ``db`` opts
    into database-backed loading through the same answer path; any
    connection, load, or verification-gate failure lands in the same
    generic 503 (readiness never claims legal-source readiness merely
    because the database is reachable).
    """
    app = FastAPI(title=settings.app_title, version=settings.app_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.registry = registry if registry is not None else Registry()
    try:
        if db is not None:
            corpus = _load_corpus_from_db(db)
        else:
            corpus = _load_corpus(manifest_path, base_dir)
        corpus_error: str | None = None
    except Exception as exc:  # noqa: BLE001 - startup stays up; detail is logged, public 503 stays generic
        logger.warning("corpus load failed: %s", exc, exc_info=True)
        corpus, corpus_error = {}, CORPUS_UNAVAILABLE
    app.state.corpus = corpus
    app.state.corpus_error = corpus_error

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_title, "version": settings.app_version}

    @app.get("/ready")
    def ready() -> Any:
        if app.state.corpus_error is not None or not app.state.corpus.get("passages"):
            raise HTTPException(status_code=503, detail=CORPUS_UNAVAILABLE)
        return {
            "status": "ready",
            "snapshot_id": app.state.corpus["manifest"].snapshot_id,
        }

    def _lookup(kind: str, trace_id: str) -> Any:
        if not trace_id.strip():
            raise HTTPException(status_code=422, detail=f"invalid {kind} id")
        store: Registry = app.state.registry
        found = store.get_trace(trace_id) if kind == "trace" else store.get_answer(trace_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"{kind} {trace_id!r} not found")
        return found.to_dict()

    @app.get("/traces/{trace_id}")
    def get_trace(trace_id: str) -> Any:
        return _lookup("trace", trace_id)

    @app.get("/answers/{trace_id}")
    def get_answer(trace_id: str) -> Any:
        return _lookup("answer", trace_id)

    @app.post("/ask")
    def ask(request: AskRequest) -> Any:
        if app.state.corpus_error is not None or not app.state.corpus.get("passages"):
            raise HTTPException(status_code=503, detail=CORPUS_UNAVAILABLE)
        corpus_state: dict[str, Any] = app.state.corpus
        snapshot = corpus_state["manifest"].snapshot_id
        request_id = uuid.uuid4().hex[:16]
        hits = retrieve_passages(request.question, corpus_state["passages"], limit=request.limit)
        if not hits:
            return {
                "request_id": request_id,
                "status": "abstained",
                "reason_code": "no_matching_evidence",
                "answer": (
                    "No passages in the current corpus lexically match your question, "
                    "so TaxGuard abstains rather than guessing."
                ),
                "passages": [],
                "snapshot_id": snapshot,
                "limitations": LIMITATIONS,
            }
        items: list[dict[str, Any]] = []
        for hit in hits:
            resolved = resolve_provenance(
                hit, corpus_state["documents"], corpus_state["sources"], {}
            )
            entry = corpus_state["entries"][hit.document_id]
            source = corpus_state["sources"].get(resolved.source_id)
            items.append({
                "passage_id": resolved.passage_id,
                "document_id": resolved.document_id,
                "source_id": resolved.source_id,
                "chunk_index": resolved.chunk_index,
                "text": resolved.text,
                "char_start": resolved.char_start,
                "char_end": resolved.char_end,
                "page_start": resolved.page_start,
                "page_end": resolved.page_end,
                "score": resolved.score,
                "rank": resolved.rank,
                "content_hash": resolved.content_hash,
                "source_title": source.title if source is not None else "",
                "source_verification": entry.source_verification,
                "unresolved": list(resolved.unresolved),
            })
        top = items[0]
        distinct_sources = sorted({item["source_id"] for item in items})
        return {
            "request_id": request_id,
            "status": "retrieved",
            "reason_code": "lexical_match",
            "answer": (
                f"Found {len(items)} lexically matching passage(s). Top match "
                f"{top['passage_id']} ({_pages_text(top['page_start'], top['page_end'])}) "
                f"comes from source {top['source_id']}, recorded as "
                f"{top['source_verification']}. Sources used: "
                f"{', '.join(distinct_sources)}. A lexical match means the "
                "passage shares keywords with your question — it does not "
                "establish legal correctness. Informational, not tax advice."
            ),
            "passages": items,
            "snapshot_id": snapshot,
            "limitations": LIMITATIONS,
        }

    return app


app = create_app()
