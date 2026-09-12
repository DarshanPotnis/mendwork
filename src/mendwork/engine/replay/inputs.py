"""Binding the inputs supplied for a run to the workflow's declarations.

Every problem is reported at once: unknown names, missing required inputs, and values
that are invalid for their kind. Messages name inputs but never repeat their values.
"""

from collections.abc import Mapping, Sequence
from difflib import get_close_matches

from mendwork.engine.domain.identifiers import InputName
from mendwork.engine.domain.values import InputDeclaration, parse_input_value
from mendwork.engine.errors import RunInputError, ValidationIssue


def bind_inputs(
    declarations: Sequence[InputDeclaration], supplied: Mapping[str, str]
) -> dict[InputName, str]:
    """The value of every declared input: the supplied one, or the declared default."""
    declared = {declaration.name: declaration for declaration in declarations}
    issues = [
        ValidationIssue(("inputs", name), _unknown_message(name, list(declared)))
        for name in sorted(supplied)
        if name not in declared
    ]
    bound: dict[InputName, str] = {}
    for declaration in declarations:
        raw = supplied.get(declaration.name)
        if raw is None:
            if declaration.default is None:
                hint = f"--input {declaration.name}=<{declaration.kind}>"
                issues.append(
                    ValidationIssue(
                        ("inputs", declaration.name), f"is required: supply it with {hint}"
                    )
                )
            else:
                bound[declaration.name] = declaration.default
            continue
        try:
            bound[declaration.name] = parse_input_value(declaration.kind, raw)
        except ValueError as error:
            issues.append(
                ValidationIssue(("inputs", declaration.name), _without_value(str(error), raw))
            )
    if issues:
        raise RunInputError(f"the run inputs have {len(issues)} problem(s)", issues=issues)
    return bound


def _unknown_message(name: str, declared: list[str]) -> str:
    message = f"'{name}' is not an input of this workflow"
    closest = get_close_matches(name, declared, n=1)
    if closest:
        return f"{message}; did you mean '{closest[0]}'?"
    if declared:
        return f"{message}; its inputs are {', '.join(sorted(declared))}"
    return f"{message}; it declares no inputs"


def _without_value(message: str, raw: str) -> str:
    # Some value checks name the offending value ("2026-02-30 is not a real calendar date").
    return message.replace(raw, "the value") if raw else message
