"""Neon/PostgreSQL persistence tests (Phase 32).

All database tests here are unit tests against an in-process fake
DBAPI connection (``FakeConn``): they exercise configuration,
migration ordering, import idempotency/conflicts, row mapping, the
verification gate over stored rows, and failure secrecy WITHOUT any
server. Nothing here ran against real Neon or local PostgreSQL —
live integration is untested (no credentials/server in this
environment) and is marked as such in docs/neon-persistence.md.
"""

import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from backend.app import db as db_module
from backend.app.chunking import chunk_document
from backend.app.corpus_store import (
    ImportConflictError,
    ImportError,
    LoadError,
    collect_file_records,
    import_corpus,
    load_corpus_from_db,
)
from backend.app.db import (
    ConnectionPool,
    DbConfig,
    DbConfigError,
    DbConnectionError,
    apply_migrations,
    db_config_from_env,
    redact_dsn,
)
from backend.app.evaluation import PassageBenchmark, PassageQuery, evaluate_passages
from backend.app.main import CORPUS_UNAVAILABLE, create_app
from backend.app.manifest import build_passages, entry_for, load_manifest_file
from backend.app.source_verification import (
    Assessment,
    CategoryVerdict,
    VerificationCheck,
    assessment_digest,
    assess,
    canonical_policy,
)
from backend.app.sources import SourceRecord

CORPUS_DIR = os.path.join("corpus", "india-code")


class FakeDatabase:
    """Dict-backed stand-in for the migrated schema. Understands only
    the fixed statement shapes our code emits (parsed column lists for
    SELECTs, PK-keyed storage for INSERTs)."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {
            "sources": {}, "documents": {}, "passages": {},
            "assessments": {}, "checks": {}, "corpus_meta": {},
            "ingestion_runs": {}, "schema_migrations": {},
        }
        self.recorded: list[tuple[str, object]] = []
        self.fail_on: tuple[str, ...] = ()


class FakeCursor:
    def __init__(self, database: FakeDatabase, conn: "FakeConn") -> None:
        self._db = database
        self._conn = conn
        self.description: list[tuple[str]] = []
        self._rows: list[tuple] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self._db.recorded.append((sql, params))
        if any(marker in sql for marker in self._db.fail_on):
            raise RuntimeError("synthetic database failure")
        upper = sql.strip().upper()
        if upper.startswith("CREATE TABLE"):
            return
        if upper.startswith("SET "):
            return
        if upper.startswith("SELECT") and " FROM " not in upper:
            self.description = []
            self._rows = []  # SELECT without FROM: no-op success
            return
        if upper.startswith("SELECT VERSION FROM SCHEMA_MIGRATIONS"):
            self.description = [("version",)]
            self._rows = [(v,) for v in sorted(self._db.tables["schema_migrations"])]
            return
        if upper.startswith("SELECT VALUE FROM CORPUS_META"):
            self.description = [("value",)]
            key = params[0]  # type: ignore[index]
            row = self._db.tables["corpus_meta"].get(key)
            self._rows = [(row["value"],)] if row else []
            return
        match = re.match(r"\s*SELECT\s+(.*?)\s+FROM\s+(\w+)", sql, re.IGNORECASE | re.DOTALL)
        if match and "WHERE" in sql.upper():
            table = match.group(2)
            key_column = re.search(r"WHERE\s+(\w+)\s*=", sql, re.IGNORECASE).group(1)  # type: ignore[union-attr]
            key = params[0]  # type: ignore[index]
            stored = {
                row[key_column]: row
                for row in self._db.tables[table].values()
                if key_column in row
            }
            row = stored.get(key)
            if row is None:
                self.description = []
                self._rows = []
                return
            columns = [c.strip() for c in match.group(1).split(",")]
            if columns == ["*"]:
                columns = list(row.keys())
            self.description = [(c.split(".")[-1].split()[-1],) for c in columns]
            self._rows = [tuple(row.get(c[0]) for c in self.description)]
            return
        if match:
            columns = [c.strip() for c in match.group(1).split(",")]
            table = match.group(2)
            self.description = [(c.split(".")[-1].split()[-1],) for c in columns]
            rows = sorted(
                self._db.tables[table].values(),
                key=lambda r: tuple(str(r.get(c[0], "")) for c in self.description),
            )
            self._rows = [tuple(r.get(c[0]) for c in self.description) for r in rows]
            return
        if upper.startswith("INSERT INTO SCHEMA_MIGRATIONS"):
            self._db.tables["schema_migrations"][params[0]] = {"version": params[0]}  # type: ignore[index]
            return
        match = re.match(r"\s*INSERT INTO (\w+)\s*\((.*?)\)", sql, re.IGNORECASE | re.DOTALL)
        if match:
            table, columns = match.group(1), [c.strip() for c in match.group(2).split(",")]
            values = list(params)  # type: ignore[arg-type]
            row = dict(zip(columns, values))
            pk = {"sources": "source_id", "documents": "document_id",
                  "passages": "passage_id", "assessments": "assessment_digest",
                  "checks": "check_id", "corpus_meta": "key",
                  "ingestion_runs": "run_id"}[table]
            if "ON CONFLICT" in sql.upper() and row[pk] in self._db.tables[table]:
                return
            self._db.tables[table][row[pk]] = row
            return
        if upper.startswith("UPDATE INGESTION_RUNS"):
            run_id = params[0]  # type: ignore[index]
            self._db.tables["ingestion_runs"][run_id]["status"] = "completed"
            return
        raise AssertionError(f"fake cannot handle statement: {sql[:80]}")

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeConn:
    def __init__(self, database: FakeDatabase) -> None:
        self._db = database
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._db, self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def file_records_to_fake() -> FakeDatabase:
    """Populate a fake database from the REAL file records using the
    same column layout the import writes (exercises the load mapping
    against genuine rows)."""
    import hashlib as _hashlib

    database = FakeDatabase()
    for item in collect_file_records(CORPUS_DIR):
        source, document = item.source, item.document
        database.tables["sources"][source.source_id] = {
            "source_id": source.source_id, "title": source.title,
            "publisher": source.publisher, "origin": source.origin,
            "source_type": source.source_type, "jurisdiction": source.jurisdiction,
            "publication": source.publication, "acquired_at": source.acquired_at,
            "note": source.note, "verification": source.verification,
            "receipt": json.dumps(item.receipt),
        }
        digest = (
            "" if document.source_verification == "unverified"
            else assessment_digest(item.assessment)
        )
        entry = entry_for(
            document, artifact_path=item.artifact_rel, document_path=item.document_rel,
        )
        database.tables["documents"][document.document_id] = {
            "document_id": document.document_id, "source_id": document.source_id,
            "filename": document.filename, "format": document.format,
            "parser_id": document.parser_id, "content_hash": document.content_hash,
            "size_bytes": document.size_bytes, "normalized_text": document.normalized_text,
            "extraction_status": document.extraction_status,
            "source_verification": document.source_verification,
            "assessment_digest": digest, "artifact_ref": item.artifact_rel,
            "document_ref": item.document_rel,
            "chunking_version": entry.chunking_version,
            "max_chars": entry.max_chars, "overlap_chars": entry.overlap_chars,
        }
        for passage in chunk_document(document):
            database.tables["passages"][passage.passage_id] = {
                "passage_id": passage.passage_id, "document_id": passage.document_id,
                "chunk_index": passage.chunk_index, "text": passage.text,
                "char_start": passage.char_start, "char_end": passage.char_end,
                "page_start": passage.page_start, "page_end": passage.page_end,
                "parser_id": passage.parser_id,
                "chunking_version": passage.chunking_version,
                "max_chars": passage.max_chars, "overlap_chars": passage.overlap_chars,
            }
        database.tables["assessments"][assessment_digest(item.assessment)] = {
            "assessment_digest": assessment_digest(item.assessment),
            "source_id": item.assessment.source_id,
            "policy_id": item.assessment.policy_id,
            "policy_version": item.assessment.policy_version,
            "status": item.assessment.status,
            "reason_codes": list(item.assessment.reason_codes),
            "check_ids": list(item.assessment.check_ids),
            "verdicts": [
                {"category": v.category, "verdict": v.verdict}
                for v in item.assessment.verdicts
            ],
        }
        for check in item.checks:
            database.tables["checks"][check.check_id] = {
                "check_id": check.check_id, "source_id": check.source_id,
                "check_type": check.check_type, "outcome": check.outcome,
                "description": check.description, "observation": check.observation,
                "limitations": check.limitations, "content_hash": check.content_hash,
                "checked_at": check.checked_at, "verifier": check.verifier,
                "observation_source": check.observation_source,
            }
    manifest = load_manifest_file(os.path.join("corpus", "manifest.json"))
    database.tables["corpus_meta"]["snapshot_id"] = {
        "key": "snapshot_id", "value": manifest.snapshot_id,
    }
    assert _hashlib.sha256(b"x").hexdigest()  # silence unused-import lint
    return database


# ---- configuration ----

def test_db_config_absent_means_file_backed(monkeypatch) -> None:
    monkeypatch.delenv("TAXGUARD_DATABASE_URL", raising=False)
    assert db_config_from_env(required=False) is None


def test_db_config_required_missing_fails(monkeypatch) -> None:
    monkeypatch.delenv("TAXGUARD_DATABASE_URL", raising=False)
    with pytest.raises(DbConfigError, match="TAXGUARD_DATABASE_URL is required"):
        db_config_from_env(required=True)


def test_db_config_validation(monkeypatch) -> None:
    monkeypatch.setenv("TAXGUARD_DATABASE_URL", "postgresql://u:p@db.example.com/taxguard?sslmode=require")
    monkeypatch.setenv("TAXGUARD_DB_POOL_MAX", "0")
    with pytest.raises(DbConfigError, match="1\\.\\.20"):
        db_config_from_env(required=True)
    monkeypatch.setenv("TAXGUARD_DB_POOL_MAX", "5")
    monkeypatch.setenv("TAXGUARD_DATABASE_URL", "http://not-postgres/x")
    with pytest.raises(DbConfigError, match="postgres"):
        db_config_from_env(required=True)


def test_db_config_neon_requires_strict_ssl(monkeypatch) -> None:
    monkeypatch.setenv("TAXGUARD_DATABASE_URL", "postgresql://u:p@ep-x.neon.tech/db")
    with pytest.raises(DbConfigError, match="sslmode"):
        db_config_from_env(required=True)
    monkeypatch.setenv(
        "TAXGUARD_DATABASE_URL", "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
    )
    config = db_config_from_env(required=True)
    assert config is not None and config.unpooled_dsn == config.dsn
    monkeypatch.setenv(
        "TAXGUARD_DATABASE_URL_UNPOOLED", "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
    )
    config = db_config_from_env(required=True)
    assert config is not None
    assert config.unpooled_dsn.startswith("postgresql://")


def test_redact_dsn_masks_password() -> None:
    redacted = redact_dsn("postgresql://user:s3cret@ep-x.neon.tech/db?sslmode=require")
    assert "s3cret" not in redacted and "***" in redacted and "ep-x.neon.tech" in redacted
    plain = "postgresql://u@db.example.com/taxguard"
    assert redact_dsn(plain) == plain


def test_connect_failure_is_redacted(monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("login failed for user s3cret-password")

    monkeypatch.setattr(db_module, "_driver", lambda: type("D", (), {"connect": staticmethod(boom)}))
    config = DbConfig(dsn="postgresql://user:s3cret-password@db.example.com/db?sslmode=require",
                      unpooled_dsn="postgresql://user:s3cret-password@db.example.com/db?sslmode=require")
    with pytest.raises(DbConnectionError) as caught:
        db_module.connect(config)
    assert "s3cret-password" not in str(caught.value)
    assert "RuntimeError" in str(caught.value)


def test_missing_driver_reports_clearly(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    config = DbConfig(dsn="postgresql://u@db.example.com/db", unpooled_dsn="x")
    with pytest.raises(DbConnectionError, match="psycopg"):
        db_module.connect(config)


# ---- pool ----

def _pool_config() -> DbConfig:
    return DbConfig(dsn="postgresql://u@db.example.com/db", unpooled_dsn="x", pool_max=2)


def test_pool_acquire_reuse_and_close(monkeypatch) -> None:
    made: list[FakeConn] = []
    monkeypatch.setattr(
        db_module, "connect",
        lambda config, **kwargs: made.append(FakeConn(FakeDatabase())) or made[-1],
    )
    pool = ConnectionPool(_pool_config())
    with pool.acquire() as first:
        pass
    with pool.acquire() as second:
        assert second is first  # returned to idle, reused
    assert len(made) == 1
    pool.close()
    assert made[0].closes == 1
    with pytest.raises(DbConnectionError, match="closed"):
        with pool.acquire():
            pass  # pragma: no cover


def test_pool_exhaustion_and_error_cleanup(monkeypatch) -> None:
    import contextlib

    monkeypatch.setattr(
        db_module, "connect", lambda config, **kwargs: FakeConn(FakeDatabase()),
    )
    pool = ConnectionPool(_pool_config())
    with contextlib.ExitStack() as stack:
        stack.enter_context(pool.acquire())
        stack.enter_context(pool.acquire())
        with pytest.raises(DbConnectionError, match="exhausted"):
            with pool.acquire():
                pass  # pragma: no cover


# ---- migrations ----

def _migration_dir(tmp_path, files: dict[str, str]) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, sql in files.items():
        (tmp_path / name).write_text(sql, encoding="utf-8")
    return str(tmp_path)


def test_migrations_apply_in_order_and_skip_applied(tmp_path) -> None:
    directory = _migration_dir(tmp_path, {
        "0002_b.sql": "SELECT 2",
        "0001_a.sql": "SELECT 1",
        "0001_a.down.sql": "SELECT 0",  # down files are never applied
    })
    conn = FakeConn(FakeDatabase())
    assert apply_migrations(conn, directory) == ("0001_a", "0002_b")
    assert apply_migrations(conn, directory) == ()
    applied = [sql for sql, _ in conn._db.recorded if sql in ("SELECT 1", "SELECT 2")]
    assert applied == ["SELECT 1", "SELECT 2"]
    assert "SELECT 0" not in [sql for sql, _ in conn._db.recorded]


def test_migrations_empty_dir_and_failure(tmp_path) -> None:
    conn = FakeConn(FakeDatabase())
    with pytest.raises(DbConnectionError, match="no migrations"):
        apply_migrations(conn, _migration_dir(tmp_path / "empty", {}))
    database = FakeDatabase()
    database.fail_on = ("BOOM",)
    conn = FakeConn(database)
    directory = _migration_dir(tmp_path / "bad", {"0001_x.sql": "SELECT BOOM"})
    with pytest.raises(DbConnectionError, match="migration failed"):
        apply_migrations(conn, directory)
    assert conn.rollbacks >= 1


# ---- import ----

def test_import_dry_run_writes_nothing() -> None:
    database = FakeDatabase()
    conn = FakeConn(database)
    report = import_corpus(conn, CORPUS_DIR, dry_run=True)
    assert report.dry_run and report.sources == 2 and report.passages == 91
    assert report.snapshot_id == "77fdc5329096755a58d09d6c5e78d52a55685021969e3dd019116f4bb37c4d31"
    assert database.recorded == []  # validation is pure reads; zero statements
    assert conn.commits == 0 and conn.rollbacks == 0


def test_import_idempotent_and_conflict(tmp_path) -> None:
    database = file_records_to_fake()
    conn = FakeConn(database)
    report = import_corpus(conn, CORPUS_DIR, dry_run=False)
    assert report.skipped_identical == 2  # both documents already identical
    assert [sql for sql, _ in database.recorded
            if sql.strip().upper().startswith("INSERT INTO DOCUMENTS")] == []

    database.tables["documents"][
        "india-code-it-act-1961-first-schedule:86c3575a49be2d8b77a571556a8ea43e"
    ]["content_hash"] = "0" * 64
    conn = FakeConn(database)
    with pytest.raises(ImportConflictError, match="refusing to overwrite"):
        import_corpus(conn, CORPUS_DIR, dry_run=False)
    assert conn.rollbacks >= 1


def _synthetic_source_dir(tmp_path, *, receipt_digest: str) -> tuple[str, str]:
    """Minimal synthetic source directory using the real ingestion and
    assessment code paths (text artifact, matching hashes)."""
    from backend.app.source_verification import (
        ExpectedArtifact,
        ObservedArtifact,
        VerificationPolicy,
        reconcile,
    )
    from backend.app.sources import SourceRegistry, ingest_file

    root = tmp_path / "corpus" / "syn-imp-s1"
    root.mkdir(parents=True)
    source = SourceRecord(source_id="syn-imp-s1", title="Synthetic Import Source")
    registry = SourceRegistry()
    registry.register(source)
    artifact = root / "syn.txt"
    artifact.write_bytes(b"synthetic import bytes")
    record = ingest_file(registry, "syn-imp-s1", artifact)
    (root / "source.json").write_text(json.dumps(source.to_dict()), encoding="utf-8")
    (root / "document.json").write_text(json.dumps(record.to_dict()), encoding="utf-8")
    size = len(b"synthetic import bytes")
    checks = reconcile(
        source,
        observed=ObservedArtifact(sha256=record.content_hash, size_bytes=size),
        expected=ExpectedArtifact(sha256=record.content_hash, size_bytes=size),
        document=record,
        evidence_origin="local",
        checked_at="2026-01-01",
        verifier="test",
    )
    assessment = assess(
        "syn-imp-s1", checks, VerificationPolicy("p", "v", ("integrity",)),
    )
    (root / "verification.json").write_text(json.dumps({
        "assessment": assessment.to_dict(),
        "checks": [c.to_dict() for c in checks],
    }), encoding="utf-8")
    (root / "syn.receipt.json").write_text(json.dumps({
        "sha256": receipt_digest, "size_bytes": size,
    }), encoding="utf-8")
    return str(tmp_path / "corpus"), str(tmp_path)


def test_import_missing_and_mismatched_records(tmp_path) -> None:
    import hashlib

    digest = hashlib.sha256(b"synthetic import bytes").hexdigest()
    directory, root = _synthetic_source_dir(tmp_path / "ok", receipt_digest=digest)
    report = import_corpus(FakeConn(FakeDatabase()), directory, root=root, dry_run=True)
    assert report.sources == 1 and report.documents == 1

    bad, _ = _synthetic_source_dir(tmp_path / "bad", receipt_digest="f" * 64)
    with pytest.raises(ImportError, match="hash mismatch"):
        collect_file_records(bad, root=str(tmp_path / "bad"))

    lonely = tmp_path / "lonely"
    lonely.mkdir()
    (lonely / "document.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ImportError):
        collect_file_records(str(lonely))


# ---- load + gate over stored rows ----

def test_load_reconstructs_file_identical_corpus() -> None:
    """Row mapping fidelity: documents, sources, passages, and the
    snapshot must equal the file-backed corpus exactly."""
    database = file_records_to_fake()
    loaded = load_corpus_from_db(FakeConn(database))
    manifest = load_manifest_file(os.path.join("corpus", "manifest.json"))
    file_passages = build_passages(manifest, ".")
    assert loaded["manifest"].snapshot_id == manifest.snapshot_id
    assert loaded["passages"] == file_passages
    assert set(loaded["documents"]) == {
        "india-code-it-act-1961-first-schedule:86c3575a49be2d8b77a571556a8ea43e",
        "india-code-it-act-1961-second-schedule:f33737ee2a0335887dd32ea73e5f1eb7",
    }
    assert set(loaded["sources"]) == {
        "india-code-it-act-1961-first-schedule",
        "india-code-it-act-1961-second-schedule",
    }
    assert all(
        loaded["documents"][pid].content_hash == doc.content_hash
        for pid, doc in zip(
            [p.document_id for p in file_passages],
            [loaded["documents"][p.document_id] for p in file_passages],
        )
    )


def test_db_verified_row_without_evidence_fails_closed() -> None:
    """Flipping a stored label to verified (assessment still
    incomplete) must fail the whole load — rows never authorize."""
    database = file_records_to_fake()
    key = "india-code-it-act-1961-first-schedule:86c3575a49be2d8b77a571556a8ea43e"
    database.tables["documents"][key]["source_verification"] = "verified"
    database.tables["documents"][key]["assessment_digest"] = "a" * 64
    with pytest.raises(LoadError):
        load_corpus_from_db(FakeConn(database))


def test_db_self_authored_origin_cannot_authorize() -> None:
    """A stored verified assessment with a fabricated origin:passed
    record still fails the canonical gate at load."""
    database = file_records_to_fake()
    source_id = "india-code-it-act-1961-first-schedule"
    doc_id = source_id + ":86c3575a49be2d8b77a571556a8ea43e"
    fabricated = VerificationCheck(
        check_id=f"{source_id}:origin:fabricated",
        source_id=source_id, check_type="origin", outcome="passed",
        description="fabricated", observation="untrusted",
    )
    policy = canonical_policy()
    assessment = Assessment(
        source_id=source_id, policy_id=policy.policy_id,
        policy_version=policy.version, status="verified",
        reason_codes=tuple(f"satisfied:{c}" for c in policy.required_checks),
        check_ids=(fabricated.check_id,),
        verdicts=tuple(
            CategoryVerdict(category=c, verdict="satisfied")
            for c in policy.required_checks
        ),
    )
    database.tables["documents"][doc_id]["source_verification"] = "verified"
    database.tables["documents"][doc_id]["assessment_digest"] = assessment_digest(assessment)
    database.tables["assessments"][assessment_digest(assessment)] = {
        "assessment_digest": assessment_digest(assessment), "source_id": source_id,
        "policy_id": assessment.policy_id, "policy_version": assessment.policy_version,
        "status": "verified", "reason_codes": list(assessment.reason_codes),
        "check_ids": list(assessment.check_ids),
        "verdicts": [{"category": v.category, "verdict": v.verdict}
                     for v in assessment.verdicts],
    }
    database.tables["checks"][fabricated.check_id] = {
        "check_id": fabricated.check_id, "source_id": source_id,
        "check_type": "origin", "outcome": "passed",
        "description": "fabricated", "observation": "untrusted",
        "limitations": "", "content_hash": "", "checked_at": "",
        "verifier": "", "observation_source": "",
    }
    with pytest.raises(LoadError):
        load_corpus_from_db(FakeConn(database))


def test_db_unknown_policy_and_broken_links_fail_closed() -> None:
    database = file_records_to_fake()
    source_id = "india-code-it-act-1961-first-schedule"
    doc_id = source_id + ":86c3575a49be2d8b77a571556a8ea43e"
    database.tables["documents"][doc_id]["source_verification"] = "verified"
    database.tables["documents"][doc_id]["assessment_digest"] = "b" * 64
    database.tables["assessments"]["b" * 64] = {
        "assessment_digest": "b" * 64, "source_id": source_id,
        "policy_id": "unknown-policy", "policy_version": "v9",
        "status": "verified", "reason_codes": [], "check_ids": [], "verdicts": [],
    }
    with pytest.raises(LoadError):
        load_corpus_from_db(FakeConn(database))

    # A document whose source row is gone must fail, not serve orphaned.
    database = file_records_to_fake()
    del database.tables["sources"]["india-code-it-act-1961-first-schedule"]
    with pytest.raises(LoadError, match="disconnected"):
        load_corpus_from_db(FakeConn(database))


def test_db_snapshot_drift_and_empty_fail_closed() -> None:
    database = file_records_to_fake()
    database.tables["corpus_meta"]["snapshot_id"]["value"] = "0" * 64
    with pytest.raises(LoadError, match="snapshot"):
        load_corpus_from_db(FakeConn(database))
    with pytest.raises(LoadError, match="no documents"):
        load_corpus_from_db(FakeConn(FakeDatabase()))


# ---- parity ----

QUALITY_QUERIES = (
    ("q-s1", "profits of life insurance business computed separately", "computed separately"),
    ("q-s2", "adjustment of tax paid by deduction at source", "deduction at source"),
    ("q-s3", "deposit by purchaser on resale in default", "Deposit by purchaser"),
    ("q-s4", "Insurance Act 1938 profit and loss account", "Insurance Act, 1938"),
    ("q-s5", "gains on realisation of investments added back", "realisation of investments"),
    ("q-s6", "notice requiring payment within fifteen days of service", "fifteen days"),
    ("q-v1", "PROFITS OF LIFE INSURANCE BUSINESS COMPUTED SEPARATELY", "computed separately"),
    ("q-x1", "depreciation rates for machinery", None),
    ("q-x2", "surcharge and cess applicability", None),
    ("q-x3", "taxes insurances", None),
)


def test_file_db_retrieval_parity() -> None:
    """Same queries, same rows, same BM25 core: rankings, metrics, and
    empty-probe behavior must agree exactly between backends."""
    manifest = load_manifest_file(os.path.join("corpus", "manifest.json"))
    file_passages = build_passages(manifest, ".")
    db_passages = load_corpus_from_db(FakeConn(file_records_to_fake()))["passages"]
    assert db_passages == file_passages

    judged = []
    for query_id, query_text, anchor in QUALITY_QUERIES:
        relevant = tuple(p.passage_id for p in file_passages
                         if anchor is not None and anchor in p.text)
        judged.append(PassageQuery(query_id, query_text, relevant,
                                   basis="parity probe" if relevant else "empty probe"))
    benchmark = PassageBenchmark(dataset_id="parity-probe-v1", version="v1",
                                 synthetic=True, queries=tuple(judged))
    file_report = evaluate_passages(file_passages, benchmark, k_values=(1, 3, 5))
    db_report = evaluate_passages(db_passages, benchmark, k_values=(1, 3, 5))
    assert db_report == file_report
    assert (db_report.query_count, db_report.included_count, db_report.excluded_count) == (10, 7, 3)
    for query in db_report.queries:
        file_ids = next(q.retrieved_ids for q in file_report.queries
                        if q.query_id == query.query_id)
        assert query.retrieved_ids == file_ids, query.query_id


# ---- app integration + secrecy ----

def test_db_failure_serves_generic_503_without_secrets(monkeypatch) -> None:
    """Database-backed /ask with a dead database behaves exactly like a
    corrupt file corpus: generic 503, redacted detail, no secrets."""
    def dead_connect(config: DbConfig, **kwargs: object) -> object:
        raise DbConnectionError(
            "could not connect to database at postgresql://app:***@ep-x.neon.tech/db (OperationalError)"
        )

    monkeypatch.setattr("backend.app.main.connect", dead_connect)
    config = DbConfig(dsn="postgresql://app:s3cret@ep-x.neon.tech/db?sslmode=require",
                      unpooled_dsn="postgresql://app:s3cret@ep-x.neon.tech/db?sslmode=require")
    client = TestClient(create_app(db=config))
    assert client.get("/ready").status_code == 503
    response = client.post("/ask", json={"question": "tax"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert "s3cret" not in response.text and "neon.tech" not in response.text


def test_db_backed_ask_matches_file_backed_ask(monkeypatch) -> None:
    """Same corpus dict shape through the same answer path: identical
    statuses, passages, labels, and limitations."""
    database = file_records_to_fake()
    monkeypatch.setattr(
        "backend.app.main.connect", lambda config, **kwargs: FakeConn(database),
    )
    db_client = TestClient(create_app(db=DbConfig(
        dsn="postgresql://u@db.example.com/db", unpooled_dsn="x")))
    file_client = TestClient(create_app())
    for payload in ({"question": "tax"}, {"question": "xyzzy qwerty asdfgh"},
                    {"question": "First Schedule as amended in 2024"}):
        db_body = db_client.post("/ask", json=payload).json()
        file_body = file_client.post("/ask", json=payload).json()
        assert db_body["status"] == file_body["status"] == \
            ("retrieved" if payload["question"] != "xyzzy qwerty asdfgh" else "abstained")
        assert [(p["passage_id"], p["score"], p["source_verification"])
                for p in db_body["passages"]] == \
               [(p["passage_id"], p["score"], p["source_verification"])
                for p in file_body["passages"]]
        assert db_body["limitations"] == file_body["limitations"]
