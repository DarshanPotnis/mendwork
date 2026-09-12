"""An element's identity: computed in the page, confirmed by Playwright's own role locator.

The page script mirrors Playwright's role and accessible-name computation. Confirmation
then asks Playwright directly, through the same selector mapping Rung 0 uses: does a
``role_name`` selector with this role and exact name find this element? If not, the
computed identity is not trusted and is reported as unconfirmed. That makes the identity
check consistent with Playwright's matching by construction, and never reads a field's
value (``aria_snapshot`` would, passwords included).
"""

from typing import Final

from playwright.async_api import ElementHandle, Page
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from mendwork.adapters.browser_playwright.locators import apply_selector
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.engine.domain.enums import AriaRole, SelectorStrategy
from mendwork.engine.domain.selectors import ByRole
from mendwork.engine.ports.browser_types import ElementIdentity

ROLES: Final = [role.value for role in AriaRole]
_KEYS: Final[TypeAdapter[list[int]]] = TypeAdapter(list[int])


class RawIdentity(BaseModel):
    """What element_identity.js returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: str
    type: str | None
    role: str | None
    name: str
    embedded_control: bool = Field(alias="embeddedControl")
    connected: bool


async def read_identity(handle: ElementHandle, scripts: PageScripts) -> RawIdentity:
    """Run the identity script on one element."""
    return RawIdentity.model_validate(await handle.evaluate(scripts.element_identity, ROLES))


async def identify(
    page: Page, handle: ElementHandle, scripts: PageScripts, *, confirm: bool
) -> ElementIdentity:
    """The element's identity, confirmed by Playwright when asked and possible."""
    raw = await read_identity(handle, scripts)
    confirmed: bool | None = None
    if confirm:
        confirmed = False if raw.embedded_control else await _confirmed(page, handle, scripts, raw)
    return ElementIdentity(
        tag=raw.tag, input_type=raw.type, role=raw.role, name=raw.name, confirmed=confirmed
    )


async def same_node(page: Page, scripts: PageScripts, handles: list[ElementHandle]) -> list[int]:
    """For each handle, the index of the first handle that is the same DOM node."""
    return _KEYS.validate_python(await page.evaluate(scripts.element_keys, handles))


async def _confirmed(
    page: Page, handle: ElementHandle, scripts: PageScripts, raw: RawIdentity
) -> bool | None:
    # Without a role or a name there is nothing Playwright's role locator can be asked.
    if raw.role is None or not raw.name:
        return None
    try:
        selector = ByRole(
            strategy=SelectorStrategy.ROLE_NAME, role=AriaRole(raw.role), name=raw.name, exact=True
        )
    except ValidationError:
        # A name no fingerprint could record (too long, control characters) cannot match one.
        return False
    candidates = await apply_selector(page, selector).filter(visible=True).element_handles()
    try:
        keys = await same_node(page, scripts, [handle, *candidates])
    finally:
        for candidate in candidates:
            await candidate.dispose()
    return 0 in keys[1:]
