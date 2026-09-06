"""Package layout smoke (review R8): every module imports cleanly."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import packages

PACKAGES_ROOT = Path(packages.__file__).resolve().parent
#: apps is the application layer (API + web) and is importable from source;
#: the wheel ships only ``packages`` (see pyproject hatch config).
IMPORT_ROOTS = ("packages", "apps")


def iter_module_names() -> list[str]:
    names: list[str] = []
    for root in IMPORT_ROOTS:
        base = PACKAGES_ROOT.parent / root
        for info in pkgutil.walk_packages([str(base)], prefix=f"{root}."):
            names.append(info.name)
    return sorted(names)


def test_every_module_in_the_layout_imports() -> None:
    names = iter_module_names()
    assert len(names) >= 25  # the documented layout has this many modules
    failures: dict[str, str] = {}
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - the smoke reports every failure
            failures[name] = f"{type(exc).__name__}: {exc}"
    assert not failures, failures


def test_public_packages_expose_expected_top_level_names() -> None:
    import apps  # noqa: F401
    import packages.core
    import packages.observability
    import packages.runtime

    assert packages.core.AgentEvent is not None
    assert packages.runtime.AgentLoop is not None
    assert packages.observability.SessionReplayer is not None
