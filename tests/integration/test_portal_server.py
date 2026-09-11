"""The portal server sets the MIME types and cache headers the browser tests rely on."""

import http.client
import io
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from mendwork.apps.portal import __main__ as portal_main
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.errors import MendworkError
from tests.integration.portal import PORTAL_ROOT


@pytest.fixture
def server() -> Iterator[PortalServer]:
    with PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as running:
        yield running


def get(server: PortalServer, path: str) -> http.client.HTTPResponse:
    address = urlsplit(server.url)
    connection = http.client.HTTPConnection(address.hostname or "", address.port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    response.read()
    connection.close()
    return response


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/index.html", "text/html; charset=utf-8"),
        ("/js/pages/reports.js", "text/javascript; charset=utf-8"),
        ("/css/portal.css", "text/css; charset=utf-8"),
        ("/favicon.svg", "image/svg+xml"),
    ],
)
def test_files_are_served_with_explicit_types_and_no_caching(
    server: PortalServer, path: str, content_type: str
) -> None:
    response = get(server, path)

    assert response.status == 200
    assert response.getheader("Content-Type") == content_type
    assert response.getheader("Cache-Control") == "no-store, no-cache, must-revalidate"
    assert response.getheader("Pragma") == "no-cache"
    assert response.getheader("Expires") == "0"


def test_an_unlisted_extension_falls_back_to_a_generic_type(tmp_path: Path) -> None:
    (tmp_path / "notes.xyz").write_text("plain", encoding="utf-8")
    with PortalServer(tmp_path, host="127.0.0.1", port=0) as running:
        response = get(running, "/notes.xyz")

    assert response.getheader("Content-Type") == "application/octet-stream"


@pytest.mark.parametrize("path", ["/js/", "/../pyproject.toml", "/missing.html"])
def test_listings_traversal_and_missing_files_are_not_found(
    server: PortalServer, path: str
) -> None:
    assert get(server, path).status == 404


def test_a_missing_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MendworkError, match="not a directory") as caught:
        PortalServer(tmp_path / "absent", host="127.0.0.1", port=0)

    assert caught.value.context["root"] == str(tmp_path / "absent")


def test_a_taken_port_is_reported_with_context(server: PortalServer) -> None:
    port = urlsplit(server.url).port

    with pytest.raises(MendworkError, match="could not start") as caught:
        PortalServer(PORTAL_ROOT, host="127.0.0.1", port=port or 0)

    assert caught.value.context == {"host": "127.0.0.1", "port": port}


def test_main_prints_the_url_and_serves_until_told_to_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENDWORK_PORTAL_PORT", "0")
    output = io.StringIO()
    statuses: list[int] = []

    def fetch_index() -> None:
        url = output.getvalue().split("running at ", 1)[1].split(" ", 1)[0]
        # The URL is the loopback address the server under test printed.
        with urllib.request.urlopen(f"{url}index.html", timeout=5) as response:  # noqa: S310
            statuses.append(response.status)

    exit_code = portal_main.main(["--root", str(PORTAL_ROOT)], wait=fetch_index, stdout=output)

    assert exit_code == 0
    assert output.getvalue().startswith("Chaos portal running at http://127.0.0.1:")
    assert statuses == [200]


def test_ctrl_c_ends_the_wait_instead_of_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[bool] = []

    class InterruptedEvent:
        def wait(self) -> bool:
            waited.append(True)
            raise KeyboardInterrupt

    # Replaces only this module's view of threading, never the real threading.Event.
    monkeypatch.setattr(portal_main, "threading", SimpleNamespace(Event=InterruptedEvent))

    portal_main.wait_for_interrupt()

    assert waited == [True]
