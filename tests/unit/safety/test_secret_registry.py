"""Every secret a process resolves reaches the scrubber its logs are cleaned with, first."""

import pytest

from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.errors import SecretUnavailable
from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_registry import RegisteringSecretResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.ports import DictSecretResolver

pytestmark = pytest.mark.asyncio


async def test_a_resolved_secret_is_registered_before_it_is_returned() -> None:
    scrubber = SecretScrubber()
    resolver = RegisteringSecretResolver(
        DictSecretResolver({"portal_password": "hunter2-ledger"}), scrubber
    )

    missing = await resolver.missing((SecretName("portal_password"), SecretName("vault_key")))
    registered_before = scrubber.active
    value = await resolver.resolve(SecretName("portal_password"))

    assert missing == ("vault_key",)
    assert registered_before is False
    assert value.get_secret_value() == "hunter2-ledger"
    assert scrubber.scrub_text("typed hunter2-ledger") == f"typed {REDACTED}"


async def test_a_secret_that_cannot_be_resolved_registers_nothing() -> None:
    scrubber = SecretScrubber()
    resolver = RegisteringSecretResolver(DictSecretResolver({}), scrubber)

    with pytest.raises(SecretUnavailable):
        await resolver.resolve(SecretName("vault_key"))

    assert scrubber.active is False
