"""Every page script ships in the wheel and loads from it, not only from the source tree.

The wheel is built with uv, then a separate interpreter imports the adapter package from
the wheel alone: ``-S`` skips site-packages (and the editable install's path to ``src``),
and PYTHONPATH names only the wheel, so importlib.resources can only read the scripts
from inside the archive.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
SCRIPTS: Final = REPO / "src" / "mendwork" / "adapters" / "browser_playwright" / "js"
PROBE: Final = """
import importlib.resources, json
import mendwork.adapters.browser_playwright as package
directory = importlib.resources.files(package) / "js"
print(json.dumps({
    "module": package.__file__,
    "scripts": {
        entry.name: entry.read_text(encoding="utf-8")
        for entry in directory.iterdir()
        if entry.name.endswith(".js")
    },
}))
"""


def test_every_page_script_loads_from_the_built_wheel(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv builds the wheel"
    built = subprocess.run(  # noqa: S603 - a fixed command line on the project's own tools
        [uv, "build", "--wheel", "--out-dir", str(tmp_path / "dist"), str(REPO)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-2000:]
    [wheel] = (tmp_path / "dist").glob("mendwork-*.whl")

    loaded = subprocess.run(  # noqa: S603 - a fixed command line on our own interpreter
        [sys.executable, "-S", "-c", PROBE],
        env={"PYTHONPATH": str(wheel)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert loaded.returncode == 0, loaded.stderr[-2000:]
    reply = json.loads(loaded.stdout)
    assert str(wheel) in reply["module"]
    expected = {path.name: path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.js")}
    assert "recorder.js" in expected
    assert reply["scripts"] == expected
