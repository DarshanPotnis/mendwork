"""Workflow-wide consistency: unique step ids, and declarations that match their use.

These rules span the whole document, so no single field can check them. They are a pure
function returning every issue with its exact path, so a file with three mistakes reports
three errors, each at its own line.
"""

from collections.abc import Sequence
from difflib import get_close_matches

from mendwork.engine.domain.enums import InputKind
from mendwork.engine.domain.steps import NavigateStep, Step, step_value
from mendwork.engine.domain.values import InputDeclaration, InputValue, SecretValue
from mendwork.engine.errors import ValidationIssue


def _suggest(name: str, candidates: Sequence[str]) -> str:
    closest = get_close_matches(name, candidates, n=1)
    return f"; did you mean '{closest[0]}'?" if closest else ""


def _duplicate_step_ids(steps: Sequence[Step]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    first_index: dict[str, int] = {}
    for index, step in enumerate(steps):
        if step.id in first_index:
            issues.append(
                ValidationIssue(
                    ("steps", index, "id"),
                    f"duplicate step id '{step.id}' "
                    f"(already used by steps[{first_index[step.id]}])",
                )
            )
        else:
            first_index[step.id] = index
    return issues


def _declaration_issues(
    inputs: Sequence[InputDeclaration], secrets: Sequence[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_inputs: set[str] = set()
    for index, declaration in enumerate(inputs):
        if declaration.name in seen_inputs:
            message = f"input '{declaration.name}' is declared more than once"
            issues.append(ValidationIssue(("inputs", index, "name"), message))
        seen_inputs.add(declaration.name)
    seen_secrets: set[str] = set()
    for index, name in enumerate(secrets):
        if name in seen_secrets:
            issues.append(
                ValidationIssue(("secrets", index), f"secret '{name}' is declared more than once")
            )
        elif name in seen_inputs:
            issues.append(
                ValidationIssue(
                    ("secrets", index), f"'{name}' is declared both as an input and as a secret"
                )
            )
        seen_secrets.add(name)
    return issues


def find_reference_issues(
    inputs: Sequence[InputDeclaration], secrets: Sequence[str], steps: Sequence[Step]
) -> tuple[ValidationIssue, ...]:
    """Every consistency problem between declarations and the steps that use them."""
    issues = _duplicate_step_ids(steps) + _declaration_issues(inputs, secrets)
    declared_inputs = {declaration.name: declaration for declaration in inputs}
    declared_secrets = set(secrets)
    used_inputs: set[str] = set()
    used_secrets: set[str] = set()

    for index, step in enumerate(steps):
        value = step_value(step)
        location = ("steps", index, "value", "name")
        if isinstance(value, InputValue):
            used_inputs.add(value.name)
            declaration = declared_inputs.get(value.name)
            if declaration is None:
                issues.append(
                    ValidationIssue(
                        location,
                        f"input '{value.name}' is not declared under inputs"
                        + _suggest(value.name, list(declared_inputs)),
                    )
                )
            elif isinstance(step, NavigateStep) and declaration.kind is not InputKind.URL:
                issues.append(
                    ValidationIssue(
                        location,
                        f"a navigate step needs a url input, but '{value.name}' is a "
                        f"{declaration.kind} input",
                    )
                )
        elif isinstance(value, SecretValue):
            used_secrets.add(value.name)
            if value.name not in declared_secrets:
                issues.append(
                    ValidationIssue(
                        location,
                        f"secret '{value.name}' is not declared under secrets"
                        + _suggest(value.name, sorted(declared_secrets)),
                    )
                )

    for index, declaration in enumerate(inputs):
        if declaration.name not in used_inputs:
            issues.append(
                ValidationIssue(
                    ("inputs", index, "name"),
                    f"input '{declaration.name}' is declared but no step uses it",
                )
            )
    for index, name in enumerate(secrets):
        if name not in used_secrets:
            issues.append(
                ValidationIssue(
                    ("secrets", index), f"secret '{name}' is declared but no step uses it"
                )
            )
    return tuple(issues)
