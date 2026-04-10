import pytest

from obsidian_agent.demo import DemoRunner, DemoStep, NavAction, decode_keypress
from obsidian_agent.models import RunResult


class FakeAgent:
    def __init__(self, run_results: list[RunResult], undo_results: list[RunResult]):
        self._run_results = run_results
        self._undo_results = undo_results
        self.run_calls: list[tuple[str, str | None]] = []
        self.undo_calls = 0

    async def run(self, instruction: str, current_file: str | None = None) -> RunResult:
        self.run_calls.append((instruction, current_file))
        return self._run_results.pop(0)

    async def undo(self) -> RunResult:
        self.undo_calls += 1
        return self._undo_results.pop(0)


def make_steps() -> list[DemoStep]:
    return [
        DemoStep("S1", "first instruction", "index.md", "check 1"),
        DemoStep("S2", "second instruction", "index.md", "check 2"),
    ]


def test_decode_keypress_controls() -> None:
    assert decode_keypress("\n") == NavAction.NEXT
    assert decode_keypress("\r") == NavAction.NEXT
    assert decode_keypress(" ") == NavAction.NEXT
    assert decode_keypress("\x7f") == NavAction.BACK
    assert decode_keypress("\x08") == NavAction.BACK
    assert decode_keypress("q") == NavAction.QUIT
    assert decode_keypress("Q") == NavAction.QUIT
    assert decode_keypress("x") == NavAction.INVALID


@pytest.mark.anyio
async def test_demo_runner_advances_only_on_success() -> None:
    agent = FakeAgent(
        run_results=[
            RunResult(ok=False, updated=False, summary="", error="failed"),
            RunResult(ok=True, updated=True, summary="ok"),
        ],
        undo_results=[],
    )
    runner = DemoRunner(agent, make_steps())

    first = await runner.apply_next()
    assert first is not None
    assert first.ok is False
    assert runner.completed_steps == 0

    second = await runner.apply_next()
    assert second is not None
    assert second.ok is True
    assert runner.completed_steps == 1


@pytest.mark.anyio
async def test_demo_runner_undo_moves_back_only_on_success() -> None:
    agent = FakeAgent(
        run_results=[
            RunResult(ok=True, updated=True, summary="step1"),
            RunResult(ok=True, updated=True, summary="step2"),
        ],
        undo_results=[
            RunResult(ok=False, updated=False, summary="", error="undo failed"),
            RunResult(ok=True, updated=True, summary="undone"),
        ],
    )
    runner = DemoRunner(agent, make_steps())

    await runner.apply_next()
    await runner.apply_next()
    assert runner.completed_steps == 2

    failed_undo = await runner.undo_last()
    assert failed_undo is not None
    assert failed_undo.ok is False
    assert runner.completed_steps == 2

    successful_undo = await runner.undo_last()
    assert successful_undo is not None
    assert successful_undo.ok is True
    assert runner.completed_steps == 1


@pytest.mark.anyio
async def test_demo_runner_handles_bounds() -> None:
    agent = FakeAgent(
        run_results=[RunResult(ok=True, updated=True, summary="step1")],
        undo_results=[RunResult(ok=True, updated=True, summary="undone")],
    )
    runner = DemoRunner(agent, [DemoStep("S1", "first instruction", None, "check")])

    assert await runner.undo_last() is None
    assert await runner.apply_next() is not None
    assert await runner.apply_next() is None
    assert await runner.undo_last() is not None
