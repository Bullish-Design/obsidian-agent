from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import filecmp
import json
import os
from pathlib import Path
import re
import shutil


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_VAULTS_DIR = REPO_ROOT / "tests" / "fixtures" / "vaults"
ARTIFACTS_DIR = REPO_ROOT / "tests" / "artifacts"


@dataclass(frozen=True)
class VaultWorkspace:
    fixture_name: str
    test_nodeid: str
    run_id: str
    workspace_root: Path
    before_dir: Path
    work_dir: Path
    after_dir: Path
    manifest_path: Path


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")


def build_run_id() -> str:
    from_env = os.environ.get("OBSIDIAN_AGENT_TEST_RUN_ID", "").strip()
    if from_env:
        return _safe_name(from_env)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"run-{timestamp}"


def create_vault_workspace(*, fixture_name: str, test_nodeid: str, run_id: str) -> VaultWorkspace:
    source_dir = FIXTURE_VAULTS_DIR / fixture_name
    if not source_dir.exists():
        msg = f"Fixture vault not found: {source_dir}"
        raise FileNotFoundError(msg)

    workspace_root = ARTIFACTS_DIR / run_id / _safe_name(test_nodeid)
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    before_dir = workspace_root / "before"
    work_dir = workspace_root / "work"
    after_dir = workspace_root / "after"
    manifest_path = workspace_root / "manifest.json"

    shutil.copytree(source_dir, before_dir)
    shutil.copytree(before_dir, work_dir)

    return VaultWorkspace(
        fixture_name=fixture_name,
        test_nodeid=test_nodeid,
        run_id=run_id,
        workspace_root=workspace_root,
        before_dir=before_dir,
        work_dir=work_dir,
        after_dir=after_dir,
        manifest_path=manifest_path,
    )


def _collect_changed_files(before_dir: Path, after_dir: Path) -> list[str]:
    changes: set[str] = set()
    comparison = filecmp.dircmp(before_dir, after_dir)

    for name in comparison.left_only:
        changes.add(name)
    for name in comparison.right_only:
        changes.add(name)
    for name in comparison.diff_files:
        changes.add(name)

    for child_name, child_comparison in comparison.subdirs.items():
        nested_changes = _collect_changed_files(before_dir / child_name, after_dir / child_name)
        for nested in nested_changes:
            changes.add(f"{child_name}/{nested}")

    return sorted(changes)


def finalize_vault_workspace(workspace: VaultWorkspace) -> None:
    if workspace.after_dir.exists():
        shutil.rmtree(workspace.after_dir)
    shutil.copytree(workspace.work_dir, workspace.after_dir)

    changed_files = _collect_changed_files(workspace.before_dir, workspace.after_dir)
    manifest = {
        "fixture_name": workspace.fixture_name,
        "test_nodeid": workspace.test_nodeid,
        "run_id": workspace.run_id,
        "before_dir": str(workspace.before_dir),
        "work_dir": str(workspace.work_dir),
        "after_dir": str(workspace.after_dir),
        "changed_files": changed_files,
    }
    workspace.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
