#!/usr/bin/env python3
"""Run deterministic tests without hidden caches and retain output under runs/."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS = Path(os.environ.get("RUNS_DIR", ROOT / "runs"))


def main(argv: list[str] | None = None) -> int:
    ts = time.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS / f"offline_tests_{ts}"
    run_dir.mkdir(parents=True, exist_ok=False)
    output = run_dir / "pytest.txt"
    args = list(argv if argv is not None else sys.argv[1:])
    command = [
        sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider",
        *(args or ["tests/"]),
    ]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with output.open("w") as stream:
        proc = subprocess.run(
            command, cwd=ROOT, env=env, stdout=stream,
            stderr=subprocess.STDOUT, text=True, check=False)
    print(f"[tests] exit={proc.returncode} -> {output}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
