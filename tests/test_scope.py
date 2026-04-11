import pytest
from pydantic import TypeAdapter, ValidationError

from obsidian_agent.scope import BlockScope, EditScope, FileScope, HeadingScope, MultiScope, SelectionScope


EDIT_SCOPE_ADAPTER = TypeAdapter(EditScope)


def test_file_scope_valid() -> None:
    scope = FileScope(path="Projects/Alpha.md")

    assert scope.kind == "file"
    assert scope.path == "Projects/Alpha.md"


def test_heading_scope_valid() -> None:
    scope = HeadingScope(path="Projects/Alpha.md", heading="## Roadmap")

    assert scope.kind == "heading"
    assert scope.heading == "## Roadmap"


def test_block_scope_valid() -> None:
    scope = BlockScope(path="Projects/Alpha.md", block_id="my-block")

    assert scope.kind == "block"
    assert scope.block_id == "my-block"


def test_selection_scope_valid() -> None:
    scope = SelectionScope(path="Projects/Alpha.md", text="line", line_start=2, line_end=4)

    assert scope.kind == "selection"
    assert scope.line_start == 2
    assert scope.line_end == 4


def test_selection_scope_rejects_invalid_line_range() -> None:
    with pytest.raises(ValidationError):
        SelectionScope(path="Projects/Alpha.md", text="line", line_start=4, line_end=2)


def test_multi_scope_rejects_mixed_paths() -> None:
    with pytest.raises(ValidationError):
        MultiScope(
            path="Projects/Alpha.md",
            scopes=[
                HeadingScope(path="Projects/Alpha.md", heading="## A"),
                BlockScope(path="Projects/Beta.md", block_id="b"),
            ],
        )


def test_edit_scope_discriminator_parses_heading_scope() -> None:
    parsed = EDIT_SCOPE_ADAPTER.validate_python(
        {
            "kind": "heading",
            "path": "Projects/Alpha.md",
            "heading": "## Roadmap",
        }
    )

    assert isinstance(parsed, HeadingScope)


def test_edit_scope_discriminator_parses_block_scope() -> None:
    parsed = EDIT_SCOPE_ADAPTER.validate_python(
        {
            "kind": "block",
            "path": "Projects/Alpha.md",
            "block_id": "my-block",
        }
    )

    assert isinstance(parsed, BlockScope)
