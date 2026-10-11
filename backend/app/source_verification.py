"""Source authenticity and verification-evidence contract. Standard library only.

Records the evidence behind a source's verification state as explicit,
auditable check records, and derives a conservative assessment from
them. Registration, origin claims, authenticity checks, integrity
checks, version checks, and applicability are separate assertions: one
label never implies the others. No network access, no downloads, no
database, no LLM, no mutation of existing source records. See
``docs/source-verification-contract.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from backend.app.sources import DocumentRecord, SourceRecord

__all__ = [
    "CANONICAL_POLICY_ID",
    "CANONICAL_POLICY_VERSION",
    "CANONICAL_REQUIRED_CHECKS",
    "CHECK_TYPES",
    "OUTCOMES",
    "Assessment",
    "CategoryVerdict",
    "ExpectedArtifact",
    "ObservedArtifact",
    "OfficialRecord",
    "SourceVerificationError",
    "VerificationCheck",
    "VerificationPolicy",
    "assess",
    "assessment_digest",
    "authorize_status",
    "canonical_policy",
    "reconcile",
]

# Supported check categories, in canonical evaluation order.
# `record_identity` was appended in Phase 14; the original five keep
# their order so existing reason codes and verdicts are unaffected.
CHECK_TYPES = ("origin", "integrity", "version", "completeness", "applicability", "record_identity")

# Supported check outcomes.
OUTCOMES = ("passed", "failed", "inconclusive", "not_checked")

# Assessment statuses.
ASSESSMENT_STATUSES = ("verified", "failed", "conflicting", "incomplete")

# Observation length bound: observations record evidence pointers, never
# full document text or secrets.
_OBSERVATION_MAX_CHARS = 4000
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class SourceVerificationError(ValueError):
    """Malformed verification record, policy, or assessment input.
    Distinct from a valid assessment that concludes conservatively."""


def _require_label(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceVerificationError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _optional_text(name: str, value: str, max_chars: int = 0) -> str:
    if not isinstance(value, str):
        raise SourceVerificationError(f"{name} must be a string")
    if max_chars and len(value) > max_chars:
        raise SourceVerificationError(f"{name} exceeds {max_chars} characters")
    return value


@dataclass(frozen=True)
class VerificationCheck:
    """One recorded verification observation. `description` states what
    was checked and its scope; `observation` records the evidence or
    finding (explicit text such as "not yet performed" when nothing was
    done — never invented). `checked_at` is caller-supplied or empty
    (no default timestamps); `content_hash`, when present, is the
    SHA-256 of the checked bytes — content identity only, never proof
    of official authenticity."""

    check_id: str
    source_id: str
    check_type: str
    outcome: str
    description: str
    observation: str
    verifier: str = ""
    checked_at: str = ""
    content_hash: str = ""
    limitations: str = ""
    observation_source: str = ""
    """Where the observation came from: "local" (measured on this
    machine), "network" (retrieved from a remote endpoint), or ""
    (unsupplied, e.g. records predating this field)."""

    def __post_init__(self) -> None:
        _require_label("check_id", self.check_id)
        _require_label("source_id", self.source_id)
        if self.check_type not in CHECK_TYPES:
            raise SourceVerificationError(f"unknown check type {self.check_type!r}")
        if self.outcome not in OUTCOMES:
            raise SourceVerificationError(f"unknown outcome {self.outcome!r}")
        _require_label("description", self.description)
        _require_label("observation", self.observation)
        _optional_text("verifier", self.verifier)
        _optional_text("checked_at", self.checked_at)
        _optional_text("limitations", self.limitations, _OBSERVATION_MAX_CHARS)
        _optional_text("observation", self.observation, _OBSERVATION_MAX_CHARS)
        if self.content_hash and not _SHA256_RE.fullmatch(self.content_hash):
            raise SourceVerificationError("content_hash must be a SHA-256 hex digest when present")
        if self.observation_source not in ("", "local", "network"):
            raise SourceVerificationError(
                f"observation_source must be 'local', 'network' or empty, got {self.observation_source!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping preserving unsupplied (empty) fields."""
        return {
            "check_id": self.check_id,
            "source_id": self.source_id,
            "check_type": self.check_type,
            "outcome": self.outcome,
            "description": self.description,
            "observation": self.observation,
            "verifier": self.verifier,
            "checked_at": self.checked_at,
            "content_hash": self.content_hash,
            "limitations": self.limitations,
            "observation_source": self.observation_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationCheck:
        """Rebuild a record, rejecting malformed input instead of coercing."""
        if not isinstance(data, dict):
            raise SourceVerificationError("check must be a mapping")
        required: dict[str, Any] = {}
        for key in ("check_id", "source_id", "check_type", "outcome", "description", "observation"):
            value = data.get(key)
            if not isinstance(value, str):
                raise SourceVerificationError(f"check.{key} must be a string")
            required[key] = value
        optional: dict[str, Any] = {}
        for key in ("verifier", "checked_at", "content_hash", "limitations", "observation_source"):
            value = data.get(key, "")
            if not isinstance(value, str):
                raise SourceVerificationError(f"check.{key} must be a string")
            optional[key] = value
        return cls(**required, **optional)  # type: ignore[arg-type]


@dataclass(frozen=True)
class VerificationPolicy:
    """Which check categories must be satisfied. Non-empty by
    construction: a policy that demands nothing can verify nothing."""
    policy_id: str
    version: str
    required_checks: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_label("policy_id", self.policy_id)
        _require_label("version", self.version)
        if any(not isinstance(c, str) for c in self.required_checks):
            raise SourceVerificationError("required_checks must be a tuple of strings")
        for check in self.required_checks:
            if check not in CHECK_TYPES:
                raise SourceVerificationError(f"unknown check type {check!r}")
        if not self.required_checks:
            raise SourceVerificationError("required_checks must not be empty")
        if len(set(self.required_checks)) != len(self.required_checks):
            raise SourceVerificationError("duplicate required checks")


# Canonical application policy: the independently trusted anchor for
# revalidating manifest entries at load time. Pinned in code — never
# read from an assessment file — so a serialized assessment cannot
# choose or weaken its own requirements. The required set matches the
# newest recorded assessments for the held sources; it is an
# engineering default and still needs product/legal approval. Effect:
# origin evidence is inconclusive by construction, so nothing reaches
# "verified" until genuine origin evidence exists — sources stay
# honestly unverified rather than weakly verified.
CANONICAL_POLICY_ID = "taxguard-source-policy"
CANONICAL_POLICY_VERSION = "v1"
CANONICAL_REQUIRED_CHECKS = ("origin", "integrity", "record_identity")


def canonical_policy() -> VerificationPolicy:
    """Return the trusted application verification policy."""
    return VerificationPolicy(
        CANONICAL_POLICY_ID, CANONICAL_POLICY_VERSION, CANONICAL_REQUIRED_CHECKS
    )


@dataclass(frozen=True)
class CategoryVerdict:
    """Per-category conclusion. `not_required` applies only to a
    non-required category with no records; every other verdict derives
    from actually recorded outcomes."""

    category: str
    verdict: str

    def __post_init__(self) -> None:
        if self.category not in CHECK_TYPES:
            raise SourceVerificationError(f"unknown check type {self.category!r}")
        if self.verdict not in (
            "satisfied",
            "conflicting",
            "failed",
            "uncertain",
            "missing",
            "not_required",
        ):
            raise SourceVerificationError(f"unknown verdict {self.verdict!r}")


@dataclass(frozen=True)
class Assessment:
    """Conservative assessment for one source. `reason_codes` name every
    required verdict as `<verdict>:<category>` in canonical order, so the
    decision's basis is preserved. A `verified` status covers exactly the
    required categories — never applicability, completeness, or anything
    else that was not required and satisfied."""

    source_id: str
    status: str
    policy_id: str
    policy_version: str
    reason_codes: tuple[str, ...]
    check_ids: tuple[str, ...]
    verdicts: tuple[CategoryVerdict, ...]

    def __post_init__(self) -> None:
        _require_label("source_id", self.source_id)
        if self.status not in ASSESSMENT_STATUSES:
            raise SourceVerificationError(f"unknown status {self.status!r}")
        _require_label("policy_id", self.policy_id)
        _require_label("policy_version", self.policy_version)
        if any(not isinstance(c, str) or not c.strip() for c in self.reason_codes):
            raise SourceVerificationError("reason_codes must be a tuple of non-empty strings")
        if any(not isinstance(c, str) or not c.strip() for c in self.check_ids):
            raise SourceVerificationError("check_ids must be a tuple of non-empty strings")
        if any(not isinstance(v, CategoryVerdict) for v in self.verdicts):
            raise SourceVerificationError("verdicts must be a tuple of CategoryVerdict")

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible mapping with deterministic ordering."""
        return {
            "source_id": self.source_id,
            "status": self.status,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "reason_codes": list(self.reason_codes),
            "check_ids": list(self.check_ids),
            "verdicts": [
                {"category": v.category, "verdict": v.verdict} for v in self.verdicts
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Assessment:
        """Rebuild an assessment, rejecting malformed input instead of
        coercing. Structural validity only: whether the assessment backs
        any label is decided by authorize_status, never here."""
        if not isinstance(data, dict):
            raise SourceVerificationError("assessment must be a mapping")
        source_id = data.get("source_id")
        status = data.get("status")
        policy_id = data.get("policy_id")
        policy_version = data.get("policy_version")
        if not isinstance(source_id, str) or not source_id.strip():
            raise SourceVerificationError("assessment.source_id must be a non-empty string")
        if status not in ASSESSMENT_STATUSES:
            raise SourceVerificationError(f"unknown assessment status {status!r}")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise SourceVerificationError("assessment.policy_id must be a non-empty string")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise SourceVerificationError("assessment.policy_version must be a non-empty string")
        reason_codes = data.get("reason_codes")
        check_ids = data.get("check_ids")
        raw_verdicts = data.get("verdicts")
        if not isinstance(reason_codes, list) or any(
            not isinstance(c, str) or not c.strip() for c in reason_codes
        ):
            raise SourceVerificationError("assessment.reason_codes must be a list of non-empty strings")
        if not isinstance(check_ids, list) or any(
            not isinstance(c, str) or not c.strip() for c in check_ids
        ):
            raise SourceVerificationError("assessment.check_ids must be a list of non-empty strings")
        if not isinstance(raw_verdicts, list):
            raise SourceVerificationError("assessment.verdicts must be a list")
        verdicts: list[CategoryVerdict] = []
        for item in raw_verdicts:
            if not isinstance(item, dict):
                raise SourceVerificationError("assessment verdicts must be mappings")
            category, verdict = item.get("category"), item.get("verdict")
            if not isinstance(category, str) or not isinstance(verdict, str):
                raise SourceVerificationError("assessment verdicts must carry string category and verdict")
            verdicts.append(CategoryVerdict(category, verdict))
        return cls(
            source_id=source_id,
            status=status,
            policy_id=policy_id,
            policy_version=policy_version,
            reason_codes=tuple(reason_codes),
            check_ids=tuple(check_ids),
            verdicts=tuple(verdicts),
        )


def _category_verdict(records: list[VerificationCheck], required: bool) -> str:
    evaluated = [r for r in records if r.outcome != "not_checked"]
    if not evaluated:
        return "missing" if required else "not_required"
    outcomes = {r.outcome for r in evaluated}
    has_passed = "passed" in outcomes
    has_failed = "failed" in outcomes
    if has_passed and has_failed:
        return "conflicting"
    if has_failed:
        return "failed"
    if has_passed and outcomes == {"passed"}:
        return "satisfied"
    return "uncertain"


def assess(
    source_id: str,
    checks: tuple[VerificationCheck, ...] | list[VerificationCheck],
    policy: VerificationPolicy,
) -> Assessment:
    """Derive a conservative assessment from recorded checks. Reads only
    the supplied records — never the source registry, URLs, files, or
    network. A gov-looking origin or a successful hash cannot promote
    anything: only `passed` records in required categories satisfy them.
    Fail-closed precedence: conflicting > failed > incomplete; `verified`
    additionally requires zero failed records anywhere considered."""
    if not isinstance(source_id, str) or not source_id.strip():
        raise SourceVerificationError("source_id must be a non-empty string")
    if not isinstance(checks, (tuple, list)):
        raise SourceVerificationError("checks must be a tuple or list of VerificationCheck")
    if any(not isinstance(c, VerificationCheck) for c in checks):
        raise SourceVerificationError("checks must contain only VerificationCheck items")
    if not isinstance(policy, VerificationPolicy):
        raise SourceVerificationError("policy must be a VerificationPolicy")
    for check in checks:
        if check.source_id != source_id:
            raise SourceVerificationError(
                f"check {check.check_id!r} belongs to source {check.source_id!r}"
            )
    check_ids = [c.check_id for c in checks]
    if len(set(check_ids)) != len(check_ids):
        raise SourceVerificationError("duplicate check identifiers")

    required = set(policy.required_checks)
    verdicts: list[CategoryVerdict] = []
    for category in CHECK_TYPES:
        records = [c for c in checks if c.check_type == category]
        verdicts.append(CategoryVerdict(category, _category_verdict(records, category in required)))

    by_category = {v.category: v.verdict for v in verdicts}
    reason_codes = tuple(f"{by_category[c]}:{c}" for c in policy.required_checks)
    if any(v == "conflicting" for v in by_category.values()):
        status = "conflicting"
    elif any(v == "failed" for v in by_category.values()):
        status = "failed"
    elif all(by_category[c] == "satisfied" for c in policy.required_checks):
        status = "verified"
    else:
        status = "incomplete"
    return Assessment(
        source_id=source_id,
        status=status,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        reason_codes=reason_codes,
        check_ids=tuple(sorted(check_ids)),
        verdicts=tuple(verdicts),
    )


def assessment_digest(assessment: Assessment) -> str:
    """Canonical SHA-256 over an assessment's deterministic mapping.
    Carries no timestamps, paths, or environment values (Assessment
    holds none; to_dict() ordering is fixed), so equal evidence always
    yields the equal digest and any change invalidates it."""
    if not isinstance(assessment, Assessment):
        raise SourceVerificationError("assessment must be an Assessment")
    canonical = json.dumps(assessment.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def authorize_status(
    *,
    source_id: str,
    content_digest: str,
    status: str,
    assessment: Assessment | None,
    checks: tuple[VerificationCheck, ...] | list[VerificationCheck] | None,
    policy: VerificationPolicy | None,
) -> None:
    """Gate promotion to a non-unverified label. Returns None when the
    label is authorized, raises SourceVerificationError otherwise.

    - "unverified" needs no evidence and is always authorized.
    - Any other label requires an assessment whose status permits it
      ("verified" assessment for "verified"; "failed"/"conflicting" for
      "disputed"), issued for this source under this policy, with every
      required category satisfied and a passed integrity record binding
      this exact content digest. Incomplete, failed, conflicting, and
      not_checked evidence can never authorize "verified"; missing or
      malformed inputs fail closed. Origin satisfaction is never accepted
      from supplied records: no independently trusted origin evidence
      exists in this system (its own machinery reports origin
      inconclusive by construction), so a policy requiring origin keeps
      the source unverified. A hash or title match alone authorizes
      nothing: only bound, independently satisfiable assessment does.
    """
    if not isinstance(status, str) or not status.strip():
        raise SourceVerificationError("status must be a non-empty string")
    if status == "unverified":
        return
    if assessment is None or checks is None or policy is None:
        raise SourceVerificationError(f"{status!r} requires an assessment, checks, and policy")
    if not isinstance(assessment, Assessment):
        raise SourceVerificationError("assessment must be an Assessment")
    if not isinstance(policy, VerificationPolicy):
        raise SourceVerificationError("policy must be a VerificationPolicy")
    if not isinstance(checks, (tuple, list)) or any(
        not isinstance(c, VerificationCheck) for c in checks
    ):
        raise SourceVerificationError("checks must be a tuple or list of VerificationCheck")
    if not isinstance(source_id, str) or not source_id.strip():
        raise SourceVerificationError("source_id must be a non-empty string")
    if not _SHA256_RE.fullmatch(content_digest or ""):
        raise SourceVerificationError("content_digest must be a SHA-256 hex digest")
    if assessment.source_id != source_id:
        raise SourceVerificationError(
            f"assessment belongs to source {assessment.source_id!r}, not {source_id!r}"
        )
    if (assessment.policy_id, assessment.policy_version) != (policy.policy_id, policy.version):
        raise SourceVerificationError("assessment policy does not match the required policy")
    if status == "verified":
        if assessment.status != "verified":
            raise SourceVerificationError(
                f"only a verified assessment authorizes 'verified', got {assessment.status!r}"
            )
        if "origin" in policy.required_checks:
            raise SourceVerificationError(
                "origin satisfaction cannot be established from supplied records: "
                "no independently trusted origin evidence exists, so the source "
                "stays unverified"
            )
    elif status == "disputed":
        if assessment.status not in ("failed", "conflicting"):
            raise SourceVerificationError(
                f"only a failed or conflicting assessment authorizes 'disputed', "
                f"got {assessment.status!r}"
            )
    else:
        raise SourceVerificationError(f"unknown verification status {status!r}")
    verdicts = {v.category: v.verdict for v in assessment.verdicts}
    expected_codes = tuple(f"{verdicts.get(c, 'missing')}:{c}" for c in policy.required_checks)
    if expected_codes != assessment.reason_codes:
        raise SourceVerificationError("assessment reason codes do not match the required policy")
    if status == "verified" and any(verdicts.get(c) != "satisfied" for c in policy.required_checks):
        raise SourceVerificationError("required assessment categories are not all satisfied")
    supplied_ids = [c.check_id for c in checks]
    if len(set(supplied_ids)) != len(supplied_ids):
        raise SourceVerificationError("duplicate check identifiers")
    for check in checks:
        if check.source_id != source_id:
            raise SourceVerificationError(
                f"check {check.check_id!r} belongs to source {check.source_id!r}"
            )
    referenced = set(assessment.check_ids)
    bound = any(
        c.check_id in referenced
        and c.check_type == "integrity"
        and c.outcome == "passed"
        and c.content_hash == content_digest
        for c in checks
    )
    if not bound:
        raise SourceVerificationError(
            "no passed integrity record binds this assessment to the content digest"
        )


def _artifact_part(name: str, sha256: str, size_bytes: int) -> None:
    if not isinstance(sha256, str) or (sha256 and not _SHA256_RE.fullmatch(sha256)):
        raise SourceVerificationError(f"{name}.sha256 must be empty or a SHA-256 hex digest")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise SourceVerificationError(f"{name}.size_bytes must be a non-negative integer")


@dataclass(frozen=True)
class ObservedArtifact:
    """Locally measured file facts. Empty sha256 / zero size means the
    measurement is missing — never a passing value."""

    sha256: str = ""
    size_bytes: int = 0

    def __post_init__(self) -> None:
        _artifact_part("observed", self.sha256, self.size_bytes)


@dataclass(frozen=True)
class ExpectedArtifact:
    """Receipt-recorded expectations to compare against. Empty means
    the receipt carries no expectation for that field."""

    sha256: str = ""
    size_bytes: int = 0

    def __post_init__(self) -> None:
        _artifact_part("expected", self.sha256, self.size_bytes)


@dataclass(frozen=True)
class OfficialRecord:
    """Official-record metadata as observed (possibly fetched live).
    Every field may be empty meaning unknown. `size_bytes` of 0 means
    the record states no size; it is never treated as an expectation
    of zero."""

    record_url: str = ""
    title: str = ""
    act_label: str = ""
    size_bytes: int = 0

    def __post_init__(self) -> None:
        for name in ("record_url", "title", "act_label"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise SourceVerificationError(f"official.{name} must be a string")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise SourceVerificationError("official.size_bytes must be a non-negative integer")

    def is_empty(self) -> bool:
        """True when no official metadata was supplied at all."""
        return not (self.record_url or self.title or self.act_label or self.size_bytes)


def reconcile(
    source: SourceRecord,
    *,
    observed: ObservedArtifact,
    expected: ExpectedArtifact,
    official: OfficialRecord | None = None,
    document: DocumentRecord | None = None,
    evidence_origin: str = "",
    checked_at: str = "",
    verifier: str = "",
) -> tuple[VerificationCheck, ...]:
    """Compare locally measured facts, receipt expectations, an optional
    parsed record, and optional official metadata into one check per
    category that this evidence can address.

    - integrity ("local" always): passed when complete observed and
      expected hashes and sizes all match; failed on any mismatch; a
      parsed `DocumentRecord` whose hash differs from expectations also
      fails the chain, while a matching one is noted. Incomplete sides
      yield inconclusive, never a pass.
    - record_identity: passed when a supplied official title equals the
      source title (record URL and act label cited); failed when both
      titles are present and differ; not_checked when no official
      metadata was supplied; inconclusive when supplied metadata has no
      title to compare. An official size that differs from the observed
      size is reported verbatim in the observation without changing a
      title-driven verdict.
    - version, completeness, applicability: not_checked — this evidence
      establishes none of them.

    `evidence_origin` ("local"/"network"/"") labels the official-derived
    checks; integrity is always "local" because its inputs must be
    locally measured, never reported. Check ids are deterministic:
    ``{source_id}:{category}:{expected-sha-prefix or nohash}``.
    """
    if not isinstance(source, SourceRecord):
        raise SourceVerificationError("source must be a SourceRecord")
    if not isinstance(observed, ObservedArtifact):
        raise SourceVerificationError("observed must be an ObservedArtifact")
    if not isinstance(expected, ExpectedArtifact):
        raise SourceVerificationError("expected must be an ExpectedArtifact")
    if official is not None and not isinstance(official, OfficialRecord):
        raise SourceVerificationError("official must be an OfficialRecord or None")
    if document is not None:
        if not isinstance(document, DocumentRecord):
            raise SourceVerificationError("document must be a DocumentRecord or None")
        if document.source_id != source.source_id:
            raise SourceVerificationError(
                f"document belongs to source {document.source_id!r}, not {source.source_id!r}"
            )
    if evidence_origin not in ("", "local", "network"):
        raise SourceVerificationError("evidence_origin must be 'local', 'network' or empty")
    for name, value in (("checked_at", checked_at), ("verifier", verifier)):
        if not isinstance(value, str):
            raise SourceVerificationError(f"{name} must be a string")

    tag = expected.sha256[:12] if expected.sha256 else "nohash"

    def make(check_type: str, outcome: str, description: str, observation: str,
             limitations: str, origin: str, content_hash: str = "") -> VerificationCheck:
        return VerificationCheck(
            check_id=f"{source.source_id}:{check_type}:{tag}",
            source_id=source.source_id,
            check_type=check_type,
            outcome=outcome,
            description=description,
            observation=observation,
            verifier=verifier,
            checked_at=checked_at,
            content_hash=content_hash,
            limitations=limitations,
            observation_source=origin,
        )

    checks: list[VerificationCheck] = []

    observed_ok = bool(observed.sha256 and observed.size_bytes)
    expected_ok = bool(expected.sha256 and expected.size_bytes)
    if observed_ok and expected_ok:
        if observed.sha256 == expected.sha256 and observed.size_bytes == expected.size_bytes:
            note = (
                f"observed SHA-256 {observed.sha256} over {observed.size_bytes} bytes "
                "matches the receipt"
            )
            if document is not None:
                if document.content_hash != expected.sha256:
                    checks.append(make(
                        "integrity", "failed",
                        "compared locally measured bytes, receipt expectations, and the parsed record",
                        f"{note}; parsed record {document.document_id} carries a different hash",
                        "content identity only; not proof of official authenticity",
                        "local", observed.sha256,
                    ))
                else:
                    note += f"; parsed record {document.document_id} carries the identical hash"
                    checks.append(make(
                        "integrity", "passed",
                        "compared locally measured bytes, receipt expectations, and the parsed record",
                        note,
                        "content identity only; not proof of official authenticity",
                        "local", observed.sha256,
                    ))
            else:
                checks.append(make(
                    "integrity", "passed",
                    "compared locally measured bytes against receipt expectations",
                    note,
                    "content identity only; not proof of official authenticity",
                    "local", observed.sha256,
                ))
        else:
            checks.append(make(
                "integrity", "failed",
                "compared locally measured bytes against receipt expectations",
                "observed and receipt hash or size differ",
                "content identity only; not proof of official authenticity",
                "local",
            ))
    else:
        checks.append(make(
            "integrity", "inconclusive",
            "compared locally measured bytes against receipt expectations",
            "observed or receipt hash or size missing; cannot evaluate",
            "content identity only; not proof of official authenticity",
            "local",
        ))

    record_url = official.record_url if official else ""
    if official is None or official.is_empty():
        checks.append(make(
            "record_identity", "not_checked",
            "compared official record metadata against the source record",
            "no official metadata supplied",
            "identity uncorroborated",
            evidence_origin,
        ))
    elif not official.title:
        checks.append(make(
            "record_identity", "inconclusive",
            "compared official record metadata against the source record",
            f"official metadata at {record_url or 'unknown location'} carries no title to compare",
            "identity uncorroborated",
            evidence_origin,
        ))
    else:
        size_note = ""
        if official.size_bytes and observed.size_bytes:
            if official.size_bytes == observed.size_bytes:
                size_note = "; official size corroborates the observed size"
            else:
                size_note = (
                    f"; size discrepancy: official record lists {official.size_bytes}, "
                    f"observed {observed.size_bytes}"
                )
        if official.title == source.title:
            checks.append(make(
                "record_identity", "passed",
                "compared official record metadata against the source record",
                f"official title matches source title at {record_url}"
                + (f"; parent Act recorded as {official.act_label}" if official.act_label else "")
                + size_note,
                "title match corroborates identity, not authenticity or currency",
                evidence_origin,
            ))
        else:
            checks.append(make(
                "record_identity", "failed",
                "compared official record metadata against the source record",
                f"official title {official.title!r} differs from source title {source.title!r}"
                + size_note,
                "identity uncorroborated",
                evidence_origin,
            ))

    checks.append(make(
        "version", "not_checked",
        "checked for an authoritative version, amendment, or effective-period statement",
        "no authoritative version statement supplied",
        "version identity unknown",
        evidence_origin,
    ))
    checks.append(make(
        "completeness", "not_checked",
        "checked whether the acquired material is the complete resource or a fragment",
        "fragment scope not established by supplied evidence",
        "completeness unknown",
        evidence_origin,
    ))
    checks.append(make(
        "applicability", "not_checked",
        "checked for evidence establishing the relevant period and legal applicability",
        "no legal analysis performed",
        "applicability unestablished",
        evidence_origin,
    ))
    return tuple(checks)
