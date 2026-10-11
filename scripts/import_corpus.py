"""Import the file-backed corpus into PostgreSQL/Neon.

Reads committed records under <root>/corpus, verifies artifact hashes,
and writes rows through backend.app.corpus_store. Dry-run is the
default: use --apply to write. Never modifies the corpus files.

Usage:
  python scripts/import_corpus.py [--root DIR] [--dry-run | --apply]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.corpus_store import ImportError, import_corpus
from backend.app.db import DbConfigError, apply_migrations, connect, db_config_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".",
                        help="repository root containing corpus/ (default: current directory)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                       help="validate and report without writing (default)")
    group.add_argument("--apply", dest="dry_run", action="store_false",
                       help="run migrations and write rows")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    try:
        config = db_config_from_env(required=True)
    except DbConfigError as exc:
        print(f"configuration error: {exc}")
        return 2
    assert config is not None
    try:
        conn = connect(config, unpooled=True)
    except Exception as exc:  # noqa: BLE001 - already redacted by db.connect
        print(f"connection error: {exc}")
        return 1
    try:
        if not args.dry_run:
            applied = apply_migrations(conn)
            print(f"migrations applied: {list(applied) or 'none pending'}")
        report = import_corpus(conn, os.path.join(root, "corpus"), root=root, dry_run=args.dry_run)
    except ImportError as exc:
        print(f"import refused: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - already redacted upstream
        print(f"import failed: {exc}")
        return 1
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
    mode = "dry-run" if report.dry_run else "applied"
    print(f"{mode}: {report.sources} sources, {report.documents} documents, "
          f"{report.passages} passages, snapshot {report.snapshot_id[:16]}... "
          f"({report.skipped_identical} skipped identical)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
