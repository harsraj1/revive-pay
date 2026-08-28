"""Tests for orchestration ordering and fail-fast behavior."""

from pathlib import Path

from src.run_all import STAGES, run_pipeline


def test_orchestrator_runs_stages_in_required_order() -> None:
    visited: list[str] = []

    def successful_executor(path: Path) -> int:
        visited.append(path.name)
        return 0

    assert run_pipeline(stage_executor=successful_executor) == 0
    assert tuple(visited) == STAGES


def test_orchestrator_stops_immediately_after_failure() -> None:
    visited: list[str] = []

    def failing_executor(path: Path) -> int:
        visited.append(path.name)
        return 7 if path.name == "decide.py" else 0

    assert run_pipeline(stage_executor=failing_executor) == 7
    assert visited == ["generate_data.py", "detect.py", "decide.py"]
