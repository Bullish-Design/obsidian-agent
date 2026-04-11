from __future__ import annotations

from typing import Protocol

from ..scope import EditScope
from .command import CommandProfile
from .forge_web import ForgeWebProfile


class InterfaceProfile(Protocol):
    id: str

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]: ...

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str: ...


INTERFACES: dict[str, InterfaceProfile] = {
    "command": CommandProfile(),
    "forge_web": ForgeWebProfile(),
}


def resolve_interface(interface_id: str) -> InterfaceProfile:
    profile = INTERFACES.get(interface_id)
    if profile is None:
        msg = f"unsupported interface_id: {interface_id}"
        raise ValueError(msg)
    return profile
