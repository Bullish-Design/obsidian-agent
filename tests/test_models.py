from obsidian_agent.models import ApplyRequest, OperationResult


def test_apply_request_minimal():
    req = ApplyRequest(instruction="Add a summary", current_url_path="/")
    assert req.instruction == "Add a summary"
    assert req.current_url_path == "/"
    assert req.current_file_path is None


def test_apply_request_with_file_path():
    req = ApplyRequest(
        instruction="Edit note",
        current_url_path="/notes/example",
        current_file_path="notes/example.md",
    )
    assert req.current_file_path == "notes/example.md"


def test_operation_result_defaults():
    result = OperationResult(ok=True, updated=False)
    assert result.ok is True
    assert result.updated is False
    assert result.summary == ""
    assert result.changed_files == []
    assert result.warning is None
    assert result.error is None


def test_operation_result_with_values():
    result = OperationResult(
        ok=True,
        updated=True,
        summary="Added section.",
        changed_files=["notes/example.md"],
        warning=None,
        error=None,
    )
    assert result.ok is True
    assert result.updated is True
    assert result.summary == "Added section."
    assert result.changed_files == ["notes/example.md"]
    assert result.warning is None
    assert result.error is None


def test_operation_result_serialization():
    result = OperationResult(
        ok=False,
        updated=False,
        summary="",
        changed_files=[],
        warning=None,
        error="Operation timed out after 120s",
    )
    data = result.model_dump()
    assert data["ok"] is False
    assert data["updated"] is False
    assert data["summary"] == ""
    assert data["changed_files"] == []
    assert data["warning"] is None
    assert data["error"] == "Operation timed out after 120s"


def test_operation_result_deserialization():
    data = {
        "ok": True,
        "updated": True,
        "summary": "Done",
        "changed_files": ["a.md", "b.md"],
        "warning": "Minor issue",
        "error": None,
    }
    result = OperationResult(**data)
    assert result.ok is True
    assert result.updated is True
    assert result.summary == "Done"
    assert result.changed_files == ["a.md", "b.md"]
    assert result.warning == "Minor issue"
    assert result.error is None
