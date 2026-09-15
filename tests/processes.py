"""The processes a test starts: finding them, and waiting for them to exit without sleeping.

Waits are exit events from the operating system (pidfd on Linux, kqueue elsewhere), each bounded by
a timeout, so a test never guesses how long a process takes to go away.
"""

import asyncio
import os
import select
import sys
import time


async def descendant_commands(root: int) -> dict[int, str]:
    """Every process descended from ``root`` now, with its command line."""
    listing = await asyncio.create_subprocess_exec(
        "ps", "-A", "-o", "pid=,ppid=,command=", stdout=asyncio.subprocess.PIPE
    )
    output, _ = await listing.communicate()
    children: dict[int, set[int]] = {}
    commands: dict[int, str] = {}
    for row in output.decode().splitlines():
        fields = row.split(maxsplit=2)
        pid, parent = int(fields[0]), int(fields[1])
        children.setdefault(parent, set()).add(pid)
        commands[pid] = fields[2] if len(fields) > 2 else ""
    found: dict[int, str] = {}
    frontier = [root]
    while frontier:
        for child in children.get(frontier.pop(), set()):
            if child not in found:
                found[child] = commands[child]
                frontier.append(child)
    return found


async def descendants(root: int) -> set[int]:
    """Every process descended from ``root`` now: a Playwright driver, Chromium, its helpers."""
    return set(await descendant_commands(root))


async def playwright_drivers() -> set[int]:
    """The Playwright driver processes this test process started."""
    commands = await descendant_commands(os.getpid())
    return {pid for pid, command in commands.items() if "run-driver" in command}


if sys.platform == "linux":

    def still_running(pids: set[int], timeout_s: float) -> set[int]:
        """The processes that have not exited within the timeout, waited on as exit events."""
        handles: dict[int, int] = {}
        for pid in pids:
            try:
                handles[os.pidfd_open(pid)] = pid
            except ProcessLookupError:
                continue
        poller = select.poll()
        for handle in handles:
            poller.register(handle, select.POLLIN)
        deadline = time.monotonic() + timeout_s
        try:
            waiting = set(handles)
            while waiting and (remaining := deadline - time.monotonic()) > 0:
                for handle, _ in poller.poll(remaining * 1000):
                    poller.unregister(handle)
                    waiting.discard(handle)
            return {handles[handle] for handle in waiting}
        finally:
            for handle in handles:
                os.close(handle)

else:

    def still_running(pids: set[int], timeout_s: float) -> set[int]:
        """The processes that have not exited within the timeout, waited on as exit events."""
        queue = select.kqueue()
        try:
            waiting: set[int] = set()
            for pid in pids:
                event = select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
                try:
                    queue.control([event], 0, 0)
                except ProcessLookupError:
                    continue
                waiting.add(pid)
            deadline = time.monotonic() + timeout_s
            while waiting and (remaining := deadline - time.monotonic()) > 0:
                for fired in queue.control(None, len(waiting), remaining):
                    waiting.discard(fired.ident)
            return waiting
        finally:
            queue.close()
