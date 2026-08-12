"""Bio script library — CRUD, availability gating, and planner catalog."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from octop.infra.bioinformatics.importer import ImportPlan
from octop.infra.db.repos.bio_script_folders import BioScriptFolderRepo, BioScriptFolderRow
from octop.infra.db.repos.bio_scripts import BioScriptRepo, BioScriptRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

_MD_CATALOG_LIMIT = 500


def script_available(row: BioScriptRow, *, now: int | None = None) -> bool:
    """A script is plannable only when active, verified, and inside its validity window."""
    if not row.is_active or not row.verified:
        return False
    ts = int(time.time()) if now is None else now
    if row.valid_from is not None and ts < row.valid_from:
        return False
    return not (row.valid_until is not None and ts > row.valid_until)


class BioScriptLibrary:
    def __init__(
        self,
        *,
        script_repo: BioScriptRepo,
        folder_repo: BioScriptFolderRepo,
        library_root: Path,
    ) -> None:
        self.scripts = script_repo
        self.folders = folder_repo
        self._root = library_root

    # ── folders ──────────────────────────────────────────────────────────

    def create_folder(
        self, *, name: str, parent_id: str = "", sort_order: int = 0
    ) -> BioScriptFolderRow:
        if parent_id and self.folders.get(parent_id) is None:
            raise OctopError(
                ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND, f"folder not found: {parent_id}"
            )
        return self.folders.create(
            id=new_ulid(), name=name.strip(), parent_id=parent_id, sort_order=sort_order
        )

    def delete_folder(self, folder_id: str) -> None:
        if self.folders.get(folder_id) is None:
            raise OctopError(
                ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND, f"folder not found: {folder_id}"
            )
        children = [f for f in self.folders.list_all() if f.parent_id == folder_id]
        scripts = self.scripts.list_all(folder_id=folder_id)
        if children or scripts:
            raise OctopError(
                ErrorCode.BIO_SCRIPT_FOLDER_NOT_EMPTY,
                f"folder {folder_id} has {len(children)} subfolders and {len(scripts)} scripts",
            )
        self.folders.delete(folder_id)

    # ── scripts ──────────────────────────────────────────────────────────

    def script_dir(self, script_id: str) -> Path:
        return self._root / script_id

    def create_script(
        self,
        *,
        name: str,
        folder_id: str = "",
        script_file: tuple[str, bytes] | None = None,
        md_file: tuple[str, bytes] | None = None,
        uploaded_by: int = 0,
        description: str = "",
        tool_id: str = "",
        category: str = "",
        version: str = "1.0.0",
        inputs: str = "[]",
        outputs: str = "[]",
        runtime_min: float = 0,
        cost: float = 0,
        weight: float = 0,
        valid_from: int | None = None,
        valid_until: int | None = None,
    ) -> BioScriptRow:
        if folder_id and self.folders.get(folder_id) is None:
            raise OctopError(
                ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND, f"folder not found: {folder_id}"
            )
        script_id = new_ulid()
        file_path = ""
        md_content = ""
        if script_file is not None:
            dest_dir = self.script_dir(script_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            filename = Path(script_file[0]).name
            (dest_dir / filename).write_bytes(script_file[1])
            file_path = filename
        if md_file is not None:
            md_content = md_file[1].decode("utf-8", errors="replace")
        return self.scripts.create(
            id=script_id,
            name=name.strip(),
            folder_id=folder_id,
            file_path=file_path,
            md_content=md_content,
            uploaded_by=uploaded_by,
            description=description,
            tool_id=tool_id,
            category=category,
            version=version,
            inputs=inputs,
            outputs=outputs,
            runtime_min=runtime_min,
            cost=cost,
            weight=weight,
            valid_from=valid_from,
            valid_until=valid_until,
        )

    def import_plans(self, plan: ImportPlan, *, uploaded_by: int = 0) -> dict[str, Any]:
        """Bulk-create scripts from a directory-upload plan.

        The target folder (plan.folder_name) is created when missing; a script
        whose name already exists in that folder is skipped, not overwritten.
        """
        folder_id = ""
        if plan.folder_name:
            existing = {f.name: f for f in self.folders.list_all() if not f.parent_id}
            folder = existing.get(plan.folder_name) or self.create_folder(name=plan.folder_name)
            folder_id = folder.id

        taken = {r.name for r in self.scripts.list_all(folder_id=folder_id)}
        created: list[str] = []
        skipped: list[dict[str, str]] = [
            {"path": p, "reason": "not a script/doc"} for p in plan.skipped
        ]
        for item in plan.scripts:
            meta = item.meta
            name = meta.name or Path(item.script[0]).stem
            if name in taken:
                skipped.append({"path": item.rel_path, "reason": f"name exists: {name}"})
                continue
            taken.add(name)
            self.create_script(
                name=name,
                folder_id=folder_id,
                script_file=item.script,
                md_file=item.md,
                uploaded_by=uploaded_by,
                description=meta.description,
                category=meta.category,
                version=meta.version or "1.0.0",
                inputs=meta.inputs,
                outputs=meta.outputs,
                runtime_min=meta.runtime_min or 0,
                cost=meta.cost or 0,
                valid_from=meta.valid_from,
                valid_until=meta.valid_until,
            )
            created.append(name)
        return {"folder_id": folder_id, "created": created, "skipped": skipped}

    def delete_script(self, script_id: str) -> None:
        row = self.scripts.get(script_id)
        if row is None:
            raise OctopError(ErrorCode.BIO_SCRIPT_NOT_FOUND, f"script not found: {script_id}")
        self.scripts.delete(script_id)
        shutil.rmtree(self.script_dir(script_id), ignore_errors=True)

    def require_script(self, script_id: str) -> BioScriptRow:
        row = self.scripts.get(script_id)
        if row is None:
            raise OctopError(ErrorCode.BIO_SCRIPT_NOT_FOUND, f"script not found: {script_id}")
        return row

    def require_available(self, script_id: str) -> BioScriptRow:
        row = self.require_script(script_id)
        if not script_available(row):
            raise OctopError(ErrorCode.BIO_SCRIPT_UNAVAILABLE, f"script unavailable: {script_id}")
        return row

    # ── planner catalog ──────────────────────────────────────────────────

    def available_scripts(self) -> list[BioScriptRow]:
        now = int(time.time())
        return [r for r in self.scripts.list_all() if script_available(r, now=now)]

    def catalog_for_planner(self) -> list[dict[str, object]]:
        """Compact script catalog handed to the planning agent as tool output."""
        catalog: list[dict[str, object]] = []
        for row in self.available_scripts():
            md = row.md_content
            if len(md) > _MD_CATALOG_LIMIT:
                md = md[:_MD_CATALOG_LIMIT] + "…"
            catalog.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "category": row.category,
                    "version": row.version,
                    "inputs": row.inputs,
                    "outputs": row.outputs,
                    "runtime_min": row.runtime_min,
                    "cost": row.cost,
                    "weight": row.weight,
                    "md_content": md,
                }
            )
        return catalog
