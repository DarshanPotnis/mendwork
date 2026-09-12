"""Step values, resolved at the moment of use.

Literal and input values are plain text. A secret is fetched from the SecretResolver only
when its fill is about to happen, registered with the run's scrubber before it goes
anywhere, and compared back only as "the field is not empty".
"""

from collections.abc import Mapping
from dataclasses import dataclass

from mendwork.engine.domain.enums import ValueKind
from mendwork.engine.domain.identifiers import InputName
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue
from mendwork.engine.ports.browser_types import (
    EqualsText,
    FieldExpectation,
    FillText,
    NonEmpty,
    PlainText,
    SecretText,
)
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class TypedValue:
    """A value ready to type, and what the field should hold afterwards."""

    text: FillText
    expectation: FieldExpectation
    kind: ValueKind


class ValueResolver:
    """Resolves one run's step values from its bound inputs and its secrets."""

    def __init__(
        self, inputs: Mapping[InputName, str], secrets: SecretResolver, scrubber: SecretScrubber
    ) -> None:
        self._inputs = inputs
        self._secrets = secrets
        self._scrubber = scrubber

    def plain(self, value: LiteralValue | InputValue) -> str:
        """A literal or input value."""
        match value:
            case LiteralValue():
                return value.value
            case InputValue():
                return self._inputs[value.name]

    async def typed(self, value: LiteralValue | InputValue | SecretValue) -> TypedValue:
        """A fill value; a secret is resolved now and registered for scrubbing."""
        match value:
            case LiteralValue() | InputValue():
                text = self.plain(value)
                return TypedValue(PlainText(value=text), EqualsText(value=text), value.kind)
            case SecretValue():
                secret = await self._secrets.resolve(value.name)
                self._scrubber.register(secret)
                return TypedValue(SecretText(value=secret), NonEmpty(), value.kind)
