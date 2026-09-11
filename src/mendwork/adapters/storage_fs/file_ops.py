"""The file-system calls a durable publish is made of, behind one small seam.

The store takes these operations through its constructor, so tests can fail any single
call or pause between calls, which is how atomicity and concurrency are tested
deterministically against a real directory.
"""

import os
import tempfile
from pathlib import Path
from typing import Protocol


class FileOps(Protocol):
    """The individual operations of a no-overwrite atomic publish."""

    def create_temp(self, directory: Path, prefix: str) -> tuple[int, Path]:
        """Create and open a new, exclusively owned file in ``directory``."""
        ...

    def write_all(self, descriptor: int, data: bytes) -> None:
        """Write every byte, looping over partial writes."""
        ...

    def sync_file(self, descriptor: int) -> None:
        """Flush a file's contents to stable storage."""
        ...

    def close(self, descriptor: int) -> None:
        """Close a descriptor."""
        ...

    def link_exclusive(self, source: Path, destination: Path) -> None:
        """Give ``source`` a second name; raise FileExistsError rather than replace anything."""
        ...

    def remove(self, path: Path) -> None:
        """Remove one name of a file."""
        ...

    def sync_directory(self, directory: Path) -> None:
        """Flush a directory's entries, so a new name survives a power loss."""
        ...


class OsFileOps:
    """FileOps on the local POSIX file system."""

    def create_temp(self, directory: Path, prefix: str) -> tuple[int, Path]:
        # mkstemp opens with O_CREAT | O_EXCL and mode 0600.
        descriptor, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
        return descriptor, Path(name)

    def write_all(self, descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]

    def sync_file(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def close(self, descriptor: int) -> None:
        os.close(descriptor)

    def link_exclusive(self, source: Path, destination: Path) -> None:
        # link(2) is atomic and fails with EEXIST; unlike rename(2) it never replaces a file.
        os.link(source, destination)

    def remove(self, path: Path) -> None:
        path.unlink()

    def sync_directory(self, directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
