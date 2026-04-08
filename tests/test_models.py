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
