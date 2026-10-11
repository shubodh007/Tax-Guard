"""Inspection API tests using SYNTHETIC fixtures only.

Covers health preservation, exact trace/answer payloads, 404/422/405
behavior, registry isolation, and the absence of write endpoints.
No external services required.
"""

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.registry import Registry

from _synthetic import synthetic_answer, synthetic_trace


def seeded_client() -> TestClient:
    registry = Registry()
    trace = synthetic_trace()
    registry.register_trace(trace)
    registry.register_answer(trace, synthetic_answer())
    return TestClient(create_app(registry))


def test_health_unchanged() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "TaxGuard", "version": "0.1.0"}


def test_trace_returns_exact_contract() -> None:
    trace = synthetic_trace()
    client = seeded_client()
    response = client.get("/traces/syn-trace-1")

    assert response.status_code == 200
    assert response.json() == trace.to_dict()
    assert set(response.json()) == set(trace.to_dict())


def test_answer_returns_exact_contract() -> None:
    answer = synthetic_answer()
    client = seeded_client()
    response = client.get("/answers/syn-trace-1")

    assert response.status_code == 200
    assert response.json() == answer.to_dict()
    assert response.json()["status"] == "draft"
    assert response.json()["reason_code"] == "verification_incomplete"


def test_unknown_ids_return_404_without_fabrication() -> None:
    client = seeded_client()
    for path in ("/traces/syn-absent", "/answers/syn-absent"):
        response = client.get(path)
        assert response.status_code == 404
        assert set(response.json()) == {"detail"}
        assert "trace" not in response.json() and "claims" not in response.json()


def test_empty_registry_returns_404() -> None:
    client = TestClient(create_app())
    assert client.get("/traces/syn-trace-1").status_code == 404
    assert client.get("/answers/syn-trace-1").status_code == 404


def test_app_instances_are_isolated() -> None:
    first, second = seeded_client(), TestClient(create_app())
    assert first.get("/traces/syn-trace-1").status_code == 200
    assert second.get("/traces/syn-trace-1").status_code == 404
    assert second.get("/answers/syn-trace-1").status_code == 404


def test_responses_do_not_leak_other_records() -> None:
    registry = Registry()
    for trace_id in ("syn-trace-1", "syn-trace-2"):
        trace = synthetic_trace(trace_id)
        registry.register_trace(trace)
        registry.register_answer(trace, synthetic_answer(trace_id))
    client = TestClient(create_app(registry))

    trace_body = client.get("/traces/syn-trace-1").json()
    assert trace_body["trace_id"] == "syn-trace-1"
    assert "syn-trace-2" not in str(trace_body)
    answer_body = client.get("/answers/syn-trace-2").json()
    assert answer_body["trace_id"] == "syn-trace-2"
    assert "syn-trace-1" not in str(answer_body)


def test_invalid_path_input_is_client_error() -> None:
    client = seeded_client()
    response = client.get("/traces/%20")
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}


def test_api_exposes_no_write_endpoint() -> None:
    client = seeded_client()
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/traces/syn-trace-1").status_code == 405
        assert getattr(client, method)("/answers/syn-trace-1").status_code == 405
    spec = client.get("/openapi.json").json()
    for path in ("/health", "/traces/{trace_id}", "/answers/{trace_id}"):
        assert set(spec["paths"][path]) == {"get"}
