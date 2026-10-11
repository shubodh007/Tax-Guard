"""Question-to-evidence API tests using SYNTHETIC isolated corpora.

Each app instance gets its own tmp corpus; no live network, no real
artifacts, no shared state. The real corpus is exercised only by the
manual smoke run reported alongside the suite.
"""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.manifest import build_manifest, entry_for
from backend.app.sources import DocumentRecord, SourceRecord


def syn_doc(doc_id: str, text: str) -> DocumentRecord:
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


def seed_corpus(tmp_path, with_source_json: bool = True) -> str:
    first = syn_doc("syn-ask-s1:" + "a" * 32, "Alpha beta gamma provisions.\n\nDelta epsilon.")
    second = syn_doc("syn-ask-s2:" + "b" * 32, "Zeta eta theta notes.")
    os.makedirs(os.path.join(str(tmp_path), "d1"))
    os.makedirs(os.path.join(str(tmp_path), "d2"))
    with open(os.path.join(str(tmp_path), "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(first.to_dict(), handle)
    with open(os.path.join(str(tmp_path), "d2", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(second.to_dict(), handle)
    if with_source_json:
        source = SourceRecord(
            source_id="syn-ask-s1", title="Synthetic Ask Source",
            publisher="Synthetic Test Board", origin="https://example.invalid/syn",
            source_type="schedule", jurisdiction="IN", note="synthetic fixture",
        )
        with open(os.path.join(str(tmp_path), "d1", "source.json"), "w", encoding="utf-8") as handle:
            json.dump(source.to_dict(), handle)
    manifest = build_manifest((
        entry_for(first, artifact_path="d1/a.pdf", document_path="d1/doc.json"),
        entry_for(second, artifact_path="d2/a.pdf", document_path="d2/doc.json"),
    ))
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle)
    return manifest_path


def client_for(tmp_path) -> TestClient:
    return TestClient(create_app(manifest_path=seed_corpus(tmp_path), base_dir=str(tmp_path)))


def test_retrieved_question_returns_evidence(tmp_path) -> None:
    client = client_for(tmp_path)
    body = client.post("/ask", json={"question": "alpha beta"}).json()

    assert body["status"] == "retrieved"
    assert body["reason_code"] == "lexical_match"
    assert len(body["passages"]) == 1
    passage = body["passages"][0]
    assert passage["document_id"] == "syn-ask-s1:" + "a" * 32
    assert passage["source_id"] == "syn-ask-s1"
    assert passage["content_hash"] == "a" * 64
    assert passage["source_title"] == "Synthetic Ask Source"
    assert passage["source_verification"] == "unverified"
    assert passage["rank"] == 1 and isinstance(passage["score"], float)
    assert body["request_id"] and body["snapshot_id"]
    assert "not tax advice" in body["answer"]
    assert "unverified" in body["limitations"]
    # Request ids are opaque per request; evidence is deterministic.
    again = client.post("/ask", json={"question": "alpha beta"}).json()
    assert again["request_id"] != body["request_id"]
    assert {k: v for k, v in again.items() if k != "request_id"} == {
        k: v for k, v in body.items() if k != "request_id"
    }


def test_unrelated_question_abstains(tmp_path) -> None:
    body = client_for(tmp_path).post("/ask", json={"question": "quantum zebra financing"}).json()
    assert body["status"] == "abstained"
    assert body["reason_code"] == "no_matching_evidence"
    assert body["passages"] == []
    assert "abstains" in body["answer"]


def test_invalid_inputs_rejected(tmp_path) -> None:
    client = client_for(tmp_path)
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "   "}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/ask", json={}).status_code == 422
    for bad_limit in (0, 11, "three"):
        assert client.post("/ask", json={"question": "alpha", "limit": bad_limit}).status_code == 422
    assert client.post("/ask", json={"question": "alpha", "limit": 1}).status_code == 200


def test_missing_or_invalid_manifest_gives_503(tmp_path) -> None:
    missing = TestClient(create_app(
        manifest_path=os.path.join(str(tmp_path), "absent.json"), base_dir=str(tmp_path)))
    response = missing.post("/ask", json={"question": "alpha"})
    assert response.status_code == 503
    assert "corpus unavailable" in response.json()["detail"]
    assert missing.get("/health").status_code == 200

    bad_path = os.path.join(str(tmp_path), "manifest.json")
    with open(bad_path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    invalid = TestClient(create_app(manifest_path=bad_path, base_dir=str(tmp_path)))
    assert invalid.post("/ask", json={"question": "alpha"}).status_code == 503


def test_missing_source_companion_falls_back(tmp_path) -> None:
    manifest_path = seed_corpus(tmp_path, with_source_json=False)
    body = TestClient(create_app(manifest_path=manifest_path, base_dir=str(tmp_path))).post(
        "/ask", json={"question": "alpha beta"}).json()
    assert body["status"] == "retrieved"
    assert body["passages"][0]["source_title"] == ""
    assert body["passages"][0]["source_verification"] == "unverified"


def test_instances_are_isolated(tmp_path) -> None:
    first = client_for(tmp_path)
    other_dir = os.path.join(str(tmp_path), "other")
    os.makedirs(os.path.join(other_dir, "d1"))
    other_doc = syn_doc("syn-ask-s9:" + "c" * 32, "Omega psi chi.")
    with open(os.path.join(other_dir, "d1", "doc.json"), "w", encoding="utf-8") as handle:
        json.dump(other_doc.to_dict(), handle)
    other_manifest = build_manifest((
        entry_for(other_doc, artifact_path="d1/a.pdf", document_path="d1/doc.json"),))
    other_manifest_path = os.path.join(other_dir, "manifest.json")
    with open(other_manifest_path, "w", encoding="utf-8") as handle:
        json.dump(other_manifest.to_dict(), handle)
    second = TestClient(create_app(manifest_path=other_manifest_path, base_dir=other_dir))

    first_answer = first.post("/ask", json={"question": "alpha beta"}).json()
    second_answer = second.post("/ask", json={"question": "alpha beta"}).json()
    assert first_answer["status"] == "retrieved"
    assert second_answer["status"] == "abstained"  # other corpus lacks the terms
    assert first_answer["snapshot_id"] != second_answer["snapshot_id"]


def test_serving_never_mutates_corpus_files(tmp_path) -> None:
    manifest_path = seed_corpus(tmp_path)
    before = {}
    for dirpath, _, filenames in os.walk(str(tmp_path)):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with open(path, "rb") as handle:
                before[os.path.relpath(path, str(tmp_path))] = handle.read()
    client = TestClient(create_app(manifest_path=manifest_path, base_dir=str(tmp_path)))
    client.post("/ask", json={"question": "alpha beta"})
    client.post("/ask", json={"question": "quantum zebra"})
    for relpath, data in before.items():
        with open(os.path.join(str(tmp_path), relpath), "rb") as handle:
            assert handle.read() == data


def test_openapi_lists_ask() -> None:
    spec = TestClient(create_app(manifest_path="nonexistent.json")).get("/openapi.json").json()
    assert set(spec["paths"]["/ask"]["post"]["requestBody"]["content"]) == {"application/json"}


REAL_MANIFEST = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "manifest.json")


@pytest.mark.skipif(
    not os.path.exists(REAL_MANIFEST),
    reason="generated corpus manifest absent from this checkout",
)
def test_lexical_overlap_is_labeled_retrieved_not_supported() -> None:
    """Regression test for the Phase 22 observation: 'capital gains
    exemption startup funding' lexically matches unrelated insurance
    text through stray terms. The API must report a lexical match with
    unverified sources — never a verified or legally supported answer."""
    client = TestClient(create_app())
    body = client.post(
        "/ask", json={"question": "capital gains exemption startup funding"}).json()

    assert body["status"] == "retrieved"
    assert body["reason_code"] == "lexical_match"
    assert body["status"] not in ("verified", "supported")
    assert "does not establish legal correctness" in body["answer"]
    assert all(p["source_verification"] == "unverified" for p in body["passages"])
    assert "unverified" in body["limitations"]
    assert body["snapshot_id"]


@pytest.mark.skipif(
    not os.path.exists(REAL_MANIFEST),
    reason="generated corpus manifest absent from this checkout",
)
def test_real_corpus_known_passage_and_abstention() -> None:
    client = TestClient(create_app())
    known = client.post(
        "/ask", json={"question": "life insurance business profits"}).json()
    assert known["status"] == "retrieved"
    assert known["passages"][0]["document_id"].startswith(
        "india-code-it-act-1961-first-schedule:")
    assert known["passages"][0]["source_title"] == "THE FIRST SCHEDULE"

    missing = client.post("/ask", json={"question": "depreciation rates plant"}).json()
    assert missing["status"] == "abstained"
    assert missing["passages"] == []


def test_clean_checkout_builds_without_pdfs_or_manifest(tmp_path) -> None:
    """Simulates a fresh deploy checkout: document.json records only, no
    manifest.json and no PDF binaries. The documented build command must
    regenerate a working manifest."""
    import shutil
    import subprocess

    for leaf in ("first-schedule", "second-schedule"):
        src = os.path.join(os.path.dirname(__file__), "..", "corpus",
                           "india-code", f"income-tax-act-1961-{leaf}")
        if not os.path.exists(os.path.join(src, "document.json")):
            pytest.skip("acquired artifacts absent from this checkout")
        dst = os.path.join(str(tmp_path), "corpus", "india-code",
                           f"income-tax-act-1961-{leaf}")
        os.makedirs(dst)
        shutil.copy(os.path.join(src, "document.json"), dst)
        shutil.copy(os.path.join(src, "source.json"), dst)
    assert not os.path.exists(os.path.join(str(tmp_path), "corpus", "manifest.json"))

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "scripts", "build_manifest.py")
    completed = subprocess.run(
        [sys.executable, script, "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    manifest_path = os.path.join(str(tmp_path), "corpus", "manifest.json")
    body = TestClient(create_app(manifest_path=manifest_path, base_dir=str(tmp_path))).post(
        "/ask", json={"question": "life insurance business profits"}).json()
    assert body["status"] == "retrieved"
    assert len(body["passages"]) >= 1


def test_cors_origins_configurable(monkeypatch) -> None:
    from backend.app.config import get_settings

    monkeypatch.setenv("TAXGUARD_CORS_ORIGINS", "https://demo.example.invalid")
    assert get_settings().cors_origins == ("https://demo.example.invalid",)
    monkeypatch.delenv("TAXGUARD_CORS_ORIGINS")
    assert get_settings().cors_origins == ("http://localhost:3000", "http://127.0.0.1:3000")
