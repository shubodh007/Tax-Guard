"""Evidence-eligibility, abstention, and reliability tests (Phase 31).

End-to-end traces of POST /ask over the read-only real corpus:
retrieved passages are never legal authority, unverified status and
provenance survive to the response, unsupported/empty queries
abstain, malformed inputs fail with status codes (never diagnostics),
and ranking is deterministic. Includes a repeatable local latency
smoke measurement for the retrieval layer.
"""

import time

from fastapi.testclient import TestClient

from backend.app.main import CORPUS_UNAVAILABLE, LIMITATIONS, create_app
from backend.app.manifest import build_passages, load_manifest_file
from backend.app.retrieval import retrieve_passages


def real_client() -> TestClient:
    return TestClient(create_app(
        manifest_path="corpus/manifest.json", base_dir=".",
    ))


def test_statuses_never_confer_authority(tmp_path) -> None:
    """Every /ask outcome is retrieved-with-limits or abstained; no
    response path returns a supporting/verifying status."""
    client = real_client()
    for question in (
        "profits of life insurance business computed separately",
        "tax",
        "First Schedule as amended in 2024",
        "Second Schedule assessment year 2026-27",
        "What is the tax rate for individuals",
        "depreciation rates for machinery",
        "xyzzy qwerty asdfgh",
    ):
        body = client.post("/ask", json={"question": question}).json()
        assert body["status"] in ("retrieved", "abstained"), question
        assert body["status"] not in ("supported", "verified"), question
        assert body["limitations"] == LIMITATIONS, question
        assert body["snapshot_id"] == "77fdc5329096755a58d09d6c5e78d52a55685021969e3dd019116f4bb37c4d31"


def test_retrieved_passages_carry_unverified_evidence() -> None:
    """Supported, ambiguous, and version-sensitive queries retrieve,
    but every passage is flagged unverified with provenance intact and
    the answer disclaims legal force."""
    client = real_client()
    for question in (
        "profits of life insurance business computed separately",
        "tax",
        "First Schedule as amended in 2024",
    ):
        body = client.post("/ask", json={"question": question}).json()
        assert body["status"] == "retrieved", question
        assert body["passages"], question
        for passage in body["passages"]:
            assert passage["source_verification"] == "unverified", question
            assert passage["passage_id"] and passage["document_id"] and passage["source_id"]
            assert "assessment" in passage["unresolved"], question
        assert "does not establish legal correctness" in body["answer"], question


def test_empty_queries_abstain_with_limits() -> None:
    """Queries with no lexical match abstain rather than guessing."""
    client = real_client()
    for question in ("depreciation", "xyzzy qwerty asdfgh"):
        body = client.post("/ask", json={"question": question}).json()
        assert body["status"] == "abstained", question
        assert body["reason_code"] == "no_matching_evidence", question
        assert body["passages"] == [], question
        assert body["limitations"] == LIMITATIONS, question


def test_ranking_deterministic_across_calls() -> None:
    """Same question twice yields identical ids, scores, and ranks."""
    client = real_client()
    first = client.post("/ask", json={"question": "tax", "limit": 5}).json()
    second = client.post("/ask", json={"question": "tax", "limit": 5}).json()
    assert first["status"] == "retrieved" == second["status"]
    assert [(p["passage_id"], p["score"], p["rank"]) for p in first["passages"]] == [
        (p["passage_id"], p["score"], p["rank"]) for p in second["passages"]
    ]


def test_malformed_inputs_rejected_without_diagnostics() -> None:
    """Blank, overlong, and out-of-range inputs fail validation; error
    bodies carry no paths or internals."""
    client = real_client()
    for payload in (
        {"question": "   "},
        {"question": "x" * 501},
        {"question": "tax", "limit": 0},
        {"question": "tax", "limit": 11},
        {"question": 42},
    ):
        response = client.post("/ask", json=payload)
        assert response.status_code == 422, payload
        assert "D:" not in response.text and "corpus" not in response.text.lower(), payload


def test_failures_stay_generic_without_paths(tmp_path) -> None:
    """Corrupt corpus and unknown ids serve generic bodies with no
    filesystem paths or exception text."""
    bad = TestClient(create_app(
        manifest_path=str(tmp_path / "absent-manifest.json"), base_dir=str(tmp_path),
    ))
    assert bad.get("/ready").status_code == 503
    response = bad.post("/ask", json={"question": "tax"})
    assert response.status_code == 503
    assert response.json() == {"detail": CORPUS_UNAVAILABLE}
    assert "D:" not in response.text

    client = real_client()
    missing = client.get("/traces/does-not-exist")
    assert missing.status_code == 404
    assert "D:" not in missing.text and "corpus" not in missing.text.lower()


def test_retrieval_latency_smoke() -> None:
    """Repeatable local latency measurement over the quality-set query
    texts (91 passages). Smoke bound only: guards against pathological
    slowdown, claims no optimization."""
    passages = build_passages(load_manifest_file("corpus/manifest.json"), ".")
    queries = [
        "profits of life insurance business computed separately",
        "adjustment of tax paid by deduction at source",
        "deposit by purchaser on resale in default",
        "Insurance Act 1938 profit and loss account",
        "gains on realisation of investments added back",
        "notice requiring payment within fifteen days of service",
        "tax",
        "depreciation rates for machinery",
    ]
    for query in queries:  # warmup, excluded from measurement
        retrieve_passages(query, passages, limit=5)
    repetitions, total = 50, 0.0
    for _ in range(repetitions):
        start = time.perf_counter()
        for query in queries:
            retrieve_passages(query, passages, limit=5)
        total += time.perf_counter() - start
    per_call_ms = total / (repetitions * len(queries)) * 1000.0
    print(f"\nretrieval latency: {per_call_ms:.3f} ms/call "
          f"({len(passages)} passages, {len(queries)} queries x{repetitions})")
    assert per_call_ms < 1000.0
