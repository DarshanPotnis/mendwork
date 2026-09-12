"""Parsing ``--input key=value`` arguments."""

from collections.abc import Sequence

from mendwork.engine.errors import RunInputError, ValidationIssue


def parse_input_arguments(arguments: Sequence[str]) -> dict[str, str]:
    """The inputs given on the command line; every malformed argument is reported at once.

    The value is everything after the first ``=``, so a value may itself contain ``=``.
    Values are never repeated in messages.
    """
    supplied: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for position, argument in enumerate(arguments, start=1):
        name, separator, value = argument.partition("=")
        name = name.strip()
        if not separator or not name:
            issues.append(
                ValidationIssue(
                    ("inputs", position), f"--input number {position} must look like name=value"
                )
            )
        elif name in supplied:
            issues.append(ValidationIssue(("inputs", name), "is given more than once"))
        else:
            supplied[name] = value
    if issues:
        raise RunInputError(f"{len(issues)} malformed --input argument(s)", issues=issues)
    return supplied
