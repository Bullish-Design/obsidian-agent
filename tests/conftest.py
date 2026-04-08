from pathlib import Path

import pytest
from obsidian_ops import Vault
from pydantic_ai import models

# Block all real LLM calls in tests.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    (vault_dir / "Projects").mkdir()
    (vault_dir / "Projects/Alpha.md").write_text("---\nstatus: draft\n---\n# Alpha\n")
    return Vault(str(vault_dir))
