"""Running one release of a real application in a container, for the real-app pair (ADR 0014).

Our own local instance of MIT-licensed software, pinned by image digest, seeded the same way every
time, and removed afterwards. Nothing here is part of the product: the benchmark starts the
application, Mendwork only ever sees a URL.
"""

import asyncio
import socket
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from typing import Final

import httpx

ADMIN_USER: Final = "bench"
# A throwaway credential for a container that exists for one benchmark run and is then removed.
ADMIN_PASSWORD: Final = "bench-demo-password"  # noqa: S105
ADMIN_EMAIL: Final = "bench@mendwork.test"
REPOSITORY: Final = "demo"
READY_TIMEOUT_S: Final = 180.0
DOCKER_TIMEOUT_S: Final = 900.0


class InstanceError(RuntimeError):
    """A release could not be started, seeded, or reached."""


@dataclass(frozen=True, slots=True)
class Release:
    """One release of an application, pinned by digest so it is the same image every time."""

    label: str
    image: str

    @property
    def tag(self) -> str:
        """The digest's short form, for provenance."""
        return self.image.split("@", 1)[-1][:19]


@dataclass(frozen=True, slots=True)
class Instance:
    """A running container and where to reach it."""

    release: Release
    container: str
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def free_port() -> int:
    """A port the operating system is not using now."""
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def docker(*arguments: str, timeout_s: float = DOCKER_TIMEOUT_S) -> str:
    """One docker command; its output, or an InstanceError carrying what it said."""
    process = await asyncio.create_subprocess_exec(
        "docker", *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        raise InstanceError(
            f"docker {' '.join(arguments)} did not finish in {timeout_s:.0f}s"
        ) from None
    if process.returncode != 0:
        raise InstanceError(
            f"docker {' '.join(arguments)} failed ({process.returncode}): "
            f"{err.decode(errors='replace').strip()[:400]}"
        )
    return out.decode(errors="replace").strip()


async def pull(release: Release) -> None:
    """Fetch the image, so a run never waits on the network in the middle."""
    await docker("pull", "--quiet", release.image)


async def start(release: Release, *, port: int | None = None) -> Instance:
    """Start the release on a free loopback port, with its installer locked and SQLite storage."""
    chosen = port or free_port()
    container = await docker(
        "run",
        "--detach",
        "--publish",
        f"127.0.0.1:{chosen}:3000",
        "--env",
        "GITEA__security__INSTALL_LOCK=true",
        "--env",
        f"GITEA__server__ROOT_URL=http://127.0.0.1:{chosen}/",
        "--env",
        "GITEA__database__DB_TYPE=sqlite3",
        "--env",
        "GITEA__database__PATH=/data/gitea/gitea.db",
        "--env",
        "GITEA__log__LEVEL=Warn",
        release.image,
    )
    return Instance(release, container[:12], chosen)


async def stop(instance: Instance) -> None:
    """Stop and remove the container, with its storage."""
    await docker("rm", "--force", "--volumes", instance.container, timeout_s=120.0)


async def wait_ready(instance: Instance, *, timeout_s: float = READY_TIMEOUT_S) -> None:
    """Wait until the application answers, or say what it last did."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    last = "no reply"
    async with httpx.AsyncClient(timeout=5.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                reply = await client.get(instance.base_url, follow_redirects=True)
            except httpx.HTTPError as error:
                last = type(error).__name__
            else:
                if reply.status_code == 200:
                    return
                last = f"HTTP {reply.status_code}"
            await asyncio.sleep(1.0)
    logs = await docker("logs", "--tail", "20", instance.container)
    raise InstanceError(
        f"{instance.release.label} was not ready in {timeout_s:.0f}s ({last}): {logs}"
    )


async def seed(instance: Instance) -> None:
    """Create the admin account and the repository the workflow uses."""
    await docker(
        "exec",
        "--user",
        "git",
        instance.container,
        "gitea",
        "admin",
        "user",
        "create",
        "--admin",
        "--username",
        ADMIN_USER,
        "--password",
        ADMIN_PASSWORD,
        "--email",
        ADMIN_EMAIL,
        "--must-change-password=false",
    )
    async with httpx.AsyncClient(
        base_url=instance.base_url, auth=(ADMIN_USER, ADMIN_PASSWORD), timeout=30.0
    ) as client:
        created = await client.post(
            "api/v1/user/repos",
            json={"name": REPOSITORY, "description": "Benchmark fixture", "auto_init": True},
        )
        if created.status_code not in (201, 409):
            raise InstanceError(f"could not create the repository: HTTP {created.status_code}")
        issue = await client.post(
            f"api/v1/repos/{ADMIN_USER}/{REPOSITORY}/issues",
            json={"title": "Sample issue for the benchmark", "body": "Created by the harness."},
        )
        if issue.status_code not in (201, 409):
            raise InstanceError(f"could not create the issue: HTTP {issue.status_code}")


@asynccontextmanager
async def running(release: Release) -> AsyncIterator[Instance]:
    """A started, seeded instance, removed when the block ends."""
    instance = await start(release)
    try:
        await wait_ready(instance)
        await seed(instance)
        yield instance
    finally:
        await stop(instance)


async def pull_all(releases: Sequence[Release]) -> None:
    """Fetch every image the pair needs."""
    for release in releases:
        await pull(release)
