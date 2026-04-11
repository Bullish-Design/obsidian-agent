import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError

from obsidian_agent.agent import Agent
from obsidian_agent.app import create_app
from obsidian_agent.config import AgentConfig
from tests.support.vault_fs import VaultWorkspace


@pytest.fixture
def vault_workspace(vault_workspace_factory) -> VaultWorkspace:
    return vault_workspace_factory("app")


@pytest.fixture
def client(vault_workspace: VaultWorkspace, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    vault = Vault(str(vault_workspace.work_dir))
    config = AgentConfig(
        vault_dir=vault_workspace.work_dir,
        site_base_url="http://localhost:8080",
        flat_urls=False,
    )
    agent = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    monkeypatch.setattr(vault, "commit", commit_noop)

    app = create_app(agent)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_get_file_by_path(client: TestClient) -> None:
    response = client.get("/api/vault/files", params={"path": "note.md"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["path"] == "note.md"
    assert payload["url"] == "http://localhost:8080/note/"
    assert payload["content"].startswith("# Test")
    assert payload["sha256"] == _sha256(payload["content"])
    assert payload["modified_at"]


def test_get_file_by_url(client: TestClient) -> None:
    response = client.get("/api/vault/files", params={"url": "/note/"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "note.md"
    assert payload["url"] == "http://localhost:8080/note/"


def test_get_file_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/api/vault/files", params={"path": "missing.md"})

    assert response.status_code == 404


def test_put_file_happy_path(client: TestClient) -> None:
    initial = client.get("/api/vault/files", params={"path": "note.md"})
    assert initial.status_code == 200
    current_sha = initial.json()["sha256"]

    new_content = "# Test\nUpdated via vault route.\n"
    response = client.put(
        "/api/vault/files",
        json={"path": "note.md", "content": new_content, "expected_sha256": current_sha},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "note.md"
    assert payload["sha256"] == _sha256(new_content)

    after = client.get("/api/vault/files", params={"path": "note.md"})
    assert after.status_code == 200
    assert after.json()["content"] == new_content


def test_put_file_stale_hash_returns_409(client: TestClient) -> None:
    initial = client.get("/api/vault/files", params={"path": "note.md"})
    assert initial.status_code == 200
    old_sha = initial.json()["sha256"]

    mutate = client.put("/api/vault/files", json={"path": "note.md", "content": "# Test\nintermediate\n"})
    assert mutate.status_code == 200

    stale = client.put(
        "/api/vault/files",
        json={"path": "note.md", "content": "# Test\nstale write\n", "expected_sha256": old_sha},
    )

    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_write"
    assert detail["path"] == "note.md"
    assert detail["expected_sha256"] == old_sha
    assert detail["current_sha256"] is not None


def test_put_file_rejects_path_and_url_together(client: TestClient) -> None:
    response = client.put(
        "/api/vault/files",
        json={"path": "note.md", "url": "/note/", "content": "x"},
    )

    assert response.status_code == 400
    assert "exactly one" in response.json()["detail"]


def test_put_file_rejects_neither_path_nor_url(client: TestClient) -> None:
    response = client.put("/api/vault/files", json={"content": "x"})

    assert response.status_code == 400
    assert "exactly one" in response.json()["detail"]


def test_vault_undo_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class UndoResult:
        warning = None

    def undo_last_change() -> UndoResult:
        return UndoResult()

    monkeypatch.setattr(client.app.state.vault, "undo_last_change", undo_last_change)

    response = client.post("/api/vault/undo")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["updated"] is True
    assert payload["summary"] == "Last change undone."
    assert payload["warning"] is None


def test_vault_undo_busy_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def undo_last_change() -> None:
        raise VaultBusyError("vault is busy elsewhere")

    monkeypatch.setattr(client.app.state.vault, "undo_last_change", undo_last_change)

    response = client.post("/api/vault/undo")

    assert response.status_code == 409
    assert response.json()["detail"] == "vault is busy elsewhere"


def test_get_file_structure_returns_501_when_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.app.state.vault, "list_structure", None, raising=False)

    response = client.get("/api/vault/files/structure", params={"path": "note.md"})

    assert response.status_code == 501
    assert "list_structure" in response.json()["detail"]


def test_get_file_structure_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def list_structure(path: str):
        assert path == "note.md"
        return SimpleNamespace(
            sha256="abcd1234",
            headings=[SimpleNamespace(text="# Test", level=1, line_start=1, line_end=5)],
            blocks=[SimpleNamespace(block_id="my-block", line_start=4, line_end=4)],
        )

    monkeypatch.setattr(client.app.state.vault, "list_structure", list_structure, raising=False)

    response = client.get("/api/vault/files/structure", params={"path": "note.md"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["path"] == "note.md"
    assert payload["sha256"] == "abcd1234"
    assert payload["headings"][0]["text"] == "# Test"
    assert payload["blocks"][0]["block_id"] == "my-block"


def test_ensure_anchor_returns_501_when_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.app.state.vault, "ensure_block_id", None, raising=False)

    response = client.post(
        "/api/vault/files/anchors",
        json={"path": "note.md", "line_start": 2, "line_end": 3},
    )

    assert response.status_code == 501
    assert "ensure_block_id" in response.json()["detail"]


def test_ensure_anchor_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def ensure_block_id(path: str, line_start: int, line_end: int):
        assert path == "note.md"
        assert line_start == 2
        assert line_end == 3
        return SimpleNamespace(block_id="anchored-1", sha256="ffff")

    monkeypatch.setattr(client.app.state.vault, "ensure_block_id", ensure_block_id, raising=False)

    response = client.post(
        "/api/vault/files/anchors",
        json={"path": "note.md", "line_start": 2, "line_end": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["path"] == "note.md"
    assert payload["block_id"] == "anchored-1"
    assert payload["sha256"] == "ffff"


def test_ensure_anchor_rejects_invalid_line_range(client: TestClient) -> None:
    response = client.post(
        "/api/vault/files/anchors",
        json={"path": "note.md", "line_start": 4, "line_end": 3},
    )

    assert response.status_code == 400
    assert "line_end must be >= line_start" in response.json()["detail"]


def _write_template(vault_root: Path, name: str, body: str) -> None:
    template_dir = vault_root / ".forge" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / name).write_text(body, encoding="utf-8")


def test_list_page_templates_happy_path(client: TestClient) -> None:
    _write_template(
        Path(client.app.state.vault.root),
        "project.yaml",
        (
            "id: project\n"
            "label: Project Page\n"
            "path: Projects/{{ slug(title) }}.md\n"
            "body: |\n"
            "  # {{ title }}\n"
            "fields:\n"
            "  - name: title\n"
            "    label: Title\n"
            "    required: true\n"
        ),
    )

    response = client.get("/api/vault/pages/templates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert len(payload["templates"]) == 1
    assert payload["templates"][0]["key"] == "project"
    assert payload["templates"][0]["label"] == "Project Page"
    assert payload["templates"][0]["fields"][0]["name"] == "title"


def test_list_page_templates_returns_501_when_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.app.state.vault, "list_templates", None, raising=False)

    response = client.get("/api/vault/pages/templates")

    assert response.status_code == 501
    assert "list_templates" in response.json()["detail"]


def test_create_page_from_template_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_template(
        Path(client.app.state.vault.root),
        "project.yaml",
        (
            "id: project\n"
            "label: Project Page\n"
            "path: Projects/{{ slug(title) }}.md\n"
            "body: |\n"
            "  # {{ title }}\n"
            "  status: draft\n"
            "fields:\n"
            "  - name: title\n"
            "    label: Title\n"
            "    required: true\n"
        ),
    )

    class FakeJJ:
        def describe(self, message: str) -> None:
            _ = message

        def new(self) -> None:
            return None

    monkeypatch.setattr(client.app.state.vault, "_get_jj", lambda: FakeJJ())

    response = client.post(
        "/api/vault/pages",
        json={"template_id": "project", "fields": {"title": "Alpha Launch"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["template_id"] == "project"
    assert payload["path"] == "Projects/alpha-launch.md"
    assert payload["url"] == "http://localhost:8080/Projects/alpha-launch/"
    assert payload["sha256"]

    created_path = Path(client.app.state.vault.root) / payload["path"]
    assert created_path.exists()
    assert "# Alpha Launch" in created_path.read_text(encoding="utf-8")


def test_create_page_from_template_conflict_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_template(
        Path(client.app.state.vault.root),
        "project.yaml",
        (
            "id: project\n"
            "label: Project Page\n"
            "path: Projects/{{ slug(title) }}.md\n"
            "body: |\n"
            "  # {{ title }}\n"
            "fields:\n"
            "  - name: title\n"
            "    label: Title\n"
            "    required: true\n"
        ),
    )

    class FakeJJ:
        def describe(self, message: str) -> None:
            _ = message

        def new(self) -> None:
            return None

    monkeypatch.setattr(client.app.state.vault, "_get_jj", lambda: FakeJJ())

    first = client.post("/api/vault/pages", json={"template_id": "project", "fields": {"title": "Alpha"}})
    assert first.status_code == 200

    second = client.post("/api/vault/pages", json={"template_id": "project", "fields": {"title": "Alpha"}})
    assert second.status_code == 409


def test_create_page_from_template_returns_501_when_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client.app.state.vault, "create_from_template", None, raising=False)

    response = client.post("/api/vault/pages", json={"template_id": "project", "fields": {"title": "Alpha"}})

    assert response.status_code == 501
    assert "create_from_template" in response.json()["detail"]
