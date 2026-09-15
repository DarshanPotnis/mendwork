"""Slow tests stay marked slow, so the fast suite keeps its time budget.

Slow means launching Chromium through the CLI, sweeping every heal pair, replaying the
examples against the portal in-process, running the heal fixture suite, recording in Chromium,
or proving the chaos portal's determinism. `make check` skips `slow`;
`make check-all` and CI run everything. A test that reaches a slow fixture from an unmarked
module, or a slow module that loses its marker, would quietly move into `make check`, so
this guard reads every test module and fails first.
"""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

TESTS: Final = Path(__file__).resolve().parents[1]
SLOW_FIXTURES: Final = frozenset({"cli_browser", "heal_pair_sweep"})
# Slow because of what the whole module does, not because of a fixture it uses (ADR 0007).
SLOW_MODULES: Final = frozenset(
    {
        "integration/test_replay_portal.py",
        "integration/test_heal_fixture_suite.py",
        "integration/test_chaos_determinism.py",
        "integration/test_recording_boundary.py",
        "integration/test_recording_interactions.py",
        "integration/test_recording_portal.py",
        "integration/test_recording_secrets.py",
        "integration/test_recording_targets.py",
        "integration/test_rung3_known_failures.py",
        "integration/test_cli_approval_browser.py",
        "integration/test_cli_interrupts.py",
        "integration/test_browser_shutdown.py",
    }
)


def _modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(TESTS.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_fixture(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
    return False


def _functions(tree: ast.Module) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    arguments = function.args
    return {
        argument.arg
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    }


def slow_fixture_closure() -> set[str]:
    """Every fixture that depends, directly or not, on a slow fixture."""
    dependencies: dict[str, set[str]] = {}
    for _, tree in _modules():
        for function in _functions(tree):
            if _is_fixture(function):
                dependencies.setdefault(function.name, set()).update(_parameters(function))
    slow = set(SLOW_FIXTURES)
    changed = True
    while changed:
        changed = False
        for name, needs in dependencies.items():
            if name not in slow and needs & slow:
                slow.add(name)
                changed = True
    return slow


def _marked_slow(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            return any(
                isinstance(mark, ast.Attribute) and mark.attr == "slow"
                for mark in ast.walk(node.value)
            )
    return False


def modules_using_slow_fixtures() -> dict[str, bool]:
    """Each test module that uses a slow fixture, and whether it is marked slow."""
    slow = slow_fixture_closure()
    found: dict[str, bool] = {}
    for path, tree in _modules():
        if not path.name.startswith("test_"):
            continue
        uses = any(
            function.name.startswith("test_") and _parameters(function) & slow
            for function in _functions(tree)
        )
        if uses:
            found[str(path.relative_to(TESTS))] = _marked_slow(tree)
    return found


def test_every_module_that_uses_a_slow_fixture_is_marked_slow() -> None:
    unmarked = [module for module, marked in modules_using_slow_fixtures().items() if not marked]

    assert unmarked == []


def test_modules_that_are_slow_as_a_whole_stay_marked() -> None:
    marked = {str(path.relative_to(TESTS)): _marked_slow(tree) for path, tree in _modules()}

    assert {module: marked.get(module) for module in sorted(SLOW_MODULES)} == dict.fromkeys(
        sorted(SLOW_MODULES), True
    )


def test_the_guard_really_finds_the_slow_modules() -> None:
    assert set(modules_using_slow_fixtures()) >= {
        "integration/test_heal_pair_sweep.py",
        "integration/test_cli_run_browser.py",
    }


def test_an_unmarked_module_would_be_caught() -> None:
    tree = ast.parse("pytestmark = [pytest.mark.browser]\ndef test_x(cli_browser): ...")

    assert not _marked_slow(tree)
    assert _marked_slow(ast.parse("pytestmark = [pytest.mark.browser, pytest.mark.slow]"))
