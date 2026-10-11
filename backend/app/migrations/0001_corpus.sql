-- 0001: evidence-grounded corpus tables.
--
-- Mirrors the file-backed records (SourceRecord, DocumentRecord,
-- Passage, VerificationCheck, Assessment, AcquisitionReceipt, manifest
-- entries) without changing their meaning: a row is storage, never
-- authorization. Verification labels are carried verbatim; whether a
-- label is backed is decided by authorize_status() at load, never by
-- the presence of a row.
--
-- DOWN migration: backend/app/migrations/0001_corpus.down.sql.
-- Rolling back drops every table below AND ALL IMPORTED ROWS. This is
-- inherently destructive: take a pg_dump backup first
-- (see docs/neon-persistence.md).

CREATE TABLE sources (
    source_id       TEXT PRIMARY KEY,
    title           TEXT NOT NULL CHECK (btrim(title) <> ''),
    publisher       TEXT NOT NULL DEFAULT '',
    origin          TEXT NOT NULL DEFAULT '',
    source_type     TEXT NOT NULL DEFAULT '',
    jurisdiction    TEXT NOT NULL DEFAULT '',
    publication     TEXT NOT NULL DEFAULT '',
    acquired_at     TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    verification    TEXT NOT NULL DEFAULT 'unverified'
                    CHECK (verification IN ('unverified', 'verified', 'disputed')),
    receipt         JSONB
);

CREATE TABLE documents (
    document_id         TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES sources (source_id),
    filename            TEXT NOT NULL CHECK (btrim(filename) <> ''),
    format              TEXT NOT NULL DEFAULT '',
    parser_id           TEXT NOT NULL DEFAULT '',
    content_hash        CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    size_bytes          BIGINT NOT NULL CHECK (size_bytes >= 0),
    normalized_text     TEXT NOT NULL,
    extraction_status   TEXT NOT NULL DEFAULT 'ok',
    source_verification TEXT NOT NULL DEFAULT 'unverified'
                        CHECK (source_verification IN ('unverified', 'verified', 'disputed')),
    assessment_digest   TEXT NOT NULL DEFAULT '',
    artifact_ref        TEXT NOT NULL DEFAULT '',
    document_ref        TEXT NOT NULL DEFAULT '',
    chunking_version    TEXT NOT NULL DEFAULT '',
    max_chars           INTEGER NOT NULL DEFAULT 0 CHECK (max_chars >= 0),
    overlap_chars       INTEGER NOT NULL DEFAULT 0 CHECK (overlap_chars >= 0)
);
CREATE INDEX documents_by_source ON documents (source_id);

CREATE TABLE passages (
    passage_id      TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES documents (document_id),
    chunk_index     INTEGER NOT NULL CHECK (chunk_index >= 0),
    text            TEXT NOT NULL CHECK (text <> ''),
    char_start      INTEGER NOT NULL CHECK (char_start >= 0),
    char_end        INTEGER NOT NULL CHECK (char_end >= char_start),
    page_start      INTEGER CHECK (page_start IS NULL OR page_start >= 1),
    page_end        INTEGER CHECK (page_end IS NULL OR page_end >= 1),
    parser_id       TEXT NOT NULL DEFAULT '',
    chunking_version TEXT NOT NULL DEFAULT '',
    max_chars       INTEGER NOT NULL DEFAULT 0 CHECK (max_chars >= 0),
    overlap_chars   INTEGER NOT NULL DEFAULT 0 CHECK (overlap_chars >= 0),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX passages_by_document ON passages (document_id);

CREATE TABLE assessments (
    assessment_digest CHAR(64) PRIMARY KEY CHECK (assessment_digest ~ '^[0-9a-f]{64}$'),
    source_id         TEXT NOT NULL REFERENCES sources (source_id),
    policy_id         TEXT NOT NULL CHECK (btrim(policy_id) <> ''),
    policy_version    TEXT NOT NULL CHECK (btrim(policy_version) <> ''),
    status            TEXT NOT NULL
                      CHECK (status IN ('verified', 'incomplete', 'failed', 'conflicting')),
    reason_codes      TEXT[] NOT NULL DEFAULT '{}',
    check_ids         TEXT[] NOT NULL DEFAULT '{}',
    verdicts          JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX assessments_by_source ON assessments (source_id);

CREATE TABLE checks (
    check_id         TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES sources (source_id),
    check_type       TEXT NOT NULL
                     CHECK (check_type IN ('origin', 'integrity', 'record_identity',
                                          'version', 'completeness', 'applicability')),
    outcome          TEXT NOT NULL
                     CHECK (outcome IN ('passed', 'failed', 'inconclusive', 'not_checked')),
    description      TEXT NOT NULL DEFAULT '',
    observation      TEXT NOT NULL DEFAULT '',
    limitations      TEXT NOT NULL DEFAULT '',
    content_hash     TEXT NOT NULL DEFAULT '',
    checked_at       TEXT NOT NULL DEFAULT '',
    verifier         TEXT NOT NULL DEFAULT '',
    observation_source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX checks_by_source ON checks (source_id);

-- Single-row manifest identity for the imported snapshot.
CREATE TABLE corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Audit trail for import runs (one row per attempt, including dry runs
-- recorded by the caller convention; failures stay visible here).
CREATE TABLE ingestion_runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_id  TEXT NOT NULL DEFAULT '',
    sources      INTEGER NOT NULL DEFAULT 0 CHECK (sources >= 0),
    documents    INTEGER NOT NULL DEFAULT 0 CHECK (documents >= 0),
    passages     INTEGER NOT NULL DEFAULT 0 CHECK (passages >= 0),
    status       TEXT NOT NULL DEFAULT 'started'
                 CHECK (status IN ('started', 'completed', 'failed')),
    detail       TEXT NOT NULL DEFAULT ''
);
