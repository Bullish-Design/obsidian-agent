from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .web_paths import normalize_vault_path


class FileScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file"] = "file"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)


class HeadingScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading"] = "heading"
    path: str
    heading: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)


class BlockScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["block"] = "block"
    path: str
    block_id: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)

    @field_validator("block_id")
    @classmethod
    def validate_block_id(cls, value: str) -> str:
        block_id = value.strip()
        if not block_id:
            msg = "block_id must be non-empty"
            raise ValueError(msg)
        return block_id


class SelectionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["selection"] = "selection"
    path: str
    text: str
    line_start: int
    line_end: int
    context_before: str | None = None
    context_after: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)

    @model_validator(mode="after")
    def validate_lines(self) -> "SelectionScope":
        if self.line_start < 1:
            msg = "line_start must be >= 1"
            raise ValueError(msg)
        if self.line_end < self.line_start:
            msg = "line_end must be >= line_start"
            raise ValueError(msg)
        return self


class MultiScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["multi"] = "multi"
    path: str
    scopes: list[HeadingScope | BlockScope | SelectionScope]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)

    @model_validator(mode="after")
    def validate_nested_paths(self) -> "MultiScope":
        for scope in self.scopes:
            if scope.path != self.path:
                msg = "all nested scopes must target the same path as multi.path"
                raise ValueError(msg)
        return self


EditScope = Annotated[
    FileScope | HeadingScope | BlockScope | SelectionScope | MultiScope,
    Field(discriminator="kind"),
]
