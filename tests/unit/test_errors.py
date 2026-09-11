"""The engine error hierarchy carries structured context, including across pickling."""

import pickle

import pytest

from mendwork.engine.errors import (
    AmbiguousTarget,
    BudgetExceeded,
    CheckpointFailed,
    MendworkError,
    NavigationError,
    PolicyViolation,
    ProviderError,
    TargetNotFound,
    WorkflowValidationError,
)

ERROR_TYPES: list[type[MendworkError]] = [
    MendworkError,
    TargetNotFound,
    AmbiguousTarget,
    CheckpointFailed,
    NavigationError,
    ProviderError,
    PolicyViolation,
    BudgetExceeded,
    WorkflowValidationError,
]


@pytest.mark.parametrize("error_type", ERROR_TYPES)
def test_error_carries_message_and_context(error_type: type[MendworkError]) -> None:
    error = error_type("could not resolve the target", step_id="step-3", attempt=2)

    assert issubclass(error_type, MendworkError)
    assert error.message == "could not resolve the target"
    assert str(error) == "could not resolve the target"
    assert dict(error.context) == {"step_id": "step-3", "attempt": 2}


@pytest.mark.parametrize("error_type", ERROR_TYPES)
def test_error_survives_a_pickle_round_trip(error_type: type[MendworkError]) -> None:
    error = error_type("could not resolve the target", step_id="step-3", attempt=2)

    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own in-memory payload

    assert type(restored) is error_type
    assert restored.message == error.message
    assert dict(restored.context) == dict(error.context)


def test_error_without_context_has_an_empty_context() -> None:
    error = MendworkError("something went wrong")

    assert dict(error.context) == {}


def test_context_cannot_be_mutated_through_the_property() -> None:
    error = MendworkError("something went wrong", step_id="step-3")

    with pytest.raises(TypeError):
        # A read-only view: callers report context, they never edit it.
        error.context["step_id"] = "step-4"  # type: ignore[index]

    assert dict(error.context) == {"step_id": "step-3"}


def test_repr_shows_message_and_context() -> None:
    error = TargetNotFound("no match", step_id="step-3")

    assert repr(error) == "TargetNotFound(message='no match', context={'step_id': 'step-3'})"
