"""Reverification tests using SYNTHETIC values and a local server only.

Nothing here contacts the live internet or verifies a real source.
Real-artifact results are reported in docs/source-fragment-acquisition.md.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.app.acquisition import (
    AcquireConfig,
    AcquisitionError,
    AcquiredArtifact,
    AcquisitionReceipt,
    fetch_json_metadata,
)
from backend.app.source_verification import (
    ExpectedArtifact,
    ObservedArtifact,
    OfficialRecord,
    SourceVerificationError,
    VerificationCheck,
    VerificationPolicy,
    assess,
    reconcile,
)
from backend.app.sources import DocumentRecord, SourceRecord

SHA = "a" * 64
OTHER_SHA = "b" * 64


def syn_source(**overrides) -> SourceRecord:
    fields = {
        "source_id": "syn-source-1",
        "title": "SYNTHETIC FIRST SCHEDULE",
        "publisher": "Synthetic Test Board",
        "origin": "https://example.invalid/syn",
        "source_type": "schedule",
        "jurisdiction": "IN",
        "note": "synthetic fixture",
    }
    fields.update(overrides)
    return SourceRecord(**fields)  # type: ignore[arg-type]


def syn_document(hash_value: str = SHA) -> DocumentRecord:
    return DocumentRecord(
        document_id=f"syn-source-1:{hash_value[:32]}",
        source_id="syn-source-1",
        filename="syn.pdf",
        format="pdf",
        parser_id="taxguard-sources-v1:pdf:pypdf-9.9.9",
        content_hash=hash_value,
        size_bytes=4,
        normalized_text="synthetic",
        extraction_status="ok",
        source_verification="unverified",
    )


def official(**overrides) -> OfficialRecord:
    fields = {
        "record_url": "https://example.invalid/syn-record",
        "title": "SYNTHETIC FIRST SCHEDULE",
        "act_label": "Synthetic Test Act (No. 43 of 1961)",
        "size_bytes": 0,
    }
    fields.update(overrides)
    return OfficialRecord(**fields)  # type: ignore[arg-type]


def by_type(checks):
    return {c.check_type: c for c in checks}


def test_integrity_match_and_mismatch() -> None:
    passed = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
    )
    assert by_type(passed)["integrity"].outcome == "passed"
    assert by_type(passed)["integrity"].observation_source == "local"

    for observed, expected in [
        (ObservedArtifact(OTHER_SHA, 4), ExpectedArtifact(SHA, 4)),
        (ObservedArtifact(SHA, 5), ExpectedArtifact(SHA, 4)),
    ]:
        failed = reconcile(syn_source(), observed=observed, expected=expected)
        assert by_type(failed)["integrity"].outcome == "failed"

    missing = reconcile(
        syn_source(),
        observed=ObservedArtifact("", 0),
        expected=ExpectedArtifact(SHA, 4),
    )
    assert by_type(missing)["integrity"].outcome == "inconclusive"


def test_document_chain_noted_or_failed() -> None:
    matching = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        document=syn_document(SHA),
    )
    integrity = by_type(matching)["integrity"]
    assert integrity.outcome == "passed"
    assert syn_document(SHA).document_id in integrity.observation

    broken = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        document=syn_document(OTHER_SHA),
    )
    assert by_type(broken)["integrity"].outcome == "failed"

    with pytest.raises(SourceVerificationError, match="belongs to source"):
        reconcile(
            syn_source(),
            observed=ObservedArtifact(SHA, 4),
            expected=ExpectedArtifact(SHA, 4),
            document=DocumentRecord(**{**syn_document().to_dict(), "source_id": "other"}),
        )


def test_record_identity_outcomes() -> None:
    matched = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        official=official(),
        evidence_origin="network",
    )
    identity = by_type(matched)["record_identity"]
    assert identity.outcome == "passed"
    assert identity.observation_source == "network"
    assert "https://example.invalid/syn-record" in identity.observation

    conflicted = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        official=official(title="SYNTHETIC SECOND SCHEDULE"),
    )
    identity = by_type(conflicted)["record_identity"]
    assert identity.outcome == "failed"
    assert "SYNTHETIC SECOND SCHEDULE" in identity.observation
    assert "SYNTHETIC FIRST SCHEDULE" in identity.observation

    absent = reconcile(
        syn_source(), observed=ObservedArtifact(SHA, 4), expected=ExpectedArtifact(SHA, 4)
    )
    assert by_type(absent)["record_identity"].outcome == "not_checked"

    no_title = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        official=official(title=""),
    )
    assert by_type(no_title)["record_identity"].outcome == "inconclusive"


def test_size_discrepancy_reported_verbatim() -> None:
    noted = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        official=official(size_bytes=9),
    )
    assert "size discrepancy" in by_type(noted)["record_identity"].observation

    corroborated = reconcile(
        syn_source(),
        observed=ObservedArtifact(SHA, 4),
        expected=ExpectedArtifact(SHA, 4),
        official=official(size_bytes=4),
    )
    assert "corroborates" in by_type(corroborated)["record_identity"].observation


def test_unknown_dimensions_stay_unchecked() -> None:
    checks = reconcile(
        syn_source(), observed=ObservedArtifact(SHA, 4), expected=ExpectedArtifact(SHA, 4)
    )
    verdicts = {c.check_type: c.outcome for c in checks}
    assert verdicts["version"] == "not_checked"
    assert verdicts["completeness"] == "not_checked"
    assert verdicts["applicability"] == "not_checked"
    assert "fragment scope" in by_type(checks)["completeness"].observation

    assessment = assess(
        "syn-source-1", checks,
        VerificationPolicy("synthetic-policy", "v1", ("integrity", "record_identity")),
    )
    assert assessment.status == "incomplete"
    assert "missing:record_identity" in assessment.reason_codes


def test_malformed_receipt_rejected() -> None:
    with pytest.raises(AcquisitionError, match="mapping"):
        AcquisitionReceipt.from_dict([])  # type: ignore[arg-type]
    with pytest.raises(AcquisitionError, match="sha256"):
        AcquisitionReceipt.from_dict({
            "source_id": "s", "artifact_id": "a", "title": "t", "publisher": "",
            "source_page_url": "u", "artifact_url": "u", "final_url": "u",
            "redirect_chain": [], "acquired_at": "now", "http_status": 200,
            "content_type": "", "filename": "f", "size_bytes": 1,
            "sha256": "not-a-hash", "format": "pdf", "parsed": False,
            "document_id": "", "verification": "unverified",
            "limitations": ("synthetic",),
        })


def test_reconcile_input_validation() -> None:
    source = syn_source()
    with pytest.raises(SourceVerificationError, match="must be a SourceRecord"):
        reconcile("nope", observed=ObservedArtifact(), expected=ExpectedArtifact())  # type: ignore[arg-type]
    with pytest.raises(SourceVerificationError, match="evidence_origin"):
        reconcile(source, observed=ObservedArtifact(), expected=ExpectedArtifact(),
                  evidence_origin="carrier-pigeon")
    with pytest.raises(SourceVerificationError, match="SHA-256"):
        ObservedArtifact("xyz", 4)
    with pytest.raises(SourceVerificationError, match="observation_source"):
        VerificationCheck("c", "s", "origin", "passed", "d", "o", observation_source="rumor")


def test_observation_source_serialization_and_compat() -> None:
    check = VerificationCheck("c", "s", "origin", "passed", "d", "o",
                              observation_source="network")
    assert VerificationCheck.from_dict(json.loads(json.dumps(check.to_dict()))) == check
    legacy = {"check_id": "c", "source_id": "s", "check_type": "origin",
              "outcome": "passed", "description": "d", "observation": "o"}
    assert VerificationCheck.from_dict(legacy).observation_source == ""


def test_no_mutation_of_inputs(tmp_path) -> None:
    source = syn_source()
    snapshot = source.to_dict()
    target = tmp_path / "syn.pdf"
    target.write_bytes(b"%PDF-1.4 synthetic")
    before = target.read_bytes()
    reconcile(
        source, observed=ObservedArtifact(SHA, 4), expected=ExpectedArtifact(SHA, 4),
        official=official(), document=syn_document(),
    )
    assert source.to_dict() == snapshot
    assert target.read_bytes() == before


class MetaHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self):  # noqa: ANN201, ANN202 - stdlib signature
        if self.path == "/item.json":
            body = b'{"title": "SYNTHETIC FIRST SCHEDULE"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/array.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[1, 2]")
        elif self.path == "/text":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")
        elif self.path == "/slow":
            import time

            time.sleep(2.0)
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(b"{}")
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/offscope":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/item.json")
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"k": "' + b"x" * 1000 + b'"}')
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def meta_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MetaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://approved.test:{server.server_address[1]}"
    server.shutdown()


def local_config(**overrides) -> AcquireConfig:
    fields = {"allowed_host_suffixes": ("127.0.0.1",)}
    fields.update(overrides)
    return AcquireConfig(**fields)  # type: ignore[arg-type]


def test_fetch_json_metadata(meta_url: str, monkeypatch) -> None:
    def fake_fetch(url: str, config: AcquireConfig):
        if not url.startswith("https://"):
            raise AcquisitionError("acquisition URL must use HTTPS")
        path = url.rsplit("/", 1)[-1]
        if path == "item.json":
            data = b'{"title": "SYNTHETIC FIRST SCHEDULE"}'
        elif path == "array.json":
            data = b"[1, 2]"
        elif path == "text":
            data = b"<html></html>"
        elif path == "slow":
            raise AcquisitionError("fetch failed for synthetic timeout")
        elif path == "offscope":
            raise AcquisitionError("redirect target outside allowed scope")
        elif path == "big":
            data = b'{"k": "' + b"x" * 1000 + b'"}'
        else:
            raise AcquisitionError("HTTP 404 for synthetic metadata")
        if len(data) > config.max_bytes:
            raise AcquisitionError("response exceeds size limit")
        return (
            AcquiredArtifact(
                source_url=url,
                final_url=url,
                redirect_chain=(),
                http_status=200,
                content_type="application/json",
                size_bytes=len(data),
                sha256="a" * 64,
                acquired_at="2026-10-09T00:00:00+00:00",
            ),
            data,
        )

    monkeypatch.setattr("backend.app.acquisition.fetch_artifact", fake_fetch)
    payload, meta = fetch_json_metadata(f"{meta_url}/item.json", local_config())
    assert payload == {"title": "SYNTHETIC FIRST SCHEDULE"}
    assert meta.http_status == 200 and meta.final_url == f"{meta_url}/item.json"

    with pytest.raises(AcquisitionError, match="JSON object"):
        fetch_json_metadata(f"{meta_url}/array.json", local_config())
    with pytest.raises(AcquisitionError, match="decodable JSON"):
        fetch_json_metadata(f"{meta_url}/text", local_config())
    with pytest.raises(AcquisitionError, match="HTTP 404"):
        fetch_json_metadata(f"{meta_url}/absent", local_config())
    with pytest.raises(AcquisitionError, match="timed out|failed"):
        fetch_json_metadata(f"{meta_url}/slow", local_config(timeout_seconds=0.2))
    with pytest.raises(AcquisitionError, match="outside allowed scope"):
        fetch_json_metadata(f"{meta_url}/offscope", local_config())
    with pytest.raises(AcquisitionError, match="size limit"):
        fetch_json_metadata(f"{meta_url}/big", local_config(), max_bytes=10)
    with pytest.raises(AcquisitionError, match="HTTPS"):
        fetch_json_metadata("http://example.invalid/item.json", AcquireConfig())
