"""Searching for a secret in everything a run or a recording leaves behind.

The encodings are derived here, independently of the product's own scrubber, so a test
cannot pass merely because both sides share a blind spot.
"""

import base64
import html
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, quote_plus


def encodings(secret: str) -> list[bytes]:
    """The secret in every form this search looks for."""
    raw = secret.encode()
    forms = {
        raw,
        json.dumps(secret)[1:-1].encode(),
        json.dumps(secret, ensure_ascii=False)[1:-1].encode(),
        html.escape(secret).encode(),
        quote(secret, safe="").encode(),
        quote_plus(secret).encode(),
        secret.encode("utf-16-le"),
    }
    for offset in range(3):
        encoded = base64.b64encode(b"\x00" * offset + raw)
        forms.add(encoded[(offset * 8 + 5) // 6 : ((offset + len(raw)) * 8) // 6])
    return sorted(forms)


def leaks(data: bytes, secret: str) -> list[bytes]:
    """Every encoding of the secret found in the data."""
    return [form for form in encodings(secret) if form in data]


def every_file(directory: Path) -> Iterator[tuple[str, bytes]]:
    """Every file under a directory, and every member of every zip archive among them."""
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        yield str(path), data
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    yield f"{path}!{member}", archive.read(member)
