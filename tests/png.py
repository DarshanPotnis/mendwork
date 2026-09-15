"""Small, valid PNG images for tests that embed or check screenshots."""

import struct
import zlib


def tiny_png(width: int = 4, height: int = 3, shade: int = 128) -> bytes:
    """A grey PNG of the given size, byte for byte the same every time."""
    raw = b"".join(b"\x00" + bytes([shade]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
