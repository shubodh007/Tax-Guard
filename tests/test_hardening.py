"""Phase 24 reliability and security hardening tests.

Synthetic fixtures only; no network, no real corpus, no production data.
Each test targets a verified defect: generic 503s, readiness, resolved
path containment, production CORS defaults, and opaque request ids.
"""

import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.main import CORPUS_UNAVAILABLE, create_app
from backend.app.manifest import (
    ManifestError,
    build_manifest,
    build_passages,
    entry_for,
    resolve_within_root,
)


def _syn_doc(doc_id: str, text: str):
    from backend.app.sources import DocumentRecord

    return DocumentRecord(
        document_id=doc_id,
        source_id=doc_id.split(":")[0],
        filename="syn.txt",
        format="txt",
        parser_id="taxguard-sources-v1:txt",
        content_hash="a" * 64,
        size_bytes=len(text.encode()),
        normalized_text=text,
        extraction_status="ok",
        source_verification="unverified",
    )


def _seed(tmp_path) -> str:
    from backend.app.sources import SourceRecord

    first = _syn_doc("syn-harden-s1:" + "a" * 32, "Alpha beta gamma provisions.")
    second = _syn_doc("syn-harden-s2:" + "b" * 32, "Zeta eta theta notes.")
    os.makedirs(os.path.join(str(tmp_path), "d1"))
    os.makedirs(os.path.join(str(tmp_path), "d2"))
    with open(os.path.join(str(tmp_path), "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(first.to_dict(), handle)
    with open(os.path.join(str(tmp_path), "d2", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(second.to_dict(), handle)
    manifest = build_manifest((
        entry_for(first, artifact_path="d1/a.pdf", document_path="d1/doc.json"),
        entry_for(second, artifact_path="d2/a.pdf", document_path="d2/doc.json"),
    ))
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle)
    return manifest_path


def test_503_is_generic_without_paths(tmp_path) -> None:
    missing = TestClient(create_app(
        manifest_path=os.path.join(str(tmp_path), "absent.json"), base_dir=str(tmp_path)))
    response = missing.post("/ask", json={"question": "alpha"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert CORPUS_UNAVAILABLE == "corpus unavailable"
    body = response.text
    assert str(tmp_path) not in body
    assert "Traceback" not in body
    assert ".py" not in body

    bad_path = os.path.join(str(tmp_path), "manifest.json")
    with open(bad_path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    invalid = TestClient(create_app(manifest_path=bad_path, base_dir=str(tmp_path)))
    response = invalid.post("/ask", json={"question": "alpha"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}


def test_ready_reflects_corpus_state(tmp_path) -> None:
    healthy = TestClient(create_app(
        manifest_path=_seed(tmp_path), base_dir=str(tmp_path)))
    response = healthy.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["snapshot_id"]

    broken = TestClient(create_app(
        manifest_path=os.path.join(str(tmp_path), "absent.json"), base_dir=str(tmp_path)))
    response = broken.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert str(tmp_path) not in response.text

    assert broken.get("/health").status_code == 200
    assert healthy.get("/health").json()["status"] == "ok"


def test_resolve_within_root_rejects_escapes(tmp_path) -> None:
    base = str(tmp_path)
    # Note: drive-relative "C:foo" is omitted here on purpose — on Windows
    # it resolves to an ordinary filename inside the root, so containment
    # correctly accepts it; the lexical manifest guard still rejects any
    # "[1] == ':'" form (see test_lexical_entry_rejects_unsafe_paths).
    for escaping in (
        "../escape.json",
        "..\\escape.json",
        "d1/../../escape.json",
        "d1\\..\\..\\escape.json",
        "/abs/path.json",
        "C:/win/path.json",
        "C:\\win\\path.json",
    ):
        with pytest.raises(ManifestError, match="escapes"):
            resolve_within_root(base, escaping)
    # Sibling prefix: naive startswith(base) would accept base + "-other".
    sibling = base + "-other"
    os.makedirs(sibling, exist_ok=True)
    outside = os.path.realpath(os.path.join(sibling, "evil.json"))
    with open(outside, "w", encoding="utf-8") as handle:
        handle.write("{}")
    rel = os.path.relpath(outside, base)
    assert rel.startswith("..")
    with pytest.raises(ManifestError, match="escapes"):
        resolve_within_root(base, rel)
    # Inside paths resolve fine.
    inner = os.path.join(base, "d1", "doc.json")
    os.makedirs(os.path.dirname(inner), exist_ok=True)
    with open(inner, "w", encoding="utf-8") as handle:
        handle.write("{}")
    assert resolve_within_root(base, "d1/doc.json") == os.path.realpath(inner)
    assert resolve_within_root(base, "d1/sub/../doc.json") == os.path.realpath(inner)


def test_unsafe_artifact_in_manifest_fails_closed_at_load(tmp_path) -> None:
    from backend.app.manifest import CorpusManifest

    manifest_path = _seed(tmp_path)
    with open(manifest_path, encoding="utf-8") as handle:
        data = json.load(handle)
    data["entries"][0]["artifact_path"] = "../escape.pdf"
    with pytest.raises(ManifestError, match="relative path"):
        CorpusManifest.from_dict(data)
    data["entries"][0]["artifact_path"] = "d1/a.pdf"
    data["entries"][0]["document_path"] = "/abs/doc.json"
    with pytest.raises(ManifestError, match="relative path"):
        CorpusManifest.from_dict(data)


def test_build_passages_rejects_symlink_escape(tmp_path) -> None:
    # No actual symlink attack is tested on Windows: creating a symlink
    # here requires a privilege the host does not grant, so this test
    # skips there. Containment itself is covered platform-independently
    # by test_resolve_within_root_rejects_escapes.
    import tempfile

    outside_dir = tempfile.mkdtemp()
    doc = _syn_doc("syn-link-s1:" + "a" * 32, "Alpha beta gamma delta epsilon.")
    outside_doc = os.path.join(outside_dir, "doc.json")
    with open(outside_doc, "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    link = os.path.join(str(tmp_path), "d1")
    try:
        os.symlink(outside_dir, link)
    except OSError:
        pytest.skip("symlinks need elevated privilege here; no symlink attack tested")
    entry = entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json")
    manifest = build_manifest((entry,))
    with pytest.raises(ManifestError, match="escapes"):
        build_passages(manifest, str(tmp_path))


def test_build_passages_missing_document_fails_closed(tmp_path) -> None:
    doc = _syn_doc("syn-miss-s1:" + "a" * 32, "Alpha beta gamma delta epsilon.")
    entry = entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json")
    manifest = build_manifest((entry,))
    with pytest.raises(ManifestError, match="cannot read"):
        build_passages(manifest, str(tmp_path))


def test_missing_artifact_file_does_not_fail(tmp_path) -> None:
    """Required manifest *references* (document records) must exist, but
    the referenced artifact binaries (PDFs, git-ignored, never read by
    the builder) must not be required on disk."""
    doc = _syn_doc("syn-art-s1:" + "a" * 32, "Alpha beta gamma delta epsilon.")
    os.makedirs(os.path.join(str(tmp_path), "d1"))
    with open(os.path.join(str(tmp_path), "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    assert not os.path.exists(os.path.join(str(tmp_path), "d1", "a.pdf"))
    manifest = build_manifest((
        entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json"),))
    assert len(build_passages(manifest, str(tmp_path))) == 1


def test_missing_required_document_serves_503(tmp_path) -> None:
    doc = _syn_doc("syn-req-s1:" + "a" * 32, "Alpha beta gamma delta epsilon.")
    manifest = build_manifest((
        entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json"),))
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle)
    client = TestClient(create_app(manifest_path=manifest_path, base_dir=str(tmp_path)))
    assert client.post("/ask", json={"question": "alpha"}).status_code == 503
    assert client.get("/ready").status_code == 503


def test_malformed_optional_companion_falls_back(tmp_path) -> None:
    """source.json companions are optional best-effort records (question-API
    contract): a corrupt companion must not take down the corpus."""
    from backend.app.sources import SourceRecord

    doc = _syn_doc("syn-comp-s1:" + "a" * 32, "Alpha beta gamma provisions.")
    os.makedirs(os.path.join(str(tmp_path), "d1"))
    with open(os.path.join(str(tmp_path), "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(doc.to_dict(), handle)
    manifest = build_manifest((
        entry_for(doc, artifact_path="d1/a.pdf", document_path="d1/doc.json"),))
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle)

    for bad in ("{not json", json.dumps({"source_id": 42, "title": 42})):
        with open(os.path.join(str(tmp_path), "d1", "source.json"), "w", encoding="utf-8") as handle:
            handle.write(bad)
        body = TestClient(
            create_app(manifest_path=manifest_path, base_dir=str(tmp_path))).post(
                "/ask", json={"question": "alpha beta"}).json()
        assert body["status"] == "retrieved"
        assert body["passages"][0]["source_title"] == ""
        assert body["passages"][0]["source_verification"] == "unverified"

    source = SourceRecord(
        source_id="syn-comp-s1", title="Companion Present",
        publisher="", origin="", source_type="", jurisdiction="",
        publication="", acquired_at="", verification="unverified", note="")
    with open(os.path.join(str(tmp_path), "d1", "source.json"), "w", encoding="utf-8") as handle:
        json.dump(source.to_dict(), handle)
    body = TestClient(
        create_app(manifest_path=manifest_path, base_dir=str(tmp_path))).post(
            "/ask", json={"question": "alpha beta"}).json()
    assert body["passages"][0]["source_title"] == "Companion Present"


def test_production_cors_defaults_closed(monkeypatch) -> None:
    monkeypatch.setenv("TAXGUARD_ENVIRONMENT", "production")
    monkeypatch.delenv("TAXGUARD_CORS_ORIGINS", raising=False)
    assert get_settings().cors_origins == ()
    monkeypatch.setenv("TAXGUARD_CORS_ORIGINS", "https://app.example.invalid")
    assert get_settings().cors_origins == ("https://app.example.invalid",)


def test_development_cors_stays_convenient(monkeypatch) -> None:
    monkeypatch.setenv("TAXGUARD_ENVIRONMENT", "development")
    monkeypatch.delenv("TAXGUARD_CORS_ORIGINS", raising=False)
    assert get_settings().cors_origins == (
        "http://localhost:3000", "http://127.0.0.1:3000")


def test_malformed_cors_rejected(monkeypatch) -> None:
    for bad in ("*", "https://ok.example.invalid, *", "not-a-url", "ftp://x.invalid"):
        monkeypatch.setenv("TAXGUARD_CORS_ORIGINS", bad)
        with pytest.raises(ValueError, match="CORS"):
            get_settings()


def test_render_ships_no_cors_value_or_placeholder() -> None:
    """Regression guard for the deployment file: production must require
    the real origin via environment config, never a shipped localhost,
    wildcard, or invented placeholder domain. Only shipped `value:` lines
    are checked — comments may discuss them."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "render.yaml"), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    start = next(
        i for i, line in enumerate(lines) if "key: TAXGUARD_CORS_ORIGINS" in line)
    shipped = [line.strip() for line in lines[start + 1:] if line.strip()]
    assert shipped and shipped[0] == "sync: false"
    assert not any(line.startswith("value:") for line in shipped)
    joined = "\n".join(shipped)
    assert "localhost" not in joined
    assert "example.invalid" not in joined
    assert "*" not in joined


def test_request_id_opaque_unique_and_stable_snapshot(tmp_path) -> None:
    client = TestClient(create_app(
        manifest_path=_seed(tmp_path), base_dir=str(tmp_path)))
    first = client.post("/ask", json={"question": "alpha beta"}).json()
    second = client.post("/ask", json={"question": "alpha beta"}).json()
    for body in (first, second):
        assert re.fullmatch(r"[0-9a-f]{16}", body["request_id"])
        assert "alpha" not in body["request_id"]
    assert first["request_id"] != second["request_id"]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["snapshot_id"]
    rest_first = {k: v for k, v in first.items() if k != "request_id"}
    rest_second = {k: v for k, v in second.items() if k != "request_id"}
    assert rest_first == rest_second


def test_lexical_entry_rejects_unsafe_paths(tmp_path) -> None:
    doc = _syn_doc("syn-unsafe-s1:" + "a" * 32, "Alpha beta gamma delta epsilon.")
    for bad in ("../escape.pdf", "/abs/doc.json", "C:/win/doc.json",
                "C:relative.pdf", "..\\escape.pdf", "d1/../../escape.pdf"):
        with pytest.raises(ManifestError, match="relative path"):
            entry_for(doc, artifact_path=bad, document_path="d1/doc.json")
