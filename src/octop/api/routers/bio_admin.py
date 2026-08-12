"""Admin HTTP API for the bio script library (BioFlow integration)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel

from octop.api.deps import get_server, require_permission
from octop.infra.bioinformatics.library import BioScriptLibrary
from octop.infra.db.repos.bio_script_folders import BioScriptFolderRow
from octop.infra.db.repos.bio_scripts import BioScriptRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/bio")


def _library(server: OctopServer) -> BioScriptLibrary:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "services not initialized")
    return BioScriptLibrary(
        script_repo=server.services.bio_script_repo,
        folder_repo=server.services.bio_script_folder_repo,
        library_root=server.paths.bio_library_dir,
    )


def _script_payload(row: BioScriptRow) -> dict[str, Any]:
    return asdict(row)


def _folder_payload(row: BioScriptFolderRow) -> dict[str, Any]:
    return asdict(row)


class UpdateScriptBody(BaseModel):
    folder_id: str | None = None
    name: str | None = None
    description: str | None = None
    tool_id: str | None = None
    category: str | None = None
    version: str | None = None
    md_content: str | None = None
    inputs: str | None = None
    outputs: str | None = None
    runtime_min: float | None = None
    cost: float | None = None
    weight: float | None = None
    valid_from: int | None = None
    valid_until: int | None = None


class FolderBody(BaseModel):
    name: str
    parent_id: str = ""
    sort_order: int = 0


class UpdateFolderBody(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    sort_order: int | None = None


# ── scripts ──────────────────────────────────────────────────────────────


@router.get("/scripts", summary="List bio scripts")
async def list_scripts(
    folder_id: str | None = None,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> list[dict[str, Any]]:
    lib = _library(server)
    return [_script_payload(r) for r in lib.scripts.list_all(folder_id=folder_id)]


@router.get("/scripts/{script_id}", summary="Get a bio script")
async def get_script(
    script_id: str,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    return _script_payload(_library(server).require_script(script_id))


@router.post(
    "/scripts",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a bio script (script file + optional Markdown doc)",
)
async def create_script(
    name: str = Form(""),  # noqa: B008
    folder_id: str = Form(""),  # noqa: B008
    description: str = Form(""),  # noqa: B008
    tool_id: str = Form(""),  # noqa: B008
    category: str = Form(""),  # noqa: B008
    version: str = Form(""),  # noqa: B008
    inputs: str = Form(""),  # noqa: B008
    outputs: str = Form(""),  # noqa: B008
    runtime_min: float = Form(0),  # noqa: B008
    cost: float = Form(0),  # noqa: B008
    weight: float = Form(0),  # noqa: B008
    valid_from: int | None = Form(None),  # noqa: B008
    valid_until: int | None = Form(None),  # noqa: B008
    script_file: UploadFile = File(...),  # noqa: B008
    md_file: UploadFile | None = File(None),  # noqa: B008
    server: OctopServer = Depends(get_server),
    admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    lib = _library(server)
    script_part = (script_file.filename or "script.py", await script_file.read())
    md_part = None
    if md_file is not None and md_file.filename:
        md_part = (md_file.filename, await md_file.read())

    # The .md doc is the metadata source of truth: any form field left blank
    # falls back to what parse_script_md extracted (name → doc title → filename).
    from octop.infra.bioinformatics.mdmeta import parse_script_md  # noqa: PLC0415

    meta = parse_script_md(md_part[1].decode("utf-8", errors="replace")) if md_part else None

    def _pick(form_val: str, meta_val: str) -> str:
        return form_val.strip() or meta_val

    name_final = name.strip()
    if not name_final:
        name_final = (meta.name if meta else "") or Path(script_part[0]).stem

    row = lib.create_script(
        name=name_final,
        folder_id=folder_id,
        script_file=script_part,
        md_file=md_part,
        description=_pick(description, meta.description if meta else ""),
        tool_id=tool_id,
        category=_pick(category, meta.category if meta else ""),
        version=_pick(version, (meta.version if meta else "") or "1.0.0"),
        inputs=_pick(inputs, meta.inputs if meta else "[]"),
        outputs=_pick(outputs, meta.outputs if meta else "[]"),
        runtime_min=runtime_min or (meta.runtime_min if meta and meta.runtime_min else 0),
        cost=cost or (meta.cost if meta and meta.cost else 0),
        weight=weight,
        valid_from=valid_from if valid_from is not None else (meta.valid_from if meta else None),
        valid_until=valid_until
        if valid_until is not None
        else (meta.valid_until if meta else None),
        uploaded_by=admin.id,
    )
    return _script_payload(row)


@router.post("/scripts/parse-md", summary="Parse script metadata from a Markdown doc")
async def parse_md_metadata(
    md_file: UploadFile = File(...),  # noqa: B008
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    """Pre-fill helper for the upload form: extract name/version/slots from .md."""
    from dataclasses import asdict as _asdict  # noqa: PLC0415

    from octop.infra.bioinformatics.mdmeta import parse_script_md  # noqa: PLC0415

    content = (await md_file.read()).decode("utf-8", errors="replace")
    return _asdict(parse_script_md(content))


_IMPORT_MAX_FILES = 2000


@router.post(
    "/scripts/import",
    status_code=status.HTTP_201_CREATED,
    summary="Bulk-import a script-library directory (scripts/ + reference/ docs)",
)
async def import_scripts(
    files: list[UploadFile] = File(...),  # noqa: B008
    folder_name: str = Form(""),  # noqa: B008
    server: OctopServer = Depends(get_server),
    admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    """Directory upload: each file's filename carries its relative path
    (e.g. ``Amplicon/scripts/step3_dada2.sh``); plan_import pairs scripts with
    same-stem docs and extracts metadata."""
    from octop.infra.bioinformatics.importer import plan_import  # noqa: PLC0415

    if len(files) > _IMPORT_MAX_FILES:
        raise OctopError(ErrorCode.BIO_IMPORT_EMPTY, f"too many files: {len(files)}")
    payloads = [(f.filename or "", await f.read()) for f in files]
    plan = plan_import(payloads, folder_name.strip())
    if not plan.scripts:
        raise OctopError(ErrorCode.BIO_IMPORT_EMPTY, "no importable scripts in upload")
    return _library(server).import_plans(plan, uploaded_by=admin.id)


@router.put("/scripts/{script_id}", summary="Update bio script metadata")
async def update_script(
    script_id: str,
    body: UpdateScriptBody,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    lib = _library(server)
    lib.require_script(script_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    lib.scripts.update(script_id, **changes)
    return _script_payload(lib.require_script(script_id))


@router.patch("/scripts/{script_id}/verify", summary="Mark a bio script as verified")
async def verify_script(
    script_id: str,
    server: OctopServer = Depends(get_server),
    admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    lib = _library(server)
    lib.require_script(script_id)
    lib.scripts.set_verified(script_id, verified=True, verified_by=admin.id)
    return _script_payload(lib.require_script(script_id))


@router.patch("/scripts/{script_id}/toggle", summary="Enable or disable a bio script")
async def toggle_script(
    script_id: str,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    lib = _library(server)
    row = lib.require_script(script_id)
    lib.scripts.set_active(script_id, is_active=not row.is_active)
    return _script_payload(lib.require_script(script_id))


@router.delete(
    "/scripts/{script_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a bio script and its files",
)
async def delete_script(
    script_id: str,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> None:
    _library(server).delete_script(script_id)


# ── folders ──────────────────────────────────────────────────────────────


@router.get("/script-folders", summary="List bio script folders")
async def list_folders(
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> list[dict[str, Any]]:
    lib = _library(server)
    return [_folder_payload(r) for r in lib.folders.list_all()]


@router.post(
    "/script-folders",
    status_code=status.HTTP_201_CREATED,
    summary="Create a bio script folder",
)
async def create_folder(
    body: FolderBody,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    row = _library(server).create_folder(
        name=body.name, parent_id=body.parent_id, sort_order=body.sort_order
    )
    return _folder_payload(row)


@router.put("/script-folders/{folder_id}", summary="Update a bio script folder")
async def update_folder(
    folder_id: str,
    body: UpdateFolderBody,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> dict[str, Any]:
    lib = _library(server)
    if lib.folders.get(folder_id) is None:
        raise OctopError(ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND, f"folder not found: {folder_id}")
    lib.folders.update(
        folder_id, name=body.name, parent_id=body.parent_id, sort_order=body.sort_order
    )
    row = lib.folders.get(folder_id)
    if row is None:  # pragma: no cover - updated above
        raise OctopError(ErrorCode.BIO_SCRIPT_FOLDER_NOT_FOUND, f"folder not found: {folder_id}")
    return _folder_payload(row)


@router.delete(
    "/script-folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty bio script folder",
)
async def delete_folder(
    folder_id: str,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("bio_scripts")),
) -> None:
    _library(server).delete_folder(folder_id)
