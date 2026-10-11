"""Acquisition tests against a local HTTP server only.

No live network access: every fetch targets 127.0.0.1. Real India Code
acquisition is documented in docs/source-acquisition-record.md, not
performed here. All fixtures are synthetic.
"""

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

import pytest

from backend.app.acquisition import (
    AcquireConfig,
    AcquisitionError,
    AcquisitionReceipt,
    fetch_artifact,
    integrity_check,
    origin_observation,
    store_artifact,
    verify_stored_artifact,
    write_receipt_file,
)
from backend.app.sources import IngestionError, SourceRecord, SourceRegistry, ingest_file

BODY = b"%PDF-1.4 synthetic artifact bytes for tests only"
BASE_URL = "https://approved.test"


class _Response:
    def __init__(self, url: str, status: int = 200, body: bytes = BODY, location: str = "") -> None:
        self._url = url
        self.status = status
        self._body = body
        self.headers = {"Content-Type": "application/pdf"}
        if location:
            self.headers["Location"] = location

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        body, self._body = self._body[:size], self._body[size:]
        return body


class _Opener:
    def __init__(self, redirector) -> None:
        self.redirector = redirector

    def open(self, request, timeout: float):
        del timeout
        url = request.get_full_url()
        path = urllib.parse.urlparse(url).path
        if path == "/file.pdf":
            return _Response(url)
        if path == "/redir":
            response = _Response(url, status=302, location=f"{BASE_URL}/file.pdf")
            redirected = self.redirector.redirect_request(
                request, response, 302, "found", response.headers, response.headers["Location"]
            )
            return self.open(redirected, 0)
        if path == "/loop":
            response = _Response(url, status=302, location=f"{BASE_URL}/loop")
            redirected = self.redirector.redirect_request(
                request, response, 302, "found", response.headers, response.headers["Location"]
            )
            return self.open(redirected, 0)
        if path == "/elsewhere":
            response = _Response(url, status=302, location="https://disallowed.test/file.pdf")
            self.redirector.redirect_request(
                request, response, 302, "found", response.headers, response.headers["Location"]
            )
        if path == "/slow":
            raise urllib.error.URLError(socket.timeout())
        if path == "/missing":
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if path == "/broken":
            raise urllib.error.HTTPError(url, 500, "broken", {}, None)
        return _Response(url, status=404, body=b"")


@pytest.fixture()
def server_url(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]
        if host in {"approved.test", "disallowed.test"}
        else [],
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda redirector: _Opener(redirector))
    return BASE_URL


def local_config(**overrides) -> AcquireConfig:
    fields = {"allowed_host_suffixes": ("approved.test",)}
    fields.update(overrides)
    return AcquireConfig(**fields)  # type: ignore[arg-type]


def receipt_for(artifact, filename="syn-act.pdf") -> AcquisitionReceipt:
    return AcquisitionReceipt(
        source_id="syn-source-1",
        artifact_id=f"syn-source-1:{artifact.sha256[:32]}",
        title="Synthetic test artifact",
        publisher="",
        source_page_url="http://127.0.0.1/syn-page",
        artifact_url=artifact.source_url,
        final_url=artifact.final_url,
        redirect_chain=artifact.redirect_chain,
        acquired_at=artifact.acquired_at,
        http_status=artifact.http_status,
        content_type=artifact.content_type,
        filename=filename,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        format="pdf",
        parsed=False,
        document_id="",
        verification="unverified",
        limitations=("synthetic test receipt; authenticity not established",),
    )


def test_receipt_from_observed_metadata(server_url: str) -> None:
    artifact, data = fetch_artifact(f"{server_url}/file.pdf", local_config())

    assert artifact.http_status == 200
    assert artifact.final_url == f"{server_url}/file.pdf"
    assert artifact.redirect_chain == ()
    assert artifact.content_type == "application/pdf"
    assert artifact.size_bytes == len(BODY)
    assert artifact.sha256 == hashlib.sha256(BODY).hexdigest()
    assert data == BODY

    receipt = receipt_for(artifact)
    assert receipt.size_bytes == len(BODY)
    assert receipt.verification == "unverified"
    assert AcquisitionReceipt.from_dict(json.loads(json.dumps(receipt.to_dict()))) == receipt


def test_hash_mismatch_detected(tmp_path, server_url: str) -> None:
    artifact, data = fetch_artifact(f"{server_url}/file.pdf", local_config())
    path = store_artifact(data, tmp_path, "syn-act.pdf")
    verify_stored_artifact(path, artifact.sha256)
    with open(path, "wb") as handle:
        handle.write(b"tampered bytes")
    with pytest.raises(AcquisitionError, match="hash mismatch"):
        verify_stored_artifact(path, artifact.sha256)
    with pytest.raises(AcquisitionError, match="cannot read"):
        verify_stored_artifact(tmp_path / "absent.pdf", artifact.sha256)


def test_size_limit_enforced(server_url: str) -> None:
    with pytest.raises(AcquisitionError, match="size limit"):
        fetch_artifact(f"{server_url}/file.pdf", local_config(max_bytes=len(BODY) - 1))
    artifact, _ = fetch_artifact(f"{server_url}/file.pdf", local_config(max_bytes=len(BODY)))
    assert artifact.size_bytes == len(BODY)


def test_timeout_and_http_errors(server_url: str) -> None:
    with pytest.raises(AcquisitionError, match="timed out|failed"):
        fetch_artifact(f"{server_url}/slow", local_config(timeout_seconds=0.2))
    with pytest.raises(AcquisitionError, match="HTTP 404"):
        fetch_artifact(f"{server_url}/missing", local_config())
    with pytest.raises(AcquisitionError, match="HTTP 500"):
        fetch_artifact(f"{server_url}/broken", local_config())


def test_redirect_policy(server_url: str) -> None:
    artifact, data = fetch_artifact(f"{server_url}/redir", local_config())
    assert data == BODY
    assert artifact.final_url == f"{server_url}/file.pdf"
    assert artifact.redirect_chain == (f"{server_url}/file.pdf",)

    with pytest.raises(AcquisitionError, match="redirect"):
        fetch_artifact(f"{server_url}/loop", local_config(max_redirects=2))
    with pytest.raises(AcquisitionError, match="outside allowed scope"):
        fetch_artifact(f"{server_url}/elsewhere", local_config())


def test_unverified_origin_and_scheme_rejected() -> None:
    with pytest.raises(AcquisitionError, match="HTTPS"):
        fetch_artifact("http://example.invalid/file.pdf", AcquireConfig())
    with pytest.raises(AcquisitionError, match="HTTPS"):
        fetch_artifact("ftp://example.invalid/file.pdf", local_config())
    with pytest.raises(AcquisitionError):
        fetch_artifact("  ", local_config())


def test_url_structure_and_destination_policy(server_url: str) -> None:
    with pytest.raises(AcquisitionError, match="credentials"):
        fetch_artifact("https://user:password@approved.test/file.pdf", local_config())
    with pytest.raises(AcquisitionError, match="allowed scope"):
        fetch_artifact("https://approved.test.evil.example/file.pdf", local_config())
    with pytest.raises(AcquisitionError, match="fragment"):
        fetch_artifact(f"{server_url}/file.pdf#fragment", local_config())


def test_no_silent_overwrite(tmp_path, server_url: str) -> None:
    _, data = fetch_artifact(f"{server_url}/file.pdf", local_config())
    first = store_artifact(data, tmp_path, "syn-act.pdf")
    assert store_artifact(data, tmp_path, "syn-act.pdf") == first
    with pytest.raises(AcquisitionError, match="refusing to overwrite"):
        store_artifact(b"different bytes", tmp_path, "syn-act.pdf")
    with pytest.raises(AcquisitionError, match="unsafe filename"):
        store_artifact(data, tmp_path, "../escape.pdf")

    receipt = receipt_for(fetch_artifact(f"{server_url}/file.pdf", local_config())[0])
    receipt_path = write_receipt_file(receipt, tmp_path)
    assert write_receipt_file(receipt, tmp_path) == receipt_path
    altered = AcquisitionReceipt(**{**receipt.to_dict(), "size_bytes": receipt.size_bytes + 1})
    with pytest.raises(AcquisitionError, match="refusing to overwrite"):
        write_receipt_file(altered, tmp_path)


def test_unknowns_stay_unknown_and_no_fake_checks() -> None:
    receipt = AcquisitionReceipt(
        source_id="syn-source-1",
        artifact_id="syn-source-1:" + "0" * 32,
        title="Synthetic test artifact",
        publisher="",
        source_page_url="http://127.0.0.1/syn-page",
        artifact_url="http://127.0.0.1/syn.pdf",
        final_url="http://127.0.0.1/syn.pdf",
        redirect_chain=(),
        acquired_at="2026-10-09T00:00:00+00:00",
        http_status=200,
        content_type="",
        filename="syn.pdf",
        size_bytes=4,
        sha256="0" * 64,
        format="pdf",
        parsed=False,
        document_id="",
        verification="unverified",
        limitations=("synthetic test receipt",),
    )
    assert receipt.publisher == "" and receipt.content_type == ""
    assert receipt.verification == "unverified"
    with pytest.raises(AcquisitionError, match="never marks"):
        AcquisitionReceipt(**{**receipt.to_dict(), "verification": "verified"})

    integrity = integrity_check("syn-source-1", "syn-check-1", "0" * 64, 4)
    assert integrity.outcome == "passed" and integrity.check_type == "integrity"
    origin = origin_observation("syn-source-1", "syn-check-2", "http://127.0.0.1/syn.pdf", 200)
    assert origin.outcome == "inconclusive"  # never auto-passed


def test_unsupported_format_preserved_unparsed(tmp_path) -> None:
    target = tmp_path / "syn-act.dat"
    target.write_bytes(BODY)
    registry = SourceRegistry()
    registry.register(SourceRecord(source_id="syn-source-1", title="Synthetic test source"))
    with pytest.raises(IngestionError, match="unsupported"):
        ingest_file(registry, "syn-source-1", target)
    assert target.read_bytes() == BODY  # original preserved


def test_receipt_filenames_stay_distinct_across_artifacts(tmp_path, server_url: str) -> None:
    artifact, _ = fetch_artifact(f"{server_url}/file.pdf", local_config())
    first = receipt_for(artifact)
    second = AcquisitionReceipt(**{
        **first.to_dict(),
        "source_id": "syn-source-2",
        "artifact_id": "syn-source-2:" + artifact.sha256[:32],
    })
    first_path = write_receipt_file(first, tmp_path)
    second_path = write_receipt_file(second, tmp_path)
    # Colons in artifact IDs are sanitized; distinct artifacts never collide.
    assert first_path.endswith(".receipt.json") and ":" not in first_path.split("\\")[-1]
    assert first_path != second_path
    stored = AcquisitionReceipt.from_dict(json.loads(open(second_path, encoding="utf-8").read()))
    assert stored == AcquisitionReceipt.from_dict(second.to_dict())
