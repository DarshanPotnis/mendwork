"""Egress enforcement in Chromium: redirect hops, subresources, frames, and clicks.

A local HTTP server plays every site: pages that redirect to a metadata address, to a host off the
allowlist, and to a name that resolves to a private address, a page loading an image from a private
address, and a page framing a host that resolves to loopback. Names resolve through a fake
resolver, so nothing but this machine is ever contacted. ADR 0011, finding F1: Playwright routing
never sees a redirect hop, so each of these is enforced by the gateway or the document filter.
"""

import asyncio
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from mendwork.adapters.browser_playwright.launcher import (
    EgressEnforcement,
    SessionOptions,
    open_session,
)
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.runs import parse_run_id
from mendwork.engine.errors import EgressBlocked, NavigationError
from mendwork.engine.safety.egress import EgressPolicy, LoopbackException
from mendwork.engine.safety.egress_blocks import EgressLayer
from tests.fakes.egress import FakeResolver
from tests.unit.replay.builders import selector

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

OPTIONS: Final = SessionOptions(
    viewport_width=1280, viewport_height=720, default_timeout_ms=5_000, trace_on_failure=False
)
TIMEOUT_MS: Final = 5_000


class Site:
    """The local server and the paths it was asked for."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self.lock = threading.Lock()
        self.port = 0

    def base(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}"


def handler_for(site: Site) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return None

        def do_GET(self) -> None:
            with site.lock:
                site.hits.append(self.path)
            redirects = {
                "/start": "/hop",
                "/hop": "/final",
                "/to-meta": "/hop-meta",
                "/hop-meta": "http://169.254.169.254/latest/meta-data/",
                "/to-unlisted": "http://unlisted.example.test/landing",
                "/to-rebind": f"http://rebind.example.test:{site.port}/final",
            }
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
                self.end_headers()
                return
            bodies = {
                "/with-image": "<img src='http://10.255.255.1/pixel.png'><p>image</p>",
                "/with-frame": f"<iframe src='http://embed.example.test:{site.port}/final'></iframe>",
                "/link": "<a id='go' href='/to-meta'>Go</a>",
            }
            body = bodies.get(self.path, f"<p>{self.path}</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    served = Site()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(served))
    served.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield served
    finally:
        server.shutdown()
        server.server_close()


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def session_for(
    browser: Browser, site: Site, *, extra_port: int | None = None
) -> AsyncIterator[PlaywrightSession]:
    ports = [site.port] if extra_port is None else [site.port, extra_port]
    policy = EgressPolicy(
        allowed_domains=("rebind.example.test",),
        loopback_exceptions=tuple(LoopbackException(address="127.0.0.1", port=p) for p in ports),
    )
    resolver = FakeResolver(
        {"rebind.example.test": ("10.0.0.5",), "embed.example.test": ("127.0.0.1",)}
    )
    scripts = await asyncio.to_thread(PageScripts.load)
    enforcement = EgressEnforcement(resolver=resolver, timeout_ms=TIMEOUT_MS)
    run_id = parse_run_id("20260914T000000Z-0000e9e5")
    async with open_session(
        browser, scripts, OPTIONS, run_id, policy=policy, enforcement=enforcement
    ) as session:
        with site.lock:
            site.hits.clear()
        yield session


@pytest_asyncio.fixture(loop_scope="session")
async def session(browser: Browser, site: Site) -> AsyncIterator[PlaywrightSession]:
    async with session_for(browser, site) as opened:
        yield opened


async def test_a_redirect_chain_between_allowed_origins_loads(
    session: PlaywrightSession, site: Site
) -> None:
    outcome = await session.navigate(f"{site.base()}/start", timeout_ms=TIMEOUT_MS)

    assert outcome.url == f"{site.base()}/final"
    assert await session.take_egress_blocks() == ()


async def test_a_redirect_hop_to_a_metadata_address_is_blocked_before_it_is_sent(
    session: PlaywrightSession, site: Site
) -> None:
    with pytest.raises(EgressBlocked) as caught:
        await session.navigate(f"{site.base()}/to-meta", timeout_ms=TIMEOUT_MS)

    context = caught.value.context
    assert (context["rule"], context["address_range"], context["layer"]) == (
        "blocked_address",
        "cloud_metadata",
        "document",
    )
    assert site.hits == ["/to-meta", "/hop-meta"]


async def test_a_redirect_to_a_host_off_the_allowlist_is_blocked(
    session: PlaywrightSession, site: Site
) -> None:
    with pytest.raises(EgressBlocked) as caught:
        await session.navigate(f"{site.base()}/to-unlisted", timeout_ms=TIMEOUT_MS)

    assert (caught.value.context["rule"], caught.value.context["host"]) == (
        "not_allowlisted",
        "unlisted.example.test",
    )


async def test_a_redirect_to_a_name_resolving_to_a_private_address_is_refused_at_connect(
    session: PlaywrightSession, site: Site
) -> None:
    with pytest.raises(EgressBlocked) as caught:
        await session.navigate(f"{site.base()}/to-rebind", timeout_ms=TIMEOUT_MS)

    context = caught.value.context
    assert (context["layer"], context["address"], context["address_range"]) == (
        "connection",
        "10.0.0.5",
        "private",
    )
    assert site.hits == ["/to-rebind"]


async def test_loopback_by_name_is_refused_even_on_the_excepted_port(
    session: PlaywrightSession, site: Site
) -> None:
    with pytest.raises(EgressBlocked) as caught:
        await session.navigate(f"{site.base('localhost')}/final", timeout_ms=TIMEOUT_MS)

    assert caught.value.context["rule"] == "not_allowlisted"
    assert site.hits == []


async def test_a_subresource_from_a_private_address_is_refused_and_reported(
    session: PlaywrightSession, site: Site
) -> None:
    await session.navigate(f"{site.base()}/with-image", timeout_ms=TIMEOUT_MS)

    blocks = await session.take_egress_blocks()

    assert [(block.layer, block.refusal.address) for block in blocks] == [
        (EgressLayer.CONNECTION, "10.255.255.1")
    ]


async def test_a_frame_skips_the_allowlist_but_not_the_connection_check(
    session: PlaywrightSession, site: Site
) -> None:
    await session.navigate(f"{site.base()}/with-frame", timeout_ms=TIMEOUT_MS)

    blocks = await session.take_egress_blocks()

    assert [(block.layer, block.refusal.host, block.refusal.address_range) for block in blocks] == [
        (EgressLayer.CONNECTION, "embed.example.test", "loopback")
    ]


async def test_a_click_that_leads_to_a_metadata_address_is_reported(
    session: PlaywrightSession, site: Site
) -> None:
    await session.navigate(f"{site.base()}/link", timeout_ms=TIMEOUT_MS)
    link = await session.resolve_unique(selector(strategy="css", value="#go"))
    assert link.element is not None

    await session.click(link.element, timeout_ms=TIMEOUT_MS)
    await session.wait_until_settled(quiet_frames=2, timeout_ms=TIMEOUT_MS)

    blocks = await session.take_egress_blocks()
    assert [(block.layer, block.main_frame, block.refusal.address_range) for block in blocks] == [
        (EgressLayer.DOCUMENT, True, "cloud_metadata")
    ]


async def test_a_refused_upstream_is_the_connection_failure_it_was(
    browser: Browser, site: Site
) -> None:
    dead = unused_port()
    async with session_for(browser, site, extra_port=dead) as opened:
        with pytest.raises(NavigationError) as caught:
            await opened.navigate(f"http://127.0.0.1:{dead}/", timeout_ms=TIMEOUT_MS)

    assert caught.value.context["reason"] == "ERR_CONNECTION_REFUSED"
