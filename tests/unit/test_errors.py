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
    UnsupportedSchemaVersion,
    ValidationIssue,
    VersionConflict,
    WorkflowValidationError,
    format_location,
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
    UnsupportedSchemaVersion,
    VersionConflict,
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


def test_validation_errors_carry_their_issues_through_pickling() -> None:
    issue = ValidationIssue(
        ("steps", 3, "value"), "is required", line=12, column=5, step_id="sign_in"
    )
    error = UnsupportedSchemaVersion("unsupported", issues=[issue], found=2, supported=(1,))

    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own in-memory payload

    assert type(restored) is UnsupportedSchemaVersion
    assert isinstance(restored, WorkflowValidationError)
    assert restored.issues == (issue,)
    assert dict(restored.context) == {"issues": (issue,), "found": 2, "supported": (1,)}


def test_a_workflow_validation_error_without_issues_has_none() -> None:
    assert WorkflowValidationError("broken").issues == ()


@pytest.mark.parametrize(
    ("location", "rendered"),
    [
        ((), ""),
        (("steps",), "steps"),
        (("steps", 3, "value", "name"), "steps[3].value.name"),
        ((0, "a"), "[0].a"),
    ],
)
def test_locations_render_as_people_write_them(
    location: tuple[str | int, ...], rendered: str
) -> None:
    assert format_location(location) == rendered
    assert ValidationIssue(location, "message").path == rendered


def test_version_conflict_is_not_a_validation_error() -> None:
    assert not issubclass(VersionConflict, WorkflowValidationError)
