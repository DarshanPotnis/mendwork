"""Scanning a page for healing candidates.

``extract_candidates.js`` finds the visible elements an action could receive and returns
only their references and a count. Each element is then pinned and read with the same
identity and facts scripts replay and recording use, so there is no second role, name, or
facts logic, and no field's content is read.
"""

from collections.abc import Callable
from typing import Final

from playwright.async_api import ElementHandle, JSHandle, Page
from playwright.async_api import Error as PlaywrightError
from pydantic import NonNegativeInt, TypeAdapter

from mendwork.adapters.browser_playwright.errors import (
    browser_closed,
    is_closed,
    is_context_destroyed,
    page_read_error,
)
from mendwork.adapters.browser_playwright.facts import FACTS_REQUEST, FactsReply
from mendwork.adapters.browser_playwright.identity import read_identity
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.candidate_types import CandidateQuery, CandidateScan, LiveCandidate

_KINDS: Final = {
    ActionType.CLICK: "click",
    ActionType.PRESS: "press",
    ActionType.FILL: "fill",
    ActionType.SELECT: "select",
    ActionType.NAVIGATE: "none",
}
_COUNT: Final[TypeAdapter[int]] = TypeAdapter(NonNegativeInt)


async def scan_candidates(
    page: Page,
    scripts: PageScripts,
    query: CandidateQuery,
    pin: Callable[[ElementHandle], ElementRef],
) -> CandidateScan:
    """Pin up to ``query.limit`` candidates with their identity and facts, and count them all.

    A document replaced mid-scan yields what was read so far; the engine's stability check
    discards that reading and releases what was pinned.
    """
    request = {"kind": _KINDS[query.action], "limit": query.limit}
    try:
        result = await page.evaluate_handle(scripts.extract_candidates, request)
    except PlaywrightError as error:
        if is_closed(error):
            raise browser_closed(error) from error
        if is_context_destroyed(error):
            return CandidateScan(candidates=(), total=0)
        raise page_read_error(error) from error
    containers: list[JSHandle] = [result]
    candidates: list[LiveCandidate] = []
    total = 0
    try:
        total_handle = await result.get_property("total")
        containers.append(total_handle)
        total = _COUNT.validate_python(await total_handle.json_value())
        elements = await result.get_property("elements")
        containers.append(elements)
        properties = await elements.get_properties()
        for key in sorted((key for key in properties if key.isdigit()), key=int):
            child = properties[key]
            element = child.as_element()
            if element is None:
                containers.append(child)
                continue
            candidates.append(await _read(element, scripts, pin))
    except PlaywrightError as error:
        if is_closed(error):
            raise browser_closed(error) from error
        if not is_context_destroyed(error):
            raise page_read_error(error) from error
        total = max(total, len(candidates))
    finally:
        for handle in containers:
            await _dispose(handle)
    return CandidateScan(candidates=tuple(candidates), total=max(total, len(candidates)))


async def _read(
    element: ElementHandle, scripts: PageScripts, pin: Callable[[ElementHandle], ElementRef]
) -> LiveCandidate:
    ref = pin(element)
    raw = await read_identity(element, scripts)
    facts = FactsReply.model_validate(await element.evaluate(scripts.element_facts, FACTS_REQUEST))
    identity = ElementIdentity(tag=raw.tag, input_type=raw.type, role=raw.role, name=raw.name)
    return LiveCandidate(element=ref, identity=identity, facts=facts.facts())


async def _dispose(handle: JSHandle) -> None:
    try:
        await handle.dispose()
    except PlaywrightError as error:
        # A handle whose document was replaced or whose page closed holds nothing.
        if not (is_closed(error) or is_context_destroyed(error)):
            raise
