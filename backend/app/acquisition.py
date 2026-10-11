"""Controlled single-artifact acquisition over plain HTTPS. Standard library only.

Fetches exactly one explicitly supplied URL with a size limit, timeout,
and explicit redirect policy; stores bytes atomically without silent
overwrites; and records provenance receipts from observed values only.
No crawling, no mirrors, no access-control bypass, no PDF parsing, no
database, no LLM. Origin scope is gated to an explicit host-suffix
allowlist (default: Indian government domains). See
``docs/source-acquisition-record.md``.

A fetched byte stream proves nothing beyond its own identity: hash is
content identity, not authenticity; retrieval is not verification.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from backend.app.source_verification import VerificationCheck

__all__ = [
    "AcquireConfig",
    "AcquiredArtifact",
    "AcquisitionError",
    "AcquisitionReceipt",
    "fetch_artifact",
    "fetch_json_metadata",
    "integrity_check",
    "origin_observation",
    "store_artifact",
    "verify_stored_artifact",
    "write_receipt_file",
]


class AcquisitionError(ValueError):
    """Acquisition failure: disallowed origin, timeout, HTTP error,
    redirect violation, size violation, unreadable file, hash mismatch,
    or refusal to overwrite. Messages name locations, never secrets."""


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _host_allowed(hostname: str, suffixes: tuple[str, ...]) -> bool:
    host = hostname.rstrip(".").lower()
    return any(
        host == suffix.lstrip(".").rstrip(".").lower()
        or host.endswith("." + suffix.lstrip(".").rstrip(".").lower())
        for suffix in suffixes
    )


def _validate_url(url: str, suffixes: tuple[str, ...], *, context: str) -> urllib.parse.ParseResult:
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        raise AcquisitionError(f"{context} has an invalid host") from None
    if parsed.scheme != "https":
        raise AcquisitionError(f"{context} must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise AcquisitionError(f"{context} must not include URL credentials")
    if parsed.hostname is None or not parsed.hostname.strip():
        raise AcquisitionError(f"{context} has an invalid host")
    if port is not None and not 1 <= port <= 65535:
        raise AcquisitionError(f"{context} has an invalid port")
    if parsed.fragment:
        raise AcquisitionError(f"{context} must not include a fragment")
    if not _host_allowed(parsed.hostname, suffixes):
        raise AcquisitionError(f"{context} is outside allowed scope")
    return parsed


def _validate_destination(parsed: urllib.parse.ParseResult, *, context: str) -> None:
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror):
        raise AcquisitionError(f"{context} host could not be resolved") from None
    if not addresses:
        raise AcquisitionError(f"{context} host has no addresses")
    for address in {entry[4][0] for entry in addresses}:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise AcquisitionError(f"{context} resolved to an invalid address") from None
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise AcquisitionError(f"{context} resolves to a prohibited address")


@dataclass(frozen=True)
class AcquireConfig:
    """Fetch policy. `allowed_host_suffixes` scopes origins explicitly;
    `max_bytes` bounds memory; `timeout_seconds` bounds waiting;
    `max_redirects` bounds redirect following."""

    max_bytes: int = 20_000_000
    timeout_seconds: float = 30.0
    max_redirects: int = 3
    allowed_host_suffixes: tuple[str, ...] = (".gov.in",)
    user_agent: str = "TaxGuard-acquisition/0.1"

    def __post_init__(self) -> None:
        if not isinstance(self.max_bytes, int) or isinstance(self.max_bytes, bool) or self.max_bytes <= 0:
            raise AcquisitionError("max_bytes must be a positive integer")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(
            self.timeout_seconds, bool
        ) or self.timeout_seconds <= 0:
            raise AcquisitionError("timeout_seconds must be a positive number")
        if not isinstance(self.max_redirects, int) or isinstance(self.max_redirects, bool) or self.max_redirects < 0:
            raise AcquisitionError("max_redirects must be a non-negative integer")
        if any(not isinstance(s, str) or not s.strip() for s in self.allowed_host_suffixes):
            raise AcquisitionError("allowed_host_suffixes must be a tuple of non-empty strings")
        _require_label("user_agent", self.user_agent)


@dataclass(frozen=True)
class AcquiredArtifact:
    """Observed fetch outcome. Every field is measured, never assumed:
    `final_url` and `redirect_chain` come from the actual responses,
    `sha256` from the received bytes, `acquired_at` from the local
    clock at fetch time."""

    source_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    http_status: int
    content_type: str
    size_bytes: int
    sha256: str
    acquired_at: str

    def __post_init__(self) -> None:
        _require_label("source_url", self.source_url)
        _require_label("final_url", self.final_url)
        if self.http_status != 200:
            raise AcquisitionError(f"unexpected HTTP status {self.http_status!r}")
        if not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise AcquisitionError("size_bytes must be a positive integer")


class _RecordingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Records each redirect target and enforces count, scheme, and
    origin scope before following. Violations raise HTTPError, which
    the fetcher converts to AcquisitionError."""

    def __init__(self, max_redirects: int, allowed_suffixes: tuple[str, ...]) -> None:
        self.chain: list[str] = []
        self.max_redirects = max_redirects
        self.allowed_suffixes = allowed_suffixes

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[no-untyped-def]
        target = urllib.parse.urljoin(req.get_full_url(), newurl)
        if len(self.chain) >= self.max_redirects:
            raise urllib.error.HTTPError(target, 599, "too many redirects", hdrs, fp)
        try:
            parsed = _validate_url(target, self.allowed_suffixes, context="redirect target")
            _validate_destination(parsed, context="redirect target")
        except AcquisitionError as exc:
            raise urllib.error.HTTPError(
                target, 599, str(exc), hdrs, fp
            ) from None
        self.chain.append(target)
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


def fetch_artifact(url: str, config: AcquireConfig | None = None) -> tuple[AcquiredArtifact, bytes]:
    """Fetch one URL, returning observed metadata plus the received bytes.

    Raises AcquisitionError for disallowed origins, timeouts, HTTP
    errors, redirect violations, or oversize/empty bodies.
    """
    if not isinstance(url, str) or not url.strip():
        raise AcquisitionError("url must be a non-empty string")
    resolved = config or AcquireConfig()
    if not isinstance(resolved, AcquireConfig):
        raise AcquisitionError("config must be an AcquireConfig")
    parsed = _validate_url(url, resolved.allowed_host_suffixes, context="acquisition URL")
    _validate_destination(parsed, context="acquisition URL")

    redirector = _RecordingRedirectHandler(resolved.max_redirects, resolved.allowed_host_suffixes)
    opener = urllib.request.build_opener(redirector)
    request = urllib.request.Request(url, headers={"User-Agent": resolved.user_agent})
    try:
        with opener.open(request, timeout=resolved.timeout_seconds) as response:
            final_url = response.geturl()
            final_parsed = _validate_url(
                final_url, resolved.allowed_host_suffixes, context="final URL"
            )
            _validate_destination(final_parsed, context="final URL")
            content_type = response.headers.get("Content-Type", "")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(min(65536, resolved.max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > resolved.max_bytes:
                    break
            data = b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code == 599:
            raise AcquisitionError(f"redirect rejected for {url!r}: {exc.msg}") from None
        raise AcquisitionError(f"HTTP {exc.code} for {url!r}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AcquisitionError(f"fetch failed for {url!r}: {type(exc).__name__}") from None
    if len(data) > resolved.max_bytes:
        raise AcquisitionError(f"response for {url!r} exceeds size limit of {resolved.max_bytes} bytes")
    if not data:
        raise AcquisitionError(f"response for {url!r} is empty")
    return (
        AcquiredArtifact(
            source_url=url,
            final_url=final_url,
            redirect_chain=tuple(redirector.chain),
            http_status=200,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            acquired_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
        data,
    )


def fetch_json_metadata(
    url: str, config: AcquireConfig | None = None, *, max_bytes: int = 262144
) -> tuple[dict[str, Any], AcquiredArtifact]:
    """Fetch one URL expected to serve a JSON metadata object, reusing
    the full fetch policy (origin scope, timeout, redirects) with a
    small dedicated size bound. Returns the parsed object plus its
    observed fetch metadata. Opt-in only: nothing calls this implicitly.
    Raises AcquisitionError for policy violations, HTTP errors,
    oversize bodies, undecodable bytes, or non-object JSON."""
    resolved = config or AcquireConfig()
    if not isinstance(resolved, AcquireConfig):
        raise AcquisitionError("config must be an AcquireConfig")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise AcquisitionError("max_bytes must be a positive integer")
    artifact, data = fetch_artifact(url, replace(resolved, max_bytes=max_bytes))
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AcquisitionError(f"response for {url!r} is not decodable JSON ({type(exc).__name__})") from None
    if not isinstance(payload, dict):
        raise AcquisitionError(f"response for {url!r} is not a JSON object")
    return payload, artifact


def _safe_filename(directory: str | os.PathLike[str], filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise AcquisitionError("filename must be a non-empty string")
    if (
        filename != os.path.basename(filename)
        or filename in (".", "..")
        or os.path.isabs(filename)
    ):
        raise AcquisitionError(f"unsafe filename {filename!r}")
    if not isinstance(directory, (str, os.PathLike)):
        raise AcquisitionError("directory must be a string or path-like object")
    return filename


def store_artifact(data: bytes, directory: str | os.PathLike[str], filename: str) -> str:
    """Write bytes atomically (temp file + rename). An existing file
    with identical bytes is returned as-is (idempotent); different
    bytes under the same name raise instead of overwriting."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise AcquisitionError("data must be non-empty bytes")
    name = _safe_filename(directory, filename)
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(os.fspath(directory), name)
    if os.path.exists(target):
        with open(target, "rb") as handle:
            if handle.read() == bytes(data):
                return target
        raise AcquisitionError(f"refusing to overwrite {target!r} with different bytes")
    fd, tmp_path = tempfile.mkstemp(prefix=name + ".", dir=os.fspath(directory))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(bytes(data))
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def verify_stored_artifact(path: str | os.PathLike[str], sha256: str) -> None:
    """Recompute the hash of stored bytes and compare. Mismatch or
    unreadable file raises; success returns None."""
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        raise AcquisitionError(f"cannot read stored file {os.fspath(path)!r}") from None
    if digest != sha256:
        raise AcquisitionError(f"hash mismatch for stored file {os.fspath(path)!r}")


@dataclass(frozen=True)
class AcquisitionReceipt:
    """Provenance receipt with observed values only. `parsed` records
    whether the artifact was ingested into a DocumentRecord (`document_id`
    set) or preserved as an unsupported-format original (`document_id`
    empty). `limitations` must state what remains unverified — never
    empty, since no acquisition verifies everything."""

    source_id: str
    artifact_id: str
    title: str
    publisher: str
    source_page_url: str
    artifact_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    acquired_at: str
    http_status: int
    content_type: str
    filename: str
    size_bytes: int
    sha256: str
    format: str
    parsed: bool
    document_id: str
    verification: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "artifact_id",
            "title",
            "source_page_url",
            "artifact_url",
            "final_url",
            "acquired_at",
            "filename",
            "format",
        ):
            _require_label(name, getattr(self, name))
        if not _SHA256_RE.fullmatch(self.sha256):
            raise AcquisitionError("receipt sha256 must be a SHA-256 hex digest")
        for name in ("publisher", "content_type", "document_id"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise AcquisitionError(f"{name} must be a string")
        if self.http_status != 200:
            raise AcquisitionError(f"unexpected HTTP status {self.http_status!r}")
        if not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise AcquisitionError("size_bytes must be a positive integer")
        if not isinstance(self.parsed, bool):
            raise AcquisitionError("parsed must be a boolean")
        if self.parsed and not self.document_id.strip():
            raise AcquisitionError("a parsed artifact must record its document_id")
        if self.verification != "unverified":
            raise AcquisitionError("an acquisition receipt never marks its source verified")
        if not self.limitations or any(not isinstance(v, str) or not v.strip() for v in self.limitations):
            raise AcquisitionError("limitations must be a non-empty tuple of non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping preserving unknown (empty) fields."""
        return {
            "source_id": self.source_id,
            "artifact_id": self.artifact_id,
            "title": self.title,
            "publisher": self.publisher,
            "source_page_url": self.source_page_url,
            "artifact_url": self.artifact_url,
            "final_url": self.final_url,
            "redirect_chain": list(self.redirect_chain),
            "acquired_at": self.acquired_at,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "format": self.format,
            "parsed": self.parsed,
            "document_id": self.document_id,
            "verification": self.verification,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcquisitionReceipt:
        """Rebuild a receipt, rejecting malformed input instead of coercing."""
        if not isinstance(data, dict):
            raise AcquisitionError("receipt must be a mapping")
        values: dict[str, Any] = {}
        for key in (
            "source_id",
            "artifact_id",
            "title",
            "publisher",
            "source_page_url",
            "artifact_url",
            "final_url",
            "acquired_at",
            "content_type",
            "filename",
            "sha256",
            "format",
            "document_id",
            "verification",
        ):
            value = data.get(key)
            if not isinstance(value, str):
                raise AcquisitionError(f"receipt.{key} must be a string")
            values[key] = value
        redirect = data.get("redirect_chain", ())
        limitations = data.get("limitations", ())
        for key, value in (("redirect_chain", redirect), ("limitations", limitations)):
            if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
                raise AcquisitionError(f"receipt.{key} must be a list of strings")
        values["redirect_chain"] = tuple(redirect)
        values["limitations"] = tuple(limitations)
        http_status = data.get("http_status")
        size_bytes = data.get("size_bytes")
        parsed = data.get("parsed")
        if not isinstance(http_status, int) or isinstance(http_status, bool):
            raise AcquisitionError("receipt.http_status must be an integer")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
            raise AcquisitionError("receipt.size_bytes must be an integer")
        if not isinstance(parsed, bool):
            raise AcquisitionError("receipt.parsed must be a boolean")
        values["http_status"] = http_status
        values["size_bytes"] = size_bytes
        values["parsed"] = parsed
        return cls(**values)  # type: ignore[arg-type]


def write_receipt_file(receipt: AcquisitionReceipt, directory: str | os.PathLike[str]) -> str:
    """Write the receipt JSON next to its artifact. Same no-silent-
    overwrite policy as artifacts: identical content returns the path,
    differing content raises."""
    if not isinstance(receipt, AcquisitionReceipt):
        raise AcquisitionError("receipt must be an AcquisitionReceipt")
    filename = receipt.artifact_id.replace(":", "-") + ".receipt.json"
    payload = json.dumps(receipt.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    target = os.path.join(os.fspath(directory), _safe_filename(directory, filename))
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(target):
        with open(target, "rb") as handle:
            if handle.read() == payload:
                return target
        raise AcquisitionError(f"refusing to overwrite {target!r} with a different receipt")
    fd, tmp_path = tempfile.mkstemp(prefix=filename + ".", dir=os.fspath(directory))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def integrity_check(source_id: str, check_id: str, sha256: str, size_bytes: int) -> VerificationCheck:
    """Build a *passed* integrity check from actually recomputed values.
    The caller must have measured `sha256`/`size_bytes` from stored
    bytes (e.g. via verify_stored_artifact); nothing here measures."""
    return VerificationCheck(
        check_id=_require_label("check_id", check_id),
        source_id=_require_label("source_id", source_id),
        check_type="integrity",
        outcome="passed",
        description="SHA-256 recomputed from stored artifact bytes and compared",
        observation=f"SHA-256 {sha256} over {size_bytes} stored bytes matches the receipt",
        limitations="content identity only; not proof of official authenticity",
    )


def origin_observation(
    source_id: str, check_id: str, final_url: str, http_status: int
) -> VerificationCheck:
    """Record an origin observation as *inconclusive* by construction:
    a successful HTTPS fetch observes the serving host but never
    establishes official status on its own."""
    return VerificationCheck(
        check_id=_require_label("check_id", check_id),
        source_id=_require_label("source_id", source_id),
        check_type="origin",
        outcome="inconclusive",
        description="recorded the serving endpoint of the fetched artifact",
        observation=f"retrieved over HTTPS from {final_url} (HTTP {http_status}); official status not independently established",
        limitations="retrieval observes provenance; authenticity requires independent checks",
    )
