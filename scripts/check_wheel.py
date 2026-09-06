"""Wheel build smoke check (review R8).

Usage: python scripts/check_wheel.py [dist_dir]

Verifies the built wheel contains every runtime package module, so the
``packages`` layout is importable when installed from the artifact — not
just from the source tree.
"""

from __future__ import annotations

import glob
import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "packages/__init__.py",
    "packages/core/events.py",
    "packages/core/provider.py",
    "packages/core/snapshots.py",
    "packages/core/stores.py",
    "packages/context/compaction.py",
    "packages/context/estimator.py",
    "packages/context/manager.py",
    "packages/evals/evaluator.py",
    "packages/experiments/compare.py",
    "packages/experiments/suite.py",
    "packages/observability/replay.py",
    "packages/observability/sqlite.py",
    "packages/runtime/loop.py",
    "packages/runtime/session.py",
    "packages/runtime/timeouts.py",
)


def main(dist_dir: str = "dist") -> int:
    wheels = sorted(glob.glob(str(Path(dist_dir) / "*.whl")))
    if not wheels:
        print(f"FAIL: no wheel found in {dist_dir}/")
        return 1
    wheel = wheels[-1]
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [required for required in REQUIRED if required not in names]
    if missing:
        print(f"FAIL: {wheel} is missing required modules: {missing}")
        return 1
    print(f"wheel ok: {wheel} ({len(names)} files, all {len(REQUIRED)} required modules present)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
