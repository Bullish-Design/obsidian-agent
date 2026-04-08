from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
import termios
import tty
from typing import Protocol

import httpx

from .agent import Agent
from .config import AgentConfig
from .models import RunResult


class NavAction(str, Enum):
    NEXT = "next"
    BACK = "back"
    QUIT = "quit"
    INVALID = "invalid"


@dataclass(frozen=True)
class DemoStep:
    title: str
    instruction: str
    current_file: str | None
    check_note: str


class AgentLike(Protocol):
    async def run(self, instruction: str, current_file: str | None = None) -> RunResult: ...

    async def undo(self) -> RunResult: ...


DEMO_STEPS: list[DemoStep] = [
    DemoStep(
        title="Add Live Demo Notes Section",
        instruction=(
            "In index.md, append a new section at the bottom with heading '## Live Demo Notes' "
            "and exactly these three bullet points:\n"
            "- Watching changes in Obsidian live\n"
            "- Triggered by terminal demo script\n"
            "- Can be undone from the terminal demo\n"
            "Do not modify any other lines."
        ),
        current_file="index.md",
        check_note="Open index.md in Obsidian and confirm the new section appears at the bottom.",
    ),
    DemoStep(
        title="Create Demo Walkthrough Note",
        instruction=(
            "Create demo-walkthrough.md with exactly this content:\n"
            "---\n"
            "demo: true\n"
            "source: obsidian-agent-demo\n"
            "---\n"
            "# Walkthrough Note\n"
            "\n"
            "This note was created by the live demo.\n"
            "- Enter/Space moves forward\n"
            "- Backspace undoes the last step\n"
        ),
        current_file="demo-walkthrough.md",
        check_note="Open demo-walkthrough.md in Obsidian and confirm the note and frontmatter were created.",
    ),
    DemoStep(
        title="Update Getting Started Bullets",
        instruction=(
            "In index.md under the '## Getting Started' heading, append one bullet line exactly:\n"
            "- Run `devenv shell -- python scripts/demo_walkthrough.py` for guided live edits.\n"
            "Keep all existing bullets and other content unchanged."
        ),
        current_file="index.md",
        check_note="In index.md, confirm the new demo bullet appears under Getting Started.",
    ),
]


class DemoRunner:
    def __init__(self, agent: AgentLike, steps: list[DemoStep]):
        self._agent = agent
        self._steps = steps
        self.next_step_index = 0

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    @property
    def completed_steps(self) -> int:
        return self.next_step_index

    def current_step(self) -> DemoStep | None:
        if self.next_step_index >= len(self._steps):
            return None
        return self._steps[self.next_step_index]

    async def apply_next(self) -> RunResult | None:
        step = self.current_step()
        if step is None:
            return None

        result = await self._agent.run(step.instruction, step.current_file)
        if result.ok:
            self.next_step_index += 1
        return result

    async def undo_last(self) -> RunResult | None:
        if self.next_step_index == 0:
            return None

        result = await self._agent.undo()
        if result.ok:
            self.next_step_index -= 1
        return result


def decode_keypress(char: str) -> NavAction:
    if char in {"\r", "\n", " "}:
        return NavAction.NEXT
    if char in {"\x08", "\x7f"}:
        return NavAction.BACK
    if char.lower() == "q":
        return NavAction.QUIT
    return NavAction.INVALID


def _read_single_keypress() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def read_navigation_action() -> NavAction:
    if not sys.stdin.isatty():
        typed = input("Action [Enter/Space=next, b=undo, q=quit]: ")
        if typed == "" or typed == " ":
            return NavAction.NEXT
        if typed.lower() == "b":
            return NavAction.BACK
        if typed.lower() == "q":
            return NavAction.QUIT
        return NavAction.INVALID

    print("Controls: [Enter/Space] next  [Backspace] undo last step  [q] quit")
    return decode_keypress(_read_single_keypress())


def _preflight_llm_base_url(base_url: str) -> None:
    response = httpx.get(f"{base_url}/models", timeout=10)
    response.raise_for_status()


def _print_status(runner: DemoRunner, vault_dir: Path) -> None:
    print()
    print("=" * 80)
    print("Obsidian Agent Live Demo")
    print(f"Vault: {vault_dir}")
    print(f"Progress: {runner.completed_steps}/{runner.total_steps} step(s) completed")
    step = runner.current_step()
    if step is None:
        print("All demo steps completed.")
        print("Use Backspace to undo and replay, or q to quit.")
    else:
        print(f"Next step: {step.title}")
        print(f"Check in Obsidian: {step.check_note}")
    print("=" * 80)


def _print_result(prefix: str, result: RunResult) -> None:
    print(f"{prefix} ok={result.ok} updated={result.updated}")
    if result.summary:
        print(f"summary: {result.summary}")
    if result.changed_files:
        print("changed files:")
        for path in result.changed_files:
            print(f"- {path}")
    if result.warning:
        print(f"warning: {result.warning}")
    if result.error:
        print(f"error: {result.error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive Obsidian demo walkthrough.")
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path("demo-vault"),
        help="Path to the demo vault to edit.",
    )
    parser.add_argument(
        "--base-url",
        default="http://remora-server:8000/v1",
        help="OpenAI-compatible vLLM base URL.",
    )
    parser.add_argument(
        "--model",
        default="unsloth/gemma-4-E4B",
        help="Model name to use from the OpenAI-compatible endpoint.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Max tokens per model response.",
    )
    parser.add_argument(
        "--operation-timeout",
        type=int,
        default=180,
        help="Per-step timeout in seconds.",
    )
    return parser


def run_demo(args: argparse.Namespace) -> int:
    vault_dir = args.vault_dir.resolve()
    if not vault_dir.exists() or not vault_dir.is_dir():
        print(f"Vault directory not found: {vault_dir}")
        return 1

    print("Checking model endpoint...")
    try:
        _preflight_llm_base_url(args.base_url.rstrip("/"))
    except Exception as exc:
        print(f"Failed to reach model endpoint: {exc}")
        return 1

    config = AgentConfig(
        vault_dir=vault_dir,
        llm_model=f"openai:{args.model}",
        llm_base_url=args.base_url,
        llm_max_tokens=args.max_tokens,
        operation_timeout=args.operation_timeout,
    )
    agent = Agent(config)
    runner = DemoRunner(agent, DEMO_STEPS)

    print()
    print("Open this vault in Obsidian and keep it visible while this demo runs:")
    print(vault_dir)

    while True:
        _print_status(runner, vault_dir)
        action = read_navigation_action()

        if action == NavAction.QUIT:
            print("Exiting demo.")
            return 0

        if action == NavAction.INVALID:
            print("Unknown key. Use Enter/Space, Backspace, or q.")
            continue

        if action == NavAction.NEXT:
            result = asyncio.run(runner.apply_next())
            if result is None:
                print("No remaining steps to apply.")
                continue
            _print_result("apply:", result)
            continue

        result = asyncio.run(runner.undo_last())
        if result is None:
            print("No step to undo yet.")
            continue
        _print_result("undo:", result)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_demo(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
