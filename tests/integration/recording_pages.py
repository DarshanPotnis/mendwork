"""Fixture pages for recording tests, served through Playwright routes on the recording context.

Each page is written for one behaviour. Their scripts are part of the site under test, not
Mendwork's page scripts.
"""

from collections.abc import Mapping
from typing import Final

from playwright.async_api import Page, Route

ORIGIN: Final = "https://fixture.mendwork.test/"

INTERACTIONS: Final = {
    "profile.html": """<!doctype html><title>Profile</title><main>
  <h1>Profile</h1>
  <p id="background">Some text that does nothing.</p>
  <form id="profile" onsubmit="event.preventDefault()">
    <label for="name">Full name</label><input id="name" name="name">
    <label for="when">Start date</label><input id="when" name="when" type="date">
  </form>
  <button id="apply" type="button"><span id="apply-label">Apply profile</span></button>
  <p id="status" role="status"></p>
  <a id="next" href="next.html">Next page</a>
</main><script>
  document.getElementById("apply").addEventListener("click", () => {
    const name = document.getElementById("name").value;
    document.getElementById("status").textContent = "Applied for " + name;
  });
</script>""",
    "next.html": """<!doctype html><title>Next</title><main>
  <h1>Next page</h1>
  <button id="done" type="button">Done</button>
  <input id="upload" type="file" aria-label="Upload">
  <iframe title="Embedded" srcdoc="<button id='inner'>Inside</button>"></iframe>
</main>""",
    "elsewhere.html": """<!doctype html><title>Elsewhere</title><main>
  <h1>Search</h1>
  <input id="query" aria-label="Query" oninput="location.assign('final.html')">
</main>""",
    "final.html": """<!doctype html><title>Final</title><main>
  <h1>Results</h1>
  <button id="finish" type="button">Finish</button>
</main>""",
}

TARGETS: Final = {
    "duplicates.html": """<!doctype html><main>
  <button type="button" data-testid="save-primary">Keep</button>
  <button type="button">Keep</button>
</main>""",
    "twins.html": """<!doctype html><main>
  <button type="button">Go</button>
  <button type="button">Go</button>
</main>""",
    "rows.html": """<!doctype html><main><h1>Orders</h1><table>
  <thead><tr><th>Order</th><th>Actions</th></tr></thead>
  <tbody>
    <tr><th scope="row">PO-1</th><td><button type="button">View</button></td></tr>
    <tr><th scope="row">PO-2</th><td><button type="button"
      onclick="document.getElementById('detail').hidden = false">View</button></td></tr>
    <tr><th scope="row">PO-3</th><td><button type="button">View</button></td></tr>
  </tbody></table>
  <section id="detail" hidden><h2>Order PO-2</h2></section>
</main>""",
    # A message loop mutates the DOM between every task the page runs.
    "restless.html": """<!doctype html><main><button type="button" id="save">Keep</button>
  <span id="tick"></span></main><script>
  const channel = new MessageChannel();
  let n = 0;
  channel.port1.onmessage = () => {
    document.getElementById("tick").setAttribute("data-n", String(n++));
    channel.port2.postMessage(0);
  };
  channel.port2.postMessage(0);
</script>""",
    "popup.html": """<!doctype html><main>
  <button type="button" onclick="window.open('about:blank')">Open window</button>
</main>""",
    "checkpoints.html": """<!doctype html><title>Checkpoints</title><main>
  <h1>Exports</h1>
  <a href="data:text/csv;charset=utf-8,a%2Cb" download="report.csv">Export report</a>
  <button type="button" id="add">Add results</button>
  <form id="filter"><button>Apply filter</button></form>
  <p id="message" role="status"></p>
</main><script>
  document.getElementById("add").addEventListener("click", () => {
    for (let i = 0; i < 2; i++) {
      const heading = document.createElement("h2");
      heading.textContent = "Results";
      document.querySelector("main").append(heading);
    }
  });
  document.getElementById("filter").addEventListener("submit", (event) => {
    event.preventDefault();
    document.getElementById("message").textContent = "Filtered 3 rows";
  });
</script>""",
}

BOUNDARY: Final = {
    "sign-in.html": """<!doctype html><title>Sign in</title><main>
  <h1>Sign in</h1>
  <form onsubmit="event.preventDefault(); document.getElementById('welcome').hidden = false">
    <label for="email">Email address</label><input id="email" type="email">
    <label for="password">Password</label><input id="password" type="password">
    <label for="code">Code</label><input id="code" style="-webkit-text-security: disc">
    <div style="-webkit-text-security: disc"><label for="note">Note</label><input id="note"></div>
    <button type="submit">Continue</button>
  </form>
  <h2 id="welcome" hidden>Welcome</h2>
</main>""",
}


def site(pages: Mapping[str, str]) -> "PageRoutes":
    """A prepare hook that serves the pages on the recording page's context."""
    return PageRoutes(pages)


class PageRoutes:
    """Serves fixture pages under ORIGIN; anything else is a 404."""

    def __init__(self, pages: Mapping[str, str]) -> None:
        self._pages = pages

    async def __call__(self, page: Page) -> None:
        await page.context.route(f"{ORIGIN}**", self._serve)

    async def _serve(self, route: Route) -> None:
        name = route.request.url.removeprefix(ORIGIN).split("?")[0]
        body = self._pages.get(name)
        if body is None:
            await route.fulfill(status=404, body="not found")
        else:
            await route.fulfill(body=body, content_type="text/html; charset=utf-8")
