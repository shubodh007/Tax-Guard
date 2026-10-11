"""Regenerate corpus/manifest.json from committed document records.

Scans <root>/corpus for files named exactly document.json, derives one
manifest entry per record (artifact path = the record's own filename
beside its document file), and writes a validated manifest. Used by
fresh checkouts and the deployment build, where the manifest may not
exist yet. Never reads PDFs, never writes anything except the manifest.

Usage: python scripts/build_manifest.py [--root DIR] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.manifest import build_manifest, entry_for
from backend.app.sources import DocumentRecord


def collect(root: str) -> list[tuple[str, str, DocumentRecord]]:
    """Find document.json files under <root>/corpus and load them.
    Returns (artifact_rel, document_rel, record) triples with
    repository-root-relative forward-slash paths."""
    corpus = os.path.join(root, "corpus")
    found: list[tuple[str, str, DocumentRecord]] = []
    for dirpath, _, filenames in os.walk(corpus):
        for name in sorted(filenames):
            if name != "document.json":
                continue
            full = os.path.join(dirpath, name)
            with open(full, encoding="utf-8") as handle:
                record = DocumentRecord.from_dict(json.load(handle))
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            artifact_rel = os.path.join(os.path.dirname(rel), record.filename).replace(os.sep, "/")
            found.append((artifact_rel, rel, record))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".",
                        help="repository root containing corpus/ (default: current directory)")
    parser.add_argument("--out", default=None,
                        help="manifest output path (default: <root>/corpus/manifest.json)")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    entries = [
        entry_for(record, artifact_path=artifact_rel, document_path=document_rel)
        for artifact_rel, document_rel, record in collect(root)
    ]
    manifest = build_manifest(entries)
    out_path = args.out or os.path.join(root, "corpus", "manifest.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {out_path}: {len(entries)} documents, snapshot {manifest.snapshot_id[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
