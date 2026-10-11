"""Provenance-preserving corpus persistence over PostgreSQL/Neon. Standard library only.

The database is storage, never authorization: rows carry verification
labels verbatim, and every non-unverified entry is re-authorized
through :func:`manifest.verify_entry_binding` under the pinned
canonical policy at load — the same gate as the file-backed path. A
row marked ``verified`` without independently trusted evidence fails
exactly as a file would. Importing records never makes them legally
authoritative.

Design notes:

- Retrieval runs the same Python BM25 core over rows loaded from the
  database (bounded SELECT statements, indexed by document). Scoring code
  is not duplicated in SQL, so file-backed and database-backed
  rankings agree by construction; parity tests prove it.
- Artifact bytes are streamed in chunks for hashing (large PDFs are
  never loaded wholly into memory).
- Import validates every record before writing anything, then writes
  once inside a single transaction: retries are safe, partial imports
  roll back, and conflicting rows raise instead of overwriting.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from backend.app.chunking import Passage, chunk_document
from backend.app.db import DbConnectionError
from backend.app.manifest import (
    CorpusManifest,
    ManifestEntry,
    ManifestError,
    build_manifest,
    entry_for,
    entry_for_verified,
    verify_entry_binding,
)
from backend.app.source_verification import (
    Assessment,
    CategoryVerdict,
    VerificationCheck,
    assessment_digest,
    canonical_policy,
)
from backend.app.sources import DocumentRecord, SourceRecord

__all__ = [
    "ImportConflictError",
    "ImportError",
    "ImportReport",
    "LoadError",
    "collect_file_records",
    "import_corpus",
    "load_corpus_from_db",
]

_CHUNK_BYTES = 1024 * 1024


class ImportError(ValueError):
    """A corpus record cannot be migrated faithfully: missing files,
    malformed records, hash mismatch, or conflicting rows. The import
    writes nothing in this case."""


class ImportConflictError(ImportError):
    """An existing database row disagrees with the file record.
    Refusing to overwrite; resolve manually, never silently."""


class LoadError(RuntimeError):
    """Database-backed corpus load failed: missing relationships,
    snapshot drift, or a failed verification gate. Callers serve a
    generic unavailable state, never the detail."""


@dataclass(frozen=True)
class FileRecords:
    """One source directory's file records, validated in memory."""

    source: SourceRecord
    document: DocumentRecord
    assessment: Assessment
    checks: tuple[VerificationCheck, ...]
    receipt: dict
    artifact_rel: str
    document_rel: str
    artifact_abs: str
    passage_count: int


@dataclass(frozen=True)
class ImportReport:
    """Outcome of :func:`import_corpus` (including dry runs, which
    change nothing)."""

    dry_run: bool
    sources: int
    documents: int
    passages: int
    snapshot_id: str
    skipped_identical: int


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ImportError(f"missing corpus file: {path}") from None
    except (OSError, ValueError) as exc:
        raise ImportError(f"unreadable corpus file {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ImportError(f"corpus file must hold a mapping: {path}")
    return data


def _stream_sha256(path: str) -> tuple[str, int]:
    """Hash a file in 1 MiB chunks. Returns (hex digest, byte size)."""
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise ImportError(f"cannot read artifact {path}: {exc}") from None
    return digest.hexdigest(), size


def collect_file_records(corpus_dir: str, *, root: str = ".") -> list[FileRecords]:
    """Read and validate every source directory under ``corpus_dir``:
    source/document/verification JSON, exactly one receipt, and the
    artifact bytes whose streamed hash must equal both the receipt and
    the document record. ``root`` is the repository root that stored
    artifact/document references are relative to (so rebuilt manifest
    entries carry the same relative paths as the committed manifest).
    Pure file reads; no database involved. Any record that cannot be
    migrated faithfully raises :class:`ImportError` identifying the
    record — nothing is invented to fill gaps."""
    found: list[FileRecords] = []
    try:
        names = sorted(os.listdir(corpus_dir))
    except OSError as exc:
        raise ImportError(f"cannot list corpus directory {corpus_dir}: {exc}") from None
    for name in names:
        directory = os.path.join(corpus_dir, name)
        if not os.path.isdir(directory):
            continue
        document_path = os.path.join(directory, "document.json")
        if not os.path.exists(document_path):
            continue  # not a source directory; the manifest builder agrees
        rel = os.path.relpath(directory, root).replace(os.sep, "/")
        try:
            source = SourceRecord.from_dict(_read_json(os.path.join(directory, "source.json")))
            document = DocumentRecord.from_dict(_read_json(document_path))
            payload = _read_json(os.path.join(directory, "verification.json"))
            raw_assessment = payload.get("assessment")
            if not isinstance(raw_assessment, dict):
                raise ImportError(f"verification assessment must be a mapping: {directory}")
            assessment = Assessment.from_dict(raw_assessment)
            raw_checks = payload.get("checks")
            if not isinstance(raw_checks, list):
                raise ImportError(f"verification checks must be a list: {directory}")
            checks = tuple(VerificationCheck.from_dict(item) for item in raw_checks)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ImportError(f"malformed record in {directory}: {exc}") from None
        receipts = glob.glob(os.path.join(directory, "*.receipt.json"))
        if len(receipts) != 1:
            raise ImportError(
                f"expected exactly one acquisition receipt in {directory}, "
                f"found {len(receipts)}"
            )
        receipt = _read_json(receipts[0])
        artifact_abs = os.path.join(directory, document.filename)
        if not os.path.isfile(artifact_abs):
            raise ImportError(f"missing artifact file: {artifact_abs}")
        digest, size = _stream_sha256(artifact_abs)
        if digest != receipt.get("sha256"):
            raise ImportError(
                f"artifact hash mismatch for {document.document_id}: "
                f"stored bytes do not match the receipt"
            )
        if digest != document.content_hash:
            raise ImportError(
                f"artifact hash mismatch for {document.document_id}: "
                f"stored bytes do not match the document record"
            )
        if size != receipt.get("size_bytes") or size != document.size_bytes:
            raise ImportError(
                f"byte-length mismatch for {document.document_id}: artifact "
                f"{size} vs receipt {receipt.get('size_bytes')} vs record "
                f"{document.size_bytes}"
            )
        if document.source_id != source.source_id:
            raise ImportError(
                f"document {document.document_id} belongs to "
                f"{document.source_id}, not {source.source_id}"
            )
        if assessment.source_id != source.source_id:
            raise ImportError(
                f"assessment in {directory} belongs to "
                f"{assessment.source_id}, not {source.source_id}"
            )
        passages = chunk_document(document)
        found.append(
            FileRecords(
                source=source,
                document=document,
                assessment=assessment,
                checks=checks,
                receipt=receipt,
                artifact_rel=f"{rel}/{document.filename}",
                document_rel=f"{rel}/document.json",
                artifact_abs=artifact_abs,
                passage_count=len(passages),
            )
        )
    if not found:
        raise ImportError(f"no source records found under {corpus_dir}")
    return found


def _entry_for(records: FileRecords) -> ManifestEntry:
    if records.document.source_verification == "unverified":
        return entry_for(
            records.document,
            artifact_path=records.artifact_rel,
            document_path=records.document_rel,
        )
    from backend.app.manifest import entry_for_verified

    return entry_for_verified(
        records.document,
        assessment=records.assessment,
        checks=list(records.checks),
        policy=canonical_policy(),
        artifact_path=records.artifact_rel,
        document_path=records.document_rel,
    )


def _row_exists(cur: Any, table: str, key_column: str, key: str) -> dict | None:
    cur.execute(
        f"SELECT * FROM {table} WHERE {key_column} = %s", (key,)  # noqa: S608 - fixed identifiers
    )
    row = cur.fetchone()
    if row is None:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


def import_corpus(conn: Any, corpus_dir: str, *, root: str = ".", dry_run: bool = False) -> ImportReport:
    """Migrate validated file records into the database.

    All records are validated (reads + streamed hashing, no writes)
    before anything is written; writes then happen in one transaction.
    Existing identical rows are skipped (idempotent retries);
    conflicting rows raise :class:`ImportConflictError` without
    overwriting. ``dry_run`` performs validation and reporting with
    zero ``INSERT``/``UPDATE`` statements. Any failure rolls back and
    raises :class:`ImportError`; partial imports never persist.
    """
    records = collect_file_records(corpus_dir, root=root)
    entries = tuple(_entry_for(item) for item in records)
    manifest = build_manifest(entries)
    run_id = uuid.uuid4().hex
    report = ImportReport(
        dry_run=dry_run,
        sources=len(records),
        documents=len(records),
        passages=sum(item.passage_count for item in records),
        snapshot_id=manifest.snapshot_id,
        skipped_identical=0,
    )
    if dry_run:
        return report
    skipped = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_runs (run_id, snapshot_id, sources, documents, "
                "passages, status) VALUES (%s, %s, %s, %s, %s, 'started')",
                (run_id, manifest.snapshot_id, report.sources, report.documents,
                 report.passages),
            )
            for item in records:
                skipped += _import_source(cur, item)
            existing_snapshot = _meta_get(cur, "snapshot_id")
            if existing_snapshot is None:
                _meta_put(cur, "snapshot_id", manifest.snapshot_id)
            elif existing_snapshot != manifest.snapshot_id:
                raise ImportConflictError(
                    "stored snapshot differs from the corpus snapshot: "
                    "the corpus changed since the last import; reconcile "
                    "before continuing"
                )
            cur.execute(
                "UPDATE ingestion_runs SET status = 'completed' WHERE run_id = %s",
                (run_id,),
            )
        conn.commit()
    except (ImportError, ManifestError):
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
        raise
    except Exception as exc:  # noqa: BLE001 - wrapped, never leaks driver detail
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
        raise DbConnectionError(f"corpus import failed: {type(exc).__name__}") from None
    return ImportReport(
        dry_run=False,
        sources=report.sources,
        documents=report.documents,
        passages=report.passages,
        snapshot_id=report.snapshot_id,
        skipped_identical=skipped,
    )


def _meta_get(cur: Any, key: str) -> str | None:
    cur.execute("SELECT value FROM corpus_meta WHERE key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _meta_put(cur: Any, key: str, value: str) -> None:
    cur.execute(
        "INSERT INTO corpus_meta (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO NOTHING",
        (key, value),
    )


def _import_source(cur: Any, item: FileRecords) -> int:
    """Import one source directory. Returns 1 when every row already
    existed identically (full skip), else 0. Raises on any conflict."""
    source, document = item.source, item.document
    existing_source = _row_exists(cur, "sources", "source_id", source.source_id)
    existing_document = _row_exists(cur, "documents", "document_id", document.document_id)
    if existing_source is not None or existing_document is not None:
        if (
            existing_source is None
            or existing_document is None
            or existing_source.get("title") != source.title
            or existing_source.get("verification") != source.verification
            or existing_document.get("content_hash") != document.content_hash
            or existing_document.get("source_verification") != document.source_verification
        ):
            raise ImportConflictError(
                f"conflicting database rows for {source.source_id}: refusing to overwrite"
            )
        return 1
    cur.execute(
        "INSERT INTO sources (source_id, title, publisher, origin, source_type, "
        "jurisdiction, publication, acquired_at, note, verification, receipt) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (source.source_id, source.title, source.publisher, source.origin,
         source.source_type, source.jurisdiction, source.publication,
         source.acquired_at, source.note, source.verification,
         json.dumps(item.receipt)),
    )
    digest = (
        ""
        if document.source_verification == "unverified"
        else assessment_digest(item.assessment)
    )
    entry = _entry_for(item)
    cur.execute(
        "INSERT INTO documents (document_id, source_id, filename, format, parser_id, "
        "content_hash, size_bytes, normalized_text, extraction_status, "
        "source_verification, assessment_digest, artifact_ref, document_ref, "
        "chunking_version, max_chars, overlap_chars) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (document.document_id, document.source_id, document.filename, document.format,
         document.parser_id, document.content_hash, document.size_bytes,
         document.normalized_text, document.extraction_status,
         document.source_verification, digest, item.artifact_rel, item.document_rel,
         entry.chunking_version, entry.max_chars, entry.overlap_chars),
    )
    for passage in chunk_document(document):
        cur.execute(
            "INSERT INTO passages (passage_id, document_id, chunk_index, text, "
            "char_start, char_end, page_start, page_end, parser_id, "
            "chunking_version, max_chars, overlap_chars) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (passage.passage_id, passage.document_id, passage.chunk_index, passage.text,
             passage.char_start, passage.char_end, passage.page_start, passage.page_end,
             passage.parser_id, passage.chunking_version, passage.max_chars,
             passage.overlap_chars),
        )
    cur.execute(
        "INSERT INTO assessments (assessment_digest, source_id, policy_id, "
        "policy_version, status, reason_codes, check_ids, verdicts) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (assessment_digest) DO NOTHING",
        (assessment_digest(item.assessment), item.assessment.source_id,
         item.assessment.policy_id, item.assessment.policy_version,
         item.assessment.status, list(item.assessment.reason_codes),
         list(item.assessment.check_ids),
         json.dumps([{"category": v.category, "verdict": v.verdict}
                     for v in item.assessment.verdicts])),
    )
    for check in item.checks:
        cur.execute(
            "INSERT INTO checks (check_id, source_id, check_type, outcome, description, "
            "observation, limitations, content_hash, checked_at, verifier, "
            "observation_source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (check_id) DO NOTHING",
            (check.check_id, check.source_id, check.check_type, check.outcome,
             check.description, check.observation, check.limitations,
             check.content_hash, check.checked_at, check.verifier,
             check.observation_source),
        )
    return 0


def load_corpus_from_db(conn: Any) -> dict:
    """Load the corpus from database rows into the exact dict shape
    ``main._load_corpus`` returns (manifest, entries, passages,
    documents, sources), so the answer path below is identical for
    both backends. Manifest entries are reconstructed from stored
    fields; the snapshot is recomputed and must equal the stored
    snapshot (drift fails the load). Every non-unverified entry is
    re-authorized through :func:`manifest.verify_entry_binding` under
    the pinned canonical policy using the stored assessment — the same
    gate as the file path. Any problem raises :class:`LoadError`;
    callers serve a generic unavailable state."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT document_id, source_id, filename, format, parser_id, "
                "content_hash, size_bytes, normalized_text, extraction_status, "
                "source_verification, assessment_digest, artifact_ref, document_ref, "
                "chunking_version, max_chars, overlap_chars "
                "FROM documents ORDER BY document_id"
            )
            doc_rows = cur.fetchall()
            doc_columns = [desc[0] for desc in cur.description]
            cur.execute(
                "SELECT passage_id, document_id, chunk_index, text, char_start, "
                "char_end, page_start, page_end, parser_id, chunking_version, "
                "max_chars, overlap_chars FROM passages "
                "ORDER BY document_id, chunk_index"
            )
            passage_rows = cur.fetchall()
            passage_columns = [desc[0] for desc in cur.description]
            cur.execute(
                "SELECT source_id, title, publisher, origin, source_type, jurisdiction, "
                "publication, acquired_at, note, verification FROM sources "
                "ORDER BY source_id"
            )
            source_rows = cur.fetchall()
            source_columns = [desc[0] for desc in cur.description]
            stored_snapshot = _meta_get(cur, "snapshot_id")
    except Exception as exc:  # noqa: BLE001 - wrapped below, detail never served
        raise LoadError("database-backed corpus load failed") from exc
    if not doc_rows:
        raise LoadError("database holds no documents")
    if stored_snapshot is None:
        raise LoadError("database holds no manifest snapshot")
    try:
        documents = {
            row["document_id"]: DocumentRecord(
                document_id=row["document_id"],
                source_id=row["source_id"],
                filename=row["filename"],
                format=row["format"],
                parser_id=row["parser_id"],
                content_hash=row["content_hash"],
                size_bytes=row["size_bytes"],
                normalized_text=row["normalized_text"],
                extraction_status=row["extraction_status"],
                source_verification=row["source_verification"],
            )
            for row in (dict(zip(doc_columns, r)) for r in doc_rows)
        }
        sources = {
            row["source_id"]: SourceRecord(
                source_id=row["source_id"],
                title=row["title"],
                publisher=row["publisher"],
                origin=row["origin"],
                source_type=row["source_type"],
                jurisdiction=row["jurisdiction"],
                publication=row["publication"],
                acquired_at=row["acquired_at"],
                note=row["note"],
                verification=row["verification"],
            )
            for row in (dict(zip(source_columns, r)) for r in source_rows)
        }
        by_doc = {row["document_id"]: row for row in (dict(zip(doc_columns, r)) for r in doc_rows)}
        entries = {}
        manifest_entries = []
        for document_id, row in sorted(by_doc.items()):
            count = sum(1 for r in passage_rows if dict(zip(passage_columns, r))["document_id"] == document_id)
            entry = ManifestEntry(
                source_id=row["source_id"],
                document_id=row["document_id"],
                source_verification=row["source_verification"],
                artifact_path=row["artifact_ref"],
                document_path=row["document_ref"],
                content_hash=row["content_hash"],
                chunking_version=row["chunking_version"],
                max_chars=row["max_chars"],
                overlap_chars=row["overlap_chars"],
                passage_count=count,
                assessment_digest=row["assessment_digest"],
            )
            entries[document_id] = entry
            manifest_entries.append(entry)
        manifest = build_manifest(tuple(manifest_entries))
        if manifest.snapshot_id != stored_snapshot:
            raise LoadError("stored manifest snapshot does not match database rows")
        passages = tuple(
            Passage(
                passage_id=row["passage_id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                parser_id=row["parser_id"],
                chunking_version=row["chunking_version"],
                max_chars=row["max_chars"],
                overlap_chars=row["overlap_chars"],
            )
            for row in (dict(zip(passage_columns, r)) for r in passage_rows)
        )
        if not passages:
            raise LoadError("database holds no passages")
        with conn.cursor() as cur:
            for entry in manifest.entries:
                if entry.source_verification == "unverified":
                    continue
                cur.execute(
                    "SELECT assessment_digest, source_id, policy_id, policy_version, "
                    "status, reason_codes, check_ids, verdicts FROM assessments "
                    "WHERE assessment_digest = %s",
                    (entry.assessment_digest,),
                )
                assessment_rows = cur.fetchall()
                assessment_columns = [desc[0] for desc in cur.description]
                matches = [
                    dict(zip(assessment_columns, r)) for r in assessment_rows
                    if dict(zip(assessment_columns, r))["source_id"] == entry.source_id
                ]
                if not matches:
                    raise LoadError(f"no stored assessment binds {entry.document_id}")
                row = matches[0]
                try:
                    verdicts = tuple(
                        CategoryVerdict(category=item["category"], verdict=item["verdict"])
                        for item in (row["verdicts"] or [])
                    )
                except (KeyError, TypeError, AttributeError) as exc:
                    raise LoadError(
                        f"stored assessment for {entry.document_id} is malformed"
                    ) from exc
                assessment = Assessment(
                    source_id=row["source_id"],
                    policy_id=row["policy_id"],
                    policy_version=row["policy_version"],
                    status=row["status"],
                    reason_codes=tuple(row["reason_codes"] or ()),
                    check_ids=tuple(row["check_ids"] or ()),
                    verdicts=verdicts,
                )
                cur.execute(
                    "SELECT check_id, source_id, check_type, outcome, description, "
                    "observation, limitations, content_hash, checked_at, verifier, "
                    "observation_source FROM checks WHERE source_id = %s",
                    (entry.source_id,),
                )
                check_columns = [desc[0] for desc in cur.description]
                checks = [
                    VerificationCheck(
                        check_id=r[0], source_id=r[1], check_type=r[2], outcome=r[3],
                        description=r[4], observation=r[5], limitations=r[6],
                        content_hash=r[7], checked_at=r[8], verifier=r[9],
                        observation_source=r[10],
                    )
                    for r in (dict(zip(check_columns, r)) for r in cur.fetchall())
                ]
                try:
                    verify_entry_binding(entry, assessment, checks, policy=canonical_policy())
                except ManifestError as exc:
                    raise LoadError(f"stored assessment does not authorize {entry.document_id}") from exc
    except LoadError:
        raise
    except Exception as exc:  # noqa: BLE001 - detail never served
        raise LoadError("database-backed corpus load failed") from exc
    for passage in passages:
        if passage.document_id not in documents:
            raise LoadError("passage disconnected from its document")
    for document_id, document in documents.items():
        if document.source_id not in sources:
            raise LoadError(f"document {document_id} disconnected from its source")
    return {
        "manifest": manifest,
        "entries": entries,
        "passages": passages,
        "documents": documents,
        "sources": sources,
    }
