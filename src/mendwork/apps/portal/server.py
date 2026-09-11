"""A static file server for the chaos portal.

`make portal` and the browser tests use this one class, so the MIME types and cache
headers a developer sees are exactly the ones the tests verified.
"""

import os
import threading
from collections.abc import Mapping
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Final, Self

import structlog

from mendwork.engine.errors import MendworkError

# Explicit, not the platform mimetypes database, which differs between macOS and Linux.
# Browsers refuse to run an ES module served with a non-JavaScript MIME type.
CONTENT_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
    }
)
FALLBACK_CONTENT_TYPE: Final = "application/octet-stream"
# serve_forever checks for shutdown once per poll; its 0.5 s default made every stop, and
# every Ctrl+C, wait half a second for nothing.
SHUTDOWN_POLL_SECONDS: Final = 0.05


class _PortalRequestHandler(SimpleHTTPRequestHandler):
    def guess_type(self, path: str | os.PathLike[str]) -> str:
        return CONTENT_TYPES.get(Path(path).suffix.lower(), FALLBACK_CONTENT_TYPE)

    def end_headers(self) -> None:
        # Every load must pick up edited files and a fresh mutation run.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def list_directory(self, path: str | os.PathLike[str]) -> BytesIO | None:
        self.send_error(HTTPStatus.NOT_FOUND, "Directory listing is disabled")
        return None

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        structlog.get_logger("mendwork.portal").info(
            "portal_request", method=self.command, path=self.path, status=str(code)
        )

    def log_error(self, format: str, *args: object) -> None:
        structlog.get_logger("mendwork.portal").warning("portal_error", detail=format % args)


class PortalServer:
    """Serve a directory over HTTP from a background thread until stopped."""

    def __init__(self, root: Path, *, host: str, port: int) -> None:
        if not root.is_dir():
            raise MendworkError("chaos portal root is not a directory", root=str(root))
        self._host = host
        handler = partial(_PortalRequestHandler, directory=str(root.resolve()))
        try:
            self._server = ThreadingHTTPServer((host, port), handler)
        except OSError as error:
            raise MendworkError(
                "could not start the chaos portal server", host=host, port=port
            ) from error
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": SHUTDOWN_POLL_SECONDS},
            name="chaos-portal",
            daemon=True,
        )

    @property
    def url(self) -> str:
        """The base URL, ending in a slash, with the port the operating system assigned."""
        return f"http://{self._host}:{self._server.server_port}/"

    def start(self) -> None:
        """Begin serving requests from a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop serving, close the listening socket, and wait for the thread to exit."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
