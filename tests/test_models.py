import pytest
from pydantic import ValidationError

from obsidian_agent.models import ApplyRequest, OperationResult, RunResult


def test_run_result_defaults() -> None:
    result = RunResult(ok=True, updated=False, summary="done")

    assert result.changed_files == []
    assert result.error is None
    assert result.warning is None


def test_operation_result_serialization() -> None:
    result = OperationResult(
        ok=True,
        updated=True,
        summary="updated",
        changed_files=["note.md"],
        error=None,
        warning=None,
    )

    dumped = result.model_dump()

    assert dumped == {
        "ok": True,
        "updated": True,
        "summary": "updated",
        "changed_files": ["note.md"],
        "error": None,
        "warning": None,
    }


def test_apply_request_validation_defaults() -> None:
    request = ApplyRequest(instruction="do stuff")

    assert request.instruction == "do stuff"
    assert request.current_file is None


def test_apply_request_with_current_file() -> None:
    request = ApplyRequest(instruction="do stuff", current_file="Projects/Alpha.md")

    assert request.instruction == "do stuff"
    assert request.current_file == "Projects/Alpha.md"


def test_apply_request_missing_instruction_defaults_to_none() -> None:
    request = ApplyRequest()

    assert request.instruction is None
    assert request.current_file is None
    assert request.interface_id is None
    assert request.scope is None
    assert request.intent is None
    assert request.allowed_write_scope == "target_only"


def test_apply_request_trims_current_file() -> None:
    request = ApplyRequest(instruction="do stuff", current_file="  Projects/Alpha.md  ")

    assert request.current_file == "Projects/Alpha.md"


def test_apply_request_with_interface_id() -> None:
    request = ApplyRequest(instruction="do stuff", interface_id=" command ")

    assert request.interface_id == "command"


def test_apply_request_with_scope() -> None:
    request = ApplyRequest(
        instruction="do stuff",
        scope={"kind": "heading", "path": "Projects/Alpha.md", "heading": "## Plan"},
    )

    assert request.scope is not None
    assert request.scope.kind == "heading"
    assert request.scope.path == "Projects/Alpha.md"


def test_apply_request_rejects_mismatched_scope_and_current_file() -> None:
    with pytest.raises(ValidationError):
        ApplyRequest(
            instruction="do stuff",
            current_file="Projects/Alpha.md",
            scope={"kind": "file", "path": "Projects/Beta.md"},
        )


def test_apply_request_accepts_matching_scope_and_current_file() -> None:
    request = ApplyRequest(
        instruction="do stuff",
        current_file="Projects/Alpha.md",
        scope={"kind": "file", "path": "Projects/Alpha.md"},
    )

    assert request.current_file == "Projects/Alpha.md"
    assert request.scope is not None
    assert request.scope.path == "Projects/Alpha.md"


@pytest.mark.parametrize(
    "current_file",
    [
        "",
        "   ",
        "/Projects/Alpha.md",
        "../Projects/Alpha.md",
        "Projects/../Alpha.md",
        "https://example.com/Projects/Alpha.md",
        "Projects\\Alpha.md",
    ],
)
def test_apply_request_rejects_invalid_current_file(current_file: str) -> None:
    with pytest.raises(ValidationError):
        ApplyRequest(instruction="do stuff", current_file=current_file)


@pytest.mark.parametrize("interface_id", ["", "   "])
def test_apply_request_rejects_invalid_interface_id(interface_id: str) -> None:
    with pytest.raises(ValidationError):
        ApplyRequest(instruction="do stuff", interface_id=interface_id)


def test_apply_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ApplyRequest(instruction="do stuff", current_url_path="/note")
