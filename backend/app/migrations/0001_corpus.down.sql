-- DOWN migration for 0001_corpus.sql. DOCUMENTATION AND EMERGENCY USE ONLY.
--
-- This drops every corpus table AND ALL IMPORTED ROWS. It is inherently
-- destructive and cannot be undone except from a backup. Procedure:
--   1. pg_dump the database first and verify the dump restores elsewhere.
--   2. Stop application traffic (the schema the app reads will vanish).
--   3. Apply this file inside one transaction; on any error it rolls back.
--   4. Re-run the up migration and re-import from the file-backed corpus
--      (the files under corpus/ are the source of truth and are untouched
--      by any database operation).
-- Never run against a shared database without an explicit, reviewed plan.

DROP TABLE IF EXISTS ingestion_runs;
DROP TABLE IF EXISTS corpus_meta;
DROP TABLE IF EXISTS checks;
DROP TABLE IF EXISTS assessments;
DROP TABLE IF EXISTS passages;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS sources;
