"""Run the complete recovery experiment pipeline in fail-fast order."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
PIPELINE_STAGES: tuple[tuple[str, Path], ...] = (
    ("generate", PROJECT_ROOT / "src" / "generate_data.py"),
    ("detect", PROJECT_ROOT / "src" / "detect.py"),
    ("decide", PROJECT_ROOT / "src" / "decide.py"),
    ("escalate", PROJECT_ROOT / "src" / "escalate.py"),
    ("message", PROJECT_ROOT / "src" / "message.py"),
    ("act", PROJECT_ROOT / "src" / "act.py"),
    ("baseline", PROJECT_ROOT / "src" / "baseline.py"),
    ("find_failure_case", PROJECT_ROOT / "find_failure_case.py"),
)


def run_all(
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Execute every stage, raising immediately when one stage fails."""

    for name, script_path in PIPELINE_STAGES:
        if not script_path.is_file():
            raise FileNotFoundError(f"Pipeline stage {name} is missing: {script_path}")
        print(f"\n=== Running {name} ===", flush=True)
        # check=True is the fail-fast contract: CalledProcessError propagates,
        # and the loop cannot proceed to any later stage.
        runner(
            [sys.executable, str(script_path)],
            cwd=PROJECT_ROOT,
            check=True,
        )


def main() -> None:
    run_all()
    print("\nAll pipeline stages completed successfully.", flush=True)


if __name__ == "__main__":
    main()
