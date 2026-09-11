"""Serve the chaos portal locally: ``python -m mendwork.apps.portal --root chaos-portal``."""

import argparse
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mendwork.apps.portal.server import PortalServer
from mendwork.observability import configure_logging
from mendwork.settings import Settings


def wait_for_interrupt() -> None:
    """Block until Ctrl+C, then return so the server shuts down cleanly."""
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        return


def main(
    argv: Sequence[str] | None = None,
    *,
    wait: Callable[[], None] = wait_for_interrupt,
    stdout: TextIO = sys.stdout,
) -> int:
    """Start the portal server, print its URL, and serve until ``wait`` returns."""
    parser = argparse.ArgumentParser(
        prog="python -m mendwork.apps.portal",
        description="Serve the chaos portal on the host and port from Settings.",
    )
    parser.add_argument("--root", type=Path, required=True, help="the chaos-portal directory")
    arguments = parser.parse_args(argv)

    settings = Settings()
    configure_logging(settings)
    with PortalServer(
        arguments.root, host=settings.portal_host, port=settings.portal_port
    ) as server:
        stdout.write(f"Chaos portal running at {server.url} (Ctrl+C to stop)\n")
        stdout.flush()
        wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
