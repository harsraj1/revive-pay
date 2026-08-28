"""Run every UPI recovery pipeline stage in order, stopping on failure."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "src"
STAGES = (
    "generate_data.py",
    "detect.py",
    "decide.py",
    "escalate.py",
    "message.py",
    "act.py",
    "baseline.py",
    "find_failure_case.py",
)


def execute_stage(script_path: Path) -> int:
    """Execute one stage with the current Python interpreter."""

    # shell=False (the default) avoids command-string parsing, and using
    # sys.executable guarantees the same environment and installed packages.
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return completed.returncode


def run_pipeline(
    stages: Sequence[str] = STAGES,
    stage_executor: Callable[[Path], int] = execute_stage,
) -> int:
    """Run stages sequentially and return immediately after the first failure."""

    for position, stage_name in enumerate(stages, start=1):
        script_path = SOURCE_DIR / stage_name
        if not script_path.is_file():
            print(f"Stage {stage_name} failed: script does not exist", file=sys.stderr)
            return 1

        print(f"[{position}/{len(stages)}] Running {stage_name}...", flush=True)
        return_code = stage_executor(script_path)
        if return_code != 0:
            print(
                f"Stage {stage_name} failed with exit code {return_code}; pipeline stopped.",
                file=sys.stderr,
            )
            return return_code
        print(f"[{position}/{len(stages)}] Completed {stage_name}", flush=True)

    print("Pipeline completed successfully.", flush=True)
    return 0


def main() -> int:
    """Command-line entry point."""

    return run_pipeline()


if __name__ == "__main__":
    raise SystemExit(main())
