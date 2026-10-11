"""Explicit in-memory inspection registry. Standard library only.

Holds already-built `EvidenceTrace` and `AnswerResult` objects keyed by
exact `trace_id` for the read-only inspection API. Process-local and
volatile by design: contents disappear on restart and are never shared
between processes. Registration is an internal Python interface —
there is no HTTP write path. See `docs/inspection-api-contract.md`.
"""

from __future__ import annotations

from backend.app.assembly import AnswerResult
from backend.app.evidence import EvidenceTrace

__all__ = ["Registry", "RegistryError"]


class RegistryError(ValueError):
    """Inconsistent or duplicate registration. Distinct from a read of a
    simply absent id (which yields None so the API can return 404)."""


class Registry:
    """Smallest adequate store: exact-id lookup plus consistency-checked
    registration. One instance per application; never shared implicitly."""

    def __init__(self) -> None:
        self._traces: dict[str, EvidenceTrace] = {}
        self._answers: dict[str, AnswerResult] = {}

    def register_trace(self, trace: EvidenceTrace) -> None:
        """Store a trace. Re-registering an id raises instead of silently
        replacing it; use a fresh registry for a new registration set."""
        if not isinstance(trace, EvidenceTrace):
            raise RegistryError("trace must be an EvidenceTrace")
        if trace.trace_id in self._traces:
            raise RegistryError(f"trace {trace.trace_id!r} is already registered")
        self._traces[trace.trace_id] = trace

    def register_answer(self, trace: EvidenceTrace, answer: AnswerResult) -> None:
        """Store an answer alongside its trace. The trace must already be
        registered and the answer's `trace_id` must match it exactly, so
        an answer can never be attached to the wrong trace."""
        if not isinstance(trace, EvidenceTrace):
            raise RegistryError("trace must be an EvidenceTrace")
        if not isinstance(answer, AnswerResult):
            raise RegistryError("answer must be an AnswerResult")
        if answer.trace_id != trace.trace_id:
            raise RegistryError(
                f"answer trace {answer.trace_id!r} does not match trace {trace.trace_id!r}"
            )
        if trace.trace_id not in self._traces:
            raise RegistryError(f"trace {trace.trace_id!r} must be registered first")
        if answer.trace_id in self._answers:
            raise RegistryError(f"answer for {answer.trace_id!r} is already registered")
        self._answers[answer.trace_id] = answer

    def get_trace(self, trace_id: str) -> EvidenceTrace | None:
        """Exact-id lookup. None means absent — the API maps it to 404 and
        never invents a placeholder."""
        return self._traces.get(trace_id)

    def get_answer(self, trace_id: str) -> AnswerResult | None:
        """Exact-id lookup. None means absent — the API maps it to 404."""
        return self._answers.get(trace_id)
