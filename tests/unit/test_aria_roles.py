"""The domain's ARIA roles are exactly the roles Playwright's role locator accepts."""

from typing import get_args, get_type_hints

from playwright.async_api import Page

from mendwork.engine.domain.enums import AriaRole


def test_aria_roles_match_playwright() -> None:
    playwright_roles = set(get_args(get_type_hints(Page.get_by_role)["role"]))

    assert {role.value for role in AriaRole} == playwright_roles
