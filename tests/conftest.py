import pytest
from obsidian_ops import Vault
from pydantic_ai import models

from tests.support.vault_fs import VaultWorkspace, build_run_id, create_vault_workspace, finalize_vault_workspace

# Block all real LLM calls in tests.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def test_run_id() -> str:
    return build_run_id()


@pytest.fixture
def vault_workspace(request: pytest.FixtureRequest, test_run_id: str) -> VaultWorkspace:
    workspace = create_vault_workspace(
        fixture_name="basic",
        test_nodeid=request.node.nodeid,
        run_id=test_run_id,
    )
    yield workspace
    finalize_vault_workspace(workspace)


@pytest.fixture
def vault_workspace_factory(request: pytest.FixtureRequest, test_run_id: str):
    workspaces: list[VaultWorkspace] = []

    def factory(fixture_name: str, *, label: str | None = None) -> VaultWorkspace:
        nodeid = request.node.nodeid if label is None else f"{request.node.nodeid}::{label}"
        workspace = create_vault_workspace(
            fixture_name=fixture_name,
            test_nodeid=nodeid,
            run_id=test_run_id,
        )
        workspaces.append(workspace)
        return workspace

    yield factory

    for workspace in workspaces:
        finalize_vault_workspace(workspace)


@pytest.fixture
def vault(vault_workspace: VaultWorkspace) -> Vault:
    return Vault(str(vault_workspace.work_dir))
