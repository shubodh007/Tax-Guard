"""Deterministic corpus manifest and passage-index builder. Standard library only.

A manifest inventories which artifacts belong to a corpus snapshot and
reproduces the exact passage collection from the referenced document
records: same inputs always yield the same snapshot identity and the
same passages. It records existing identities (source/document ids,
content digests, verification snapshots) without upgrading their
meaning — an inventory, never a declaration of authenticity, currency,
completeness, or applicability. See the manifest section of
``docs/architecture.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from backend.app.chunking import CHUNKING_VERSION, ChunkConfig, Passage, chunk_document
from backend.app.sources import DocumentRecord
from backend.app.source_verification import (
    Assessment,
    VerificationCheck,
    VerificationPolicy,
    assessment_digest,
    authorize_status,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "CorpusManifest",
    "ExcludedArtifact",
    "ManifestEntry",
    "ManifestError",
    "build_manifest",
    "build_passages",
    "entry_for",
    "entry_for_verified",
    "load_manifest_file",
    "resolve_within_root",
    "verify_entry_binding",
]

MANIFEST_SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ManifestError(ValueError):
    """Malformed manifest, unsafe path, unresolvable reference, or
    identity mismatch. Distinct from valid inputs that simply yield an
    empty build (never: an empty entry set is itself malformed)."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_relative_path(name: str, value: str) -> str:
    """Lexical guard: normalized forward-slash relative path with no
    absolute prefix, drive letter, or parent-directory escape."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    normalized = value.replace("\\", "/").strip()
    if (
        normalized.startswith("/")
        or (len(normalized) > 1 and normalized[1] == ":")
        or normalized in (".", "..")
        or "/../" in f"/{normalized}/"
        or normalized.startswith("../")
        or normalized.endswith("/..")
    ):
        raise ManifestError(f"{name} must be a relative path inside the corpus, got {value!r}")
    return normalized


def resolve_within_root(base_dir: str, rel_path: str) -> str:
    """Resolve a manifest-relative path against the corpus root.

    Follows symlinks via realpath, then requires containment with
    commonpath (a string prefix alone cannot prove containment, and
    would wrongly accept siblings like '<root>-other'). Raises
    ManifestError on escape; returns the resolved absolute path.
    """
    full = os.path.realpath(os.path.join(base_dir, rel_path))
    base = os.path.realpath(base_dir)
    try:
        inside = os.path.commonpath([base, full]) == base
    except ValueError:
        inside = False
    if not inside:
        raise ManifestError(f"{rel_path!r} escapes the corpus root")
    return full


@dataclass(frozen=True)
class ExcludedArtifact:
    """An artifact deliberately left out of the snapshot, with its
    reason. Excluded paths are never resolved by the builder."""

    identifier: str
    reason: str

    def __post_init__(self) -> None:
        _require_label("identifier", self.identifier)
        _require_label("reason", self.reason)

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping."""
        return {"identifier": self.identifier, "reason": self.reason}


@dataclass(frozen=True)
class ManifestEntry:
    """One inventoried document. `passage_count` is the deterministic
    chunking output for these inputs (drift detector, not a guess);
    `source_verification` snapshots the record state without promoting
    it. `assessment_digest` binds a non-unverified label to the exact
    authorizing assessment (empty for unverified entries); a label
    without its digest, or a digest without its label, is malformed."""

    source_id: str
    document_id: str
    source_verification: str
    artifact_path: str
    document_path: str
    content_hash: str
    chunking_version: str
    max_chars: int
    overlap_chars: int
    passage_count: int
    assessment_digest: str = ""

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "document_id",
            "source_verification",
            "chunking_version",
        ):
            _require_label(name, getattr(self, name))
        _require_relative_path("artifact_path", self.artifact_path)
        _require_relative_path("document_path", self.document_path)
        if not isinstance(self.content_hash, str) or not self.content_hash.strip():
            raise ManifestError("content_hash must be a non-empty string")
        for name in ("max_chars", "overlap_chars", "passage_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ManifestError(f"{name} must be a non-negative integer")
        if self.max_chars <= 0:
            raise ManifestError("max_chars must be positive")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ManifestError("overlap_chars must satisfy 0 <= overlap_chars < max_chars")
        if not isinstance(self.assessment_digest, str):
            raise ManifestError("assessment_digest must be a string")
        if self.source_verification == "unverified":
            if self.assessment_digest:
                raise ManifestError("unverified entry must not carry an assessment reference")
        elif not _SHA256_RE.fullmatch(self.assessment_digest):
            raise ManifestError(
                "a non-unverified entry must carry its authorizing assessment digest"
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping."""
        return {
            "source_id": self.source_id,
            "document_id": self.document_id,
            "source_verification": self.source_verification,
            "artifact_path": self.artifact_path,
            "document_path": self.document_path,
            "content_hash": self.content_hash,
            "chunking_version": self.chunking_version,
            "max_chars": self.max_chars,
            "overlap_chars": self.overlap_chars,
            "passage_count": self.passage_count,
            "assessment_digest": self.assessment_digest,
        }


def _canonical(entries: tuple[ManifestEntry, ...], excluded: tuple[ExcludedArtifact, ...]) -> str:
    """Canonical JSON over sorted entries. Volatile metadata (absolute
    paths, timestamps, environment values) is never admitted: only the
    sorted identity fields below participate in the digest. The
    assessment digest participates only when present: absence is the
    canonical unverified form, so manifests written before the binding
    field existed reproduce their snapshots bit-for-bit."""
    def entry_canonical(e: ManifestEntry) -> dict[str, Any]:
        item: dict[str, Any] = {
            "source_id": e.source_id,
            "document_id": e.document_id,
            "source_verification": e.source_verification,
            "artifact_path": e.artifact_path,
            "document_path": e.document_path,
            "content_hash": e.content_hash,
            "chunking_version": e.chunking_version,
            "max_chars": e.max_chars,
            "overlap_chars": e.overlap_chars,
            "passage_count": e.passage_count,
        }
        if e.assessment_digest:
            item["assessment_digest"] = e.assessment_digest
        return item

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "entries": [
            entry_canonical(e) for e in sorted(entries, key=lambda entry: entry.document_id)
        ],
        "excluded": [
            {"identifier": x.identifier, "reason": x.reason}
            for x in sorted(excluded, key=lambda item: item.identifier)
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CorpusManifest:
    """A validated snapshot. `snapshot_id` is the SHA-256 of the
    canonical representation; any change to an included digest,
    artifact, chunking version, or configuration changes it, while
    input reordering never does."""

    snapshot_id: str
    entries: tuple[ManifestEntry, ...]
    excluded: tuple[ExcludedArtifact, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(e, ManifestEntry) for e in self.entries):
            raise ManifestError("entries must be a tuple of ManifestEntry")
        if any(not isinstance(x, ExcludedArtifact) for x in self.excluded):
            raise ManifestError("excluded must be a tuple of ExcludedArtifact")
        if not self.entries:
            raise ManifestError("a manifest must include at least one entry")
        document_ids = [e.document_id for e in self.entries]
        if len(set(document_ids)) != len(document_ids):
            raise ManifestError("duplicate document identifiers")
        for entry in self.entries:
            if not entry.document_id.startswith(f"{entry.source_id}:"):
                raise ManifestError(
                    f"conflicting document/source identity links for {entry.document_id!r}"
                )
        if self.snapshot_id != hashlib.sha256(_canonical(self.entries, self.excluded).encode()).hexdigest():
            raise ManifestError("snapshot_id does not match manifest contents")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping; round-trips through from_dict."""
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "entries": [e.to_dict() for e in self.entries],
            "excluded": [x.to_dict() for x in self.excluded],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorpusManifest:
        """Rebuild and revalidate; a stored snapshot_id that does not
        match the contents raises instead of being trusted."""
        if not isinstance(data, dict):
            raise ManifestError("manifest must be a mapping")
        if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported manifest schema version {data.get('schema_version')!r}"
            )
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ManifestError("manifest entries must be a non-empty list")
        entries: list[ManifestEntry] = []
        for i, item in enumerate(raw_entries):
            if not isinstance(item, dict):
                raise ManifestError(f"entries[{i}] must be a mapping")
            try:
                digest = item.get("assessment_digest", "")
                if not isinstance(digest, str):
                    raise ManifestError(f"entries[{i}] assessment_digest must be a string")
                entries.append(
                    ManifestEntry(
                        source_id=item["source_id"],
                        document_id=item["document_id"],
                        source_verification=item["source_verification"],
                        artifact_path=item["artifact_path"],
                        document_path=item["document_path"],
                        content_hash=item["content_hash"],
                        chunking_version=item["chunking_version"],
                        max_chars=item["max_chars"],
                        overlap_chars=item["overlap_chars"],
                        passage_count=item["passage_count"],
                        assessment_digest=digest,
                    )
                )
            except KeyError as exc:
                raise ManifestError(f"entries[{i}] is missing {exc}") from None
        raw_excluded = data.get("excluded", [])
        if not isinstance(raw_excluded, list):
            raise ManifestError("manifest excluded must be a list")
        excluded: list[ExcludedArtifact] = []
        for i, item in enumerate(raw_excluded):
            if not isinstance(item, dict):
                raise ManifestError(f"excluded[{i}] must be a mapping")
            try:
                excluded.append(ExcludedArtifact(item["identifier"], item["reason"]))
            except KeyError as exc:
                raise ManifestError(f"excluded[{i}] is missing {exc}") from None
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            raise ManifestError("snapshot_id must be a string")
        return cls(snapshot_id=snapshot_id, entries=tuple(entries), excluded=tuple(excluded))


def load_manifest_file(path: str) -> CorpusManifest:
    """Read and validate a manifest file. Missing/unreadable files,
    invalid JSON, and snapshot mismatches raise ManifestError."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        raise ManifestError(f"cannot read manifest {path!r}") from None
    except ValueError:
        raise ManifestError(f"manifest {path!r} is not valid JSON") from None
    return CorpusManifest.from_dict(data)


def _build_entry(
    document: DocumentRecord,
    *,
    artifact_path: str,
    document_path: str,
    config=None,
    assessment_digest: str = "",
) -> ManifestEntry:
    """Shared entry construction: chunk to establish the deterministic
    passage count and snapshot the record's verification state."""
    if not isinstance(document, DocumentRecord):
        raise ManifestError("document must be a DocumentRecord")
    resolved = config or ChunkConfig()
    if not isinstance(resolved, ChunkConfig):
        raise ManifestError("config must be a ChunkConfig or None")
    passages = chunk_document(document, resolved)
    return ManifestEntry(
        source_id=document.source_id,
        document_id=document.document_id,
        source_verification=document.source_verification,
        artifact_path=_require_relative_path("artifact_path", artifact_path),
        document_path=_require_relative_path("document_path", document_path),
        content_hash=document.content_hash,
        chunking_version=CHUNKING_VERSION,
        max_chars=resolved.max_chars,
        overlap_chars=resolved.overlap_chars,
        passage_count=len(passages),
        assessment_digest=assessment_digest,
    )


def entry_for(
    document: DocumentRecord,
    *,
    artifact_path: str,
    document_path: str,
    config=None,
) -> ManifestEntry:
    """Derive an entry from a loaded record: chunk it to establish the
    deterministic passage count and snapshot its verification state.
    The caller supplies corpus-relative paths; nothing is read here.
    Unverified records only: a non-unverified label without evidence is
    refused here and must go through entry_for_verified."""
    if isinstance(document, DocumentRecord) and document.source_verification != "unverified":
        raise ManifestError(
            "refused: a non-unverified record needs evidence-backed "
            "authorization via entry_for_verified"
        )
    return _build_entry(
        document,
        artifact_path=artifact_path,
        document_path=document_path,
        config=config,
    )


def entry_for_verified(
    document: DocumentRecord,
    *,
    assessment: Assessment,
    checks: tuple[VerificationCheck, ...] | list[VerificationCheck],
    policy: VerificationPolicy,
    artifact_path: str,
    document_path: str,
    config=None,
) -> ManifestEntry:
    """Derive an entry for a non-unverified record. The label is
    authorized against the supplied assessment, checks, and policy for
    this exact source and content digest; the entry stores the
    assessment's digest so later changes invalidate the reference."""
    if not isinstance(document, DocumentRecord):
        raise ManifestError("document must be a DocumentRecord")
    if document.source_verification == "unverified":
        raise ManifestError("unverified records use entry_for, not entry_for_verified")
    try:
        authorize_status(
            source_id=document.source_id,
            content_digest=document.content_hash,
            status=document.source_verification,
            assessment=assessment,
            checks=checks,
            policy=policy,
        )
    except ValueError as exc:
        raise ManifestError(f"verification not authorized: {exc}") from None
    return _build_entry(
        document,
        artifact_path=artifact_path,
        document_path=document_path,
        config=config,
        assessment_digest=assessment_digest(assessment),
    )


def verify_entry_binding(
    entry: ManifestEntry,
    assessment: Assessment,
    checks: tuple[VerificationCheck, ...] | list[VerificationCheck],
    *,
    policy: VerificationPolicy,
) -> None:
    """Revalidate a manifest entry against its authorizing assessment
    under an independently supplied policy. Unverified entries carry no
    reference and pass trivially; verified entries fail closed on a
    missing, stale, or mismatched assessment — or when the assessment
    does not satisfy the given policy. The policy must come from a
    trusted configuration (e.g. canonical_policy()), never from the
    assessment file itself: reason codes are self-reported strings, and
    rebuilding requirements from them would let a serialized assessment
    choose or weaken its own policy."""
    if not isinstance(entry, ManifestEntry):
        raise ManifestError("entry must be a ManifestEntry")
    if entry.source_verification == "unverified":
        if entry.assessment_digest:
            raise ManifestError("unverified entry carries an assessment reference")
        return
    if not isinstance(assessment, Assessment):
        raise ManifestError("a non-unverified entry requires its assessment")
    if not isinstance(policy, VerificationPolicy):
        raise ManifestError("revalidation requires a trusted policy")
    if assessment.source_id != entry.source_id:
        raise ManifestError("assessment belongs to another source")
    if assessment_digest(assessment) != entry.assessment_digest:
        raise ManifestError("stale or mismatched assessment reference")
    try:
        authorize_status(
            source_id=entry.source_id,
            content_digest=entry.content_hash,
            status=entry.source_verification,
            assessment=assessment,
            checks=checks,
            policy=policy,
        )
    except ValueError as exc:
        raise ManifestError(f"verification not authorized: {exc}") from None


def build_manifest(
    entries: tuple[ManifestEntry, ...] | list[ManifestEntry],
    *,
    excluded: tuple[ExcludedArtifact, ...] | list[ExcludedArtifact] = (),
) -> CorpusManifest:
    """Validate entries and compute the snapshot identity. Input order
    is irrelevant: entries sort by document_id in the digest."""
    if not isinstance(entries, (tuple, list)) or any(
        not isinstance(e, ManifestEntry) for e in entries
    ):
        raise ManifestError("entries must be a tuple or list of ManifestEntry")
    if not isinstance(excluded, (tuple, list)) or any(
        not isinstance(x, ExcludedArtifact) for x in excluded
    ):
        raise ManifestError("excluded must be a tuple or list of ExcludedArtifact")
    entry_tuple, excluded_tuple = tuple(entries), tuple(excluded)
    snapshot_id = hashlib.sha256(_canonical(entry_tuple, excluded_tuple).encode()).hexdigest()
    return CorpusManifest(snapshot_id=snapshot_id, entries=entry_tuple, excluded=excluded_tuple)


def build_passages(manifest: CorpusManifest, base_dir: str) -> tuple[Passage, ...]:
    """Rebuild the snapshot's passage collection. Each referenced
    document is loaded, checked field-for-field against its entry
    (identity, hash, verification snapshot), re-chunked with the
    entry's configuration, and count-checked. Anything missing,
    mismatched, stale, or drifted fails closed. Nothing is written;
    excluded artifacts are never resolved. Passages return in
    document_id order."""
    if not isinstance(manifest, CorpusManifest):
        raise ManifestError("manifest must be a CorpusManifest")
    if not isinstance(base_dir, str) or not base_dir.strip():
        raise ManifestError("base_dir must be a non-empty string")
    ordered = sorted(manifest.entries, key=lambda entry: entry.document_id)
    passages: list[Passage] = []
    for entry in ordered:
        if entry.chunking_version != CHUNKING_VERSION:
            raise ManifestError(
                f"entry {entry.document_id!r} requires chunking {entry.chunking_version!r}"
            )
        full_path = resolve_within_root(base_dir, entry.document_path)
        resolve_within_root(base_dir, entry.artifact_path)
        try:
            with open(full_path, encoding="utf-8") as handle:
                record = DocumentRecord.from_dict(json.loads(handle.read()))
        except OSError:
            raise ManifestError(f"cannot read {entry.document_path!r}") from None
        for name, actual, want in (
            ("document_id", record.document_id, entry.document_id),
            ("source_id", record.source_id, entry.source_id),
            ("content_hash", record.content_hash, entry.content_hash),
            ("source_verification", record.source_verification, entry.source_verification),
        ):
            if actual != want:
                raise ManifestError(
                    f"{entry.document_path!r} {name} mismatch: record carries {actual!r}"
                )
        rebuilt = chunk_document(
            record, ChunkConfig(max_chars=entry.max_chars, overlap_chars=entry.overlap_chars)
        )
        if len(rebuilt) != entry.passage_count:
            raise ManifestError(
                f"{entry.document_path!r} yields {len(rebuilt)} passages, "
                f"manifest expects {entry.passage_count}"
            )
        passages.extend(rebuilt)
    return tuple(passages)
