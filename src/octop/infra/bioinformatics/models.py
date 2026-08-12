"""Domain models for the BioFlow integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FileSlot:
    """One required input slot declared by a planned analysis path."""

    label: str
    extensions: list[str] = field(default_factory=list)
    multiple: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "extensions": self.extensions, "multiple": self.multiple}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileSlot:
        exts = d.get("extensions")
        return cls(
            label=str(d.get("label", "")),
            extensions=[str(e).lower() for e in exts] if isinstance(exts, list) else [],
            multiple=bool(d.get("multiple", False)),
        )


@dataclass(frozen=True)
class FileMapping:
    """User-confirmed assignment of an uploaded workspace file to a slot."""

    slot_label: str
    workspace_path: str
    original_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_label": self.slot_label,
            "workspace_path": self.workspace_path,
            "original_name": self.original_name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileMapping:
        return cls(
            slot_label=str(d.get("slot_label", "")),
            workspace_path=str(d.get("workspace_path", "")),
            original_name=str(d.get("original_name", "")),
        )


@dataclass(frozen=True)
class ToolRef:
    id: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolRef:
        return cls(id=str(d.get("id", "")), name=str(d.get("name", "")))


@dataclass(frozen=True)
class CandidateChain:
    """One candidate analysis path presented to the user."""

    id: str
    tool_chain: list[ToolRef]
    previously_used: bool = False
    total_runtime: float = 0
    total_cost: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_chain": [t.to_dict() for t in self.tool_chain],
            "previously_used": self.previously_used,
            "total_runtime": self.total_runtime,
            "total_cost": self.total_cost,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CandidateChain:
        chain = d.get("tool_chain")
        return cls(
            id=str(d.get("id", "")),
            tool_chain=[ToolRef.from_dict(t) for t in chain] if isinstance(chain, list) else [],
            previously_used=bool(d.get("previously_used", False)),
            total_runtime=float(d.get("total_runtime", 0) or 0),
            total_cost=float(d.get("total_cost", 0) or 0),
        )


def slots_to_json(slots: list[FileSlot]) -> str:
    return json.dumps([s.to_dict() for s in slots], ensure_ascii=False)


def slots_from_json(raw: str) -> list[FileSlot]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [FileSlot.from_dict(d) for d in data if isinstance(d, dict)]


def mappings_to_json(mappings: list[FileMapping]) -> str:
    return json.dumps([m.to_dict() for m in mappings], ensure_ascii=False)


def mappings_from_json(raw: str) -> list[FileMapping]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [FileMapping.from_dict(d) for d in data if isinstance(d, dict)]


def candidates_to_json(candidates: list[CandidateChain]) -> str:
    return json.dumps([c.to_dict() for c in candidates], ensure_ascii=False)


def candidates_from_json(raw: str) -> list[CandidateChain]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [CandidateChain.from_dict(d) for d in data if isinstance(d, dict)]


def candidate_to_json(candidate: CandidateChain) -> str:
    return json.dumps(candidate.to_dict(), ensure_ascii=False)


def candidate_from_json(raw: str) -> CandidateChain | None:
    try:
        data = json.loads(raw or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return CandidateChain.from_dict(data)
