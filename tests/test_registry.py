"""Registry unit tests. No external services required."""

import pytest

from backend.app.registry import Registry, RegistryError

from _synthetic import synthetic_answer, synthetic_trace


def test_register_and_lookup() -> None:
    registry = Registry()
    trace = synthetic_trace()
    answer = synthetic_answer()
    registry.register_trace(trace)
    registry.register_answer(trace, answer)

    assert registry.get_trace("syn-trace-1") is trace
    assert registry.get_answer("syn-trace-1") is answer


def test_empty_registry_returns_nothing() -> None:
    registry = Registry()
    assert registry.get_trace("syn-trace-1") is None
    assert registry.get_answer("syn-trace-1") is None
    assert registry.get_trace("") is None


def test_answer_trace_mismatch_rejected() -> None:
    registry = Registry()
    trace = synthetic_trace("syn-trace-1")
    registry.register_trace(trace)
    with pytest.raises(RegistryError, match="does not match"):
        registry.register_answer(trace, synthetic_answer("syn-trace-2"))
    assert registry.get_answer("syn-trace-2") is None


def test_answer_before_trace_rejected() -> None:
    registry = Registry()
    with pytest.raises(RegistryError, match="registered first"):
        registry.register_answer(synthetic_trace(), synthetic_answer())


def test_duplicate_registration_rejected() -> None:
    registry = Registry()
    trace = synthetic_trace()
    registry.register_trace(trace)
    with pytest.raises(RegistryError, match="already registered"):
        registry.register_trace(synthetic_trace())
    registry.register_answer(trace, synthetic_answer())
    with pytest.raises(RegistryError, match="already registered"):
        registry.register_answer(trace, synthetic_answer())
    # Originals untouched by the rejected duplicates.
    assert registry.get_trace("syn-trace-1") is trace


def test_wrong_types_rejected() -> None:
    registry = Registry()
    with pytest.raises(RegistryError):
        registry.register_trace(synthetic_answer())  # type: ignore[arg-type]
    with pytest.raises(RegistryError):
        registry.register_answer(synthetic_trace(), synthetic_trace())  # type: ignore[arg-type]


def test_registries_are_isolated() -> None:
    first, second = Registry(), Registry()
    first.register_trace(synthetic_trace())
    assert second.get_trace("syn-trace-1") is None
