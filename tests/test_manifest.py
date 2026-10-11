"""Manifest tests using SYNTHETIC records in temporary directories.

Nothing here touches the real corpus, network, or any persistent
store. Determinism, validation, and preservation are asserted directly.
"""

import json
import os

import pytest

from backend.app.chunking import CHUNKING_VERSION, ChunkConfig
from backend.app.manifest import (
    MANIFEST_SCHEMA_VERSION,
    CorpusManifest,
    ExcludedArtifact,
    ManifestEntry,
    ManifestError,
    build_manifest,
    build_passages,
    entry_for,
)
from backend.app.retrieval import retrieve_passages
from backend.app.sources import DocumentRecord


def syn_doc(doc_id: str, text: str, verification: str = "unverified") -> DocumentRecord:
    return DocumentRecord(
        document_id=doc_id,
        source_id=doc_id.split(":")[0],
        filename=f"{doc_id.split(':')[0]}.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash="a" * 64,
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification=verification,
    )


def write_doc(tmp_path, name: str, document: DocumentRecord) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document.to_dict(), handle)
    return path


def two_doc_manifest(tmp_path, config=None):
    first = syn_doc("syn-s1:" + "a" * 32, "Alpha beta.\n\nGamma delta.")
    second = syn_doc("syn-s2:" + "b" * 32, "Delta zeta.\n\nEta theta.")
    write_doc(tmp_path, "d1.json", first)
    write_doc(tmp_path, "d2.json", second)
    entries = (
        entry_for(second, artifact_path="a2.pdf", document_path="d2.json", config=config),
        entry_for(first, artifact_path="a1.pdf", document_path="d1.json", config=config),
    )
    manifest = build_manifest(entries)
    return manifest, first, second


def test_deterministic_identity_and_reorder_invariance(tmp_path) -> None:
    manifest, _, _ = two_doc_manifest(tmp_path)
    assert manifest.snapshot_id == build_manifest(
        tuple(reversed(manifest.entries))).snapshot_id
    assert CorpusManifest.from_dict(
        json.loads(json.dumps(manifest.to_dict()))) == manifest
    assert manifest.to_dict()["schema_version"] == MANIFEST_SCHEMA_VERSION


def test_identity_sensitive_inputs(tmp_path) -> None:
    manifest, first, second = two_doc_manifest(tmp_path)
    tampered = DocumentRecord(**{**first.to_dict(), "content_hash": "c" * 64,
                                 "document_id": "syn-s1:" + "c" * 32})
    changed = build_manifest((entry_for(second, artifact_path="a2.pdf", document_path="d2.json"),
                              entry_for(tampered, artifact_path="a1.pdf", document_path="d1.json")))
    assert changed.snapshot_id != manifest.snapshot_id

    other_config = ChunkConfig(max_chars=40, overlap_chars=0)
    reconfigured = build_manifest((
        entry_for(second, artifact_path="a2.pdf", document_path="d2.json", config=other_config),
        entry_for(first, artifact_path="a1.pdf", document_path="d1.json", config=other_config),
    ))
    assert reconfigured.snapshot_id != manifest.snapshot_id
    assert reconfigured.entries[0].chunking_version == CHUNKING_VERSION


def test_duplicates_and_conflicts_rejected(tmp_path) -> None:
    manifest, first, _ = two_doc_manifest(tmp_path)
    entry = manifest.entries[0]
    with pytest.raises(ManifestError, match="duplicate"):
        build_manifest((entry, entry))
    with pytest.raises(ManifestError, match="conflicting"):
        build_manifest((ManifestEntry(**{**entry.to_dict(), "source_id": "other"}),))
    with pytest.raises(ManifestError, match="at least one entry"):
        build_manifest(())
    with pytest.raises(ManifestError, match="relative path"):
        entry_for(first, artifact_path="../escape.pdf", document_path="d1.json")
    with pytest.raises(ManifestError, match="relative path"):
        entry_for(first, artifact_path="a1.pdf", document_path="/abs/d1.json")


def test_builder_rebuild_identical_and_retrievable(tmp_path) -> None:
    manifest, _, _ = two_doc_manifest(tmp_path)
    first, second = build_passages(manifest, str(tmp_path)), build_passages(manifest, str(tmp_path))
    assert first == second
    assert [p.document_id for p in first] == sorted(p.document_id for p in first)
    assert len({p.passage_id for p in first}) == len(first)
    hits = retrieve_passages("alpha", first)
    assert {h.passage_id for h in hits} == {p.passage_id for p in first if "alpha" in p.text.lower()}


def test_builder_failures_closed(tmp_path) -> None:
    manifest, first, _ = two_doc_manifest(tmp_path)
    with pytest.raises(ManifestError, match="cannot read"):
        build_passages(manifest, str(tmp_path / "absent-dir"))

    tampered_path = os.path.join(str(tmp_path), "d1.json")
    with open(tampered_path, encoding="utf-8") as handle:
        pristine = handle.read()

    def rewrite(payload: str) -> None:
        with open(tampered_path, "w", encoding="utf-8") as handle:
            handle.write(payload)

    rewrite(json.dumps({**json.loads(pristine), "content_hash": "d" * 64}))
    with pytest.raises(ManifestError, match="mismatch"):
        build_passages(manifest, str(tmp_path))

    rewrite("{not json")
    with pytest.raises(ValueError, match="Expecting"):
        build_passages(manifest, str(tmp_path))
    rewrite(pristine)

    stale = ManifestEntry(**{**manifest.entries[0].to_dict(), "chunking_version": "taxguard-chunks-v0"})
    with pytest.raises(ManifestError, match="chunking"):
        build_passages(build_manifest((stale, manifest.entries[1])), str(tmp_path))

    drifted = ManifestEntry(**{**manifest.entries[0].to_dict(), "passage_count": 99})
    with pytest.raises(ManifestError, match="expects 99"):
        build_passages(build_manifest((drifted, manifest.entries[1])), str(tmp_path))


def test_excluded_never_enter(tmp_path) -> None:
    manifest, _, _ = two_doc_manifest(tmp_path)
    ghost = os.path.join(str(tmp_path), "ghost.json")
    with open(ghost, "w", encoding="utf-8") as handle:
        handle.write("{}")
    with_manifest = build_manifest(
        manifest.entries,
        excluded=(ExcludedArtifact("ghost.json", "synthetic exclusion for tests"),),
    )
    assert with_manifest.snapshot_id != manifest.snapshot_id
    built = build_passages(with_manifest, str(tmp_path))
    assert all("ghost" not in p.document_id for p in built)
    assert with_manifest.to_dict()["excluded"] == [
        {"identifier": "ghost.json", "reason": "synthetic exclusion for tests"}]


def test_inputs_preserved_and_trust_untouched(tmp_path) -> None:
    manifest, first, second = two_doc_manifest(tmp_path)
    before = {}
    for name in ("d1.json", "d2.json"):
        with open(os.path.join(str(tmp_path), name), "rb") as handle:
            before[name] = handle.read()
    assert first.source_verification == "unverified"
    assert second.source_verification == "unverified"
    build_passages(manifest, str(tmp_path))
    for name, data in before.items():
        with open(os.path.join(str(tmp_path), name), "rb") as handle:
            assert handle.read() == data
    entries = manifest.to_dict()["entries"]
    assert {e["source_verification"] for e in entries} == {"unverified"}


REAL_MANIFEST = os.path.join(os.path.dirname(__file__), "..", "corpus", "manifest.json")


@pytest.mark.skipif(
    not os.path.exists(REAL_MANIFEST),
    reason="generated corpus manifest absent from this checkout",
)
def test_real_corpus_manifest_rebuilds() -> None:
    """Developer workflow: validate a generated manifest and rebuild its
    passages. Regenerate with the Phase 21 ops script pattern (load each
    document.json, entry_for() with corpus-relative paths, build_manifest,
    write corpus/manifest.json); this test revalidates the result."""
    base = os.path.dirname(REAL_MANIFEST)
    root = os.path.dirname(base)
    with open(REAL_MANIFEST, encoding="utf-8") as handle:
        manifest = CorpusManifest.from_dict(json.load(handle))
    assert len(manifest.entries) == 2
    passages = build_passages(manifest, root)
    assert len(passages) == sum(e.passage_count for e in manifest.entries)
    assert len({p.passage_id for p in passages}) == len(passages)
    assert {e.source_verification for e in manifest.entries} == {"unverified"}
