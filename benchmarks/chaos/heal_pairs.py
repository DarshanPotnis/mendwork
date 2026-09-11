"""The heal pair table: a seed for every (heal_expected mutation, eligible target) pair.

Under `only=<mutation>` the seed alone decides which eligible target a mutation takes. The
benchmark's ground truth needs every heal_expected mutation to keep every target it can
take working, so this table records, for each possible pair, the smallest seed that
selects it.

Everything is derived inside the browser from the portal's own modules: its target
declarations, its eligibility predicates, and its selection function. Nothing about the
portal is reimplemented here, and the portal has no test hooks.

Regenerate with `make chaos-pairs`.
"""

import asyncio
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

from playwright.async_api import Page, async_playwright
from pydantic import BaseModel, ConfigDict, Field

from mendwork.apps.portal.server import PortalServer
from mendwork.engine.errors import MendworkError

PageId = Literal["login", "dashboard", "reports", "orders"]

TABLE_PATH: Final = Path(__file__).with_name("heal_pairs.json")
PORTAL_ROOT: Final = Path(__file__).resolve().parents[2] / "chaos-portal"
PAGE_PATHS: Final[Mapping[PageId, str]] = MappingProxyType(
    {
        "login": "index.html",
        "dashboard": "dashboard.html",
        "reports": "reports.html",
        "orders": "orders.html",
    }
)
MAX_SEED: Final = 5_000
READY_TIMEOUT_MS: Final = 5_000
REGENERATE_HINT: Final = (
    "Regenerate the table with `make chaos-pairs` and commit benchmarks/chaos/heal_pairs.json."
)

# The portal's own modules, loaded into the page that already runs them. The registry is
# built on an unmutated page, exactly as the page itself builds one before mutating.
_ENGINE = """
  const load = (path) => import(new URL(path, document.baseURI).href);
  const [{ MUTATIONS }, { PAGE_TARGETS }, { TargetRegistry }, { selectMutations }] =
    await Promise.all([
      load("js/mutations/index.js"),
      load("js/app/page-targets.js"),
      load("js/app/targets.js"),
      load("js/chaos/selection.js"),
    ]);
  const pageId = window.__chaos.pageId;
  const registry = new TargetRegistry(document, pageId, PAGE_TARGETS[pageId]);
  const heal = MUTATIONS.filter((m) => m.category === "heal_expected" && m.scope === "target");
  const eligible = (mutation) => registry.all()
    .filter((target) => registry.element(target) !== null && mutation.isEligible(target, registry))
    .map((target) => target.key);
  const selected = (mutationId, seed) => {
    const config = { seed, level: 1, only: [mutationId] };
    const { plan } = selectMutations(config, pageId, MUTATIONS, registry);
    return plan.length === 1 && plan[0].target !== null ? plan[0].target.key : null;
  };
"""

_ELIGIBLE_PAIRS = f"""async () => {{
  {_ENGINE}
  return heal.flatMap((mutation) => eligible(mutation).map((key) => [mutation.id, key]));
}}"""

_FIND_SEEDS = f"""async (maxSeed) => {{
  {_ENGINE}
  const found = [];
  for (const mutation of heal) {{
    const wanted = new Set(eligible(mutation));
    for (let seed = 0; seed <= maxSeed && wanted.size > 0; seed += 1) {{
      const key = selected(mutation.id, seed);
      if (key !== null && wanted.delete(key)) {{
        found.push([mutation.id, key, seed]);
      }}
    }}
    for (const key of wanted) {{
      found.push([mutation.id, key, null]);
    }}
  }}
  return found;
}}"""

_SELECTED_TARGETS = f"""async (entries) => {{
  {_ENGINE}
  return entries.map(([mutationId, seed]) => selected(mutationId, seed));
}}"""


class HealPair(BaseModel):
    """One heal_expected mutation on one target, and a seed that selects exactly that pair."""

    model_config = ConfigDict(frozen=True)

    page: PageId
    mutation: str
    target: str
    seed: int = Field(ge=0)

    @property
    def key(self) -> tuple[PageId, str, str]:
        return (self.page, self.mutation, self.target)


class HealPairTable(BaseModel):
    """The committed table, as stored in heal_pairs.json."""

    model_config = ConfigDict(frozen=True)

    description: str
    regenerate: str
    pairs: tuple[HealPair, ...]


def load_table(path: Path = TABLE_PATH) -> HealPairTable:
    return HealPairTable.model_validate_json(path.read_text(encoding="utf-8"))


def render_table(pairs: Iterable[HealPair]) -> str:
    """The canonical file contents: pairs sorted by page, mutation, and target."""
    table = HealPairTable(
        description=(
            "For every heal_expected mutation and every target it can take, a seed that "
            "makes ?seed=<seed>&only=<mutation> apply exactly that pair."
        ),
        regenerate="make chaos-pairs",
        pairs=tuple(sorted(pairs, key=lambda pair: pair.key)),
    )
    return json.dumps(table.model_dump(mode="json"), indent=2) + "\n"


async def sign_in(page: Page, base_url: str) -> None:
    await open_unmutated(page, base_url, "login")
    await page.get_by_label("Email address").fill("buyer@harborline.test")
    await page.get_by_label("Password").fill("harbor-demo")
    await page.get_by_role("button", name="Sign in").click()
    await page.wait_for_url("**/dashboard.html", timeout=READY_TIMEOUT_MS)


async def open_unmutated(page: Page, base_url: str, page_id: PageId) -> None:
    await page.goto(f"{base_url}{PAGE_PATHS[page_id]}?level=0")
    await page.wait_for_function("() => window.__chaos?.ready === true", timeout=READY_TIMEOUT_MS)


async def eligible_pairs(page: Page, base_url: str) -> frozenset[tuple[PageId, str, str]]:
    """Every (page, mutation, target) the portal's declarations and predicates allow.

    Needs a signed-in page.
    """
    pairs: set[tuple[PageId, str, str]] = set()
    for page_id in PAGE_PATHS:
        await open_unmutated(page, base_url, page_id)
        for mutation, target in await page.evaluate(_ELIGIBLE_PAIRS):
            pairs.add((page_id, str(mutation), str(target)))
    return frozenset(pairs)


async def selected_targets(
    page: Page, base_url: str, pairs: Iterable[HealPair]
) -> dict[HealPair, str | None]:
    """The target the portal's selection actually picks for each pair's mutation and seed."""
    by_page: dict[PageId, list[HealPair]] = {}
    for pair in pairs:
        by_page.setdefault(pair.page, []).append(pair)
    selected: dict[HealPair, str | None] = {}
    for page_id, page_pairs in by_page.items():
        await open_unmutated(page, base_url, page_id)
        keys = await page.evaluate(
            _SELECTED_TARGETS, [[pair.mutation, pair.seed] for pair in page_pairs]
        )
        selected.update(
            zip(page_pairs, (None if key is None else str(key) for key in keys), strict=True)
        )
    return selected


async def find_seeds(page: Page, base_url: str, max_seed: int = MAX_SEED) -> tuple[HealPair, ...]:
    """The smallest selecting seed for every eligible pair. Needs a signed-in page."""
    pairs: list[HealPair] = []
    unreachable: list[str] = []
    for page_id in PAGE_PATHS:
        await open_unmutated(page, base_url, page_id)
        for mutation, target, seed in await page.evaluate(_FIND_SEEDS, max_seed):
            if seed is None:
                unreachable.append(f"{mutation} -> {target}")
            else:
                pairs.append(
                    HealPair(
                        page=page_id, mutation=str(mutation), target=str(target), seed=int(seed)
                    )
                )
    if unreachable:
        raise MendworkError(
            "no seed selects some eligible pairs", max_seed=max_seed, pairs=unreachable
        )
    return tuple(pairs)


async def generate() -> tuple[HealPair, ...]:
    """Serve the portal, sign in, and find a seed for every eligible pair."""
    with PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await sign_in(page, server.url)
                return await find_seeds(page, server.url)
            finally:
                await browser.close()


def main() -> int:
    pairs = asyncio.run(generate())
    TABLE_PATH.write_text(render_table(pairs), encoding="utf-8")
    sys.stdout.write(f"Wrote {len(pairs)} heal pairs to {TABLE_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
