"""Enter in the password field behaves exactly as clicking the form's default button would.

A browser submits a form on Enter by sending a synthetic click to its default button, so
whatever that button would do on a click, Enter does too: a healed button signs in, a
dangerous or duplicated one records a wrong action, and a removed one leaves nothing to
activate.
"""

from typing import Literal

import pytest
from playwright.async_api import expect

from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD, PortalDriver

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

Outcome = Literal["signs_in", "records_wrong_action", "does_nothing"]


async def open_login_with_sign_in_mutated(portal: PortalDriver, mutation_id: str | None) -> None:
    """Find a seed whose only= mutation lands on the sign-in button. Deterministic: the same
    seed is found on every run."""
    if mutation_id is None:
        await portal.open("login", level=0)
        return
    for seed in range(1, 41):
        state = await portal.open("login", seed=seed, only=[mutation_id])
        if any(applied.target_key == "login.sign_in" for applied in state.applied):
            return
    raise AssertionError(f"no seed in 1..40 puts {mutation_id} on login.sign_in")


@pytest.mark.parametrize(
    ("mutation_id", "outcome"),
    [
        (None, "signs_in"),
        ("synonym_rename", "signs_in"),
        ("change_ids_classes", "signs_in"),
        ("extra_wrappers", "signs_in"),
        ("icon_only_aria", "signs_in"),
        ("remove_target", "does_nothing"),
        ("duplicate_plausible", "records_wrong_action"),
        ("dangerous_rename", "records_wrong_action"),
    ],
)
async def test_enter_in_the_password_field_matches_clicking_the_default_button(
    portal: PortalDriver, mutation_id: str | None, outcome: Outcome
) -> None:
    await open_login_with_sign_in_mutated(portal, mutation_id)
    await (await portal.locate("login.email")).fill(DEMO_EMAIL)
    password = await portal.locate("login.password")
    await password.fill(DEMO_PASSWORD)

    if outcome == "signs_in":
        async with portal.expect_page("dashboard"):
            await password.press("Enter")
        assert await portal.is_signed_in()
        return

    await password.press("Enter")

    # The submit handler runs synchronously during the key press, so by now a session
    # would already exist if the form had been submitted.
    assert not await portal.is_signed_in()
    state = await portal.state()
    assert state.page_id == "login"
    if outcome == "records_wrong_action":
        assert len(state.wrong_actions) == 1
        assert state.wrong_actions[0].mutation_id == mutation_id
        await expect(
            portal.page.get_by_role("alert").filter(has_text="Wrong action recorded")
        ).to_be_visible()
    else:
        assert state.wrong_actions == ()
