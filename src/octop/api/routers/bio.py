"""User-facing BioFlow confirmation flow — structured REST, not chat text.

The agent stream presents candidates / file slots / code previews as custom
chunks; the user's structured answers (pick a path, assign files, approve
execution) come back through these endpoints. State rules live in
``BioWorkflowService``; this router only does HTTP validation, ownership
checks, and triggering the follow-up internal turn.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from octop.api.common.agent import require_agent_row
from octop.api.common.workspace import require_running_workspace
from octop.api.deps import current_user, get_server
from octop.infra.bioinformatics.models import FileMapping
from octop.infra.bioinformatics.service import BioWorkflowService
from octop.infra.db.repos.bio_analysis_tasks import BioTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/bio")


def _service(server: OctopServer) -> BioWorkflowService:
    if server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server not ready")
    service: BioWorkflowService | None = server.app_runtime.agent_registry.bio_service
    if service is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "bio service not wired")
    return service


def _task_payload(row: BioTaskRow) -> dict[str, Any]:
    return asdict(row)


def _owned_task(
    server: OctopServer,
    agent_id: str,
    task_id: str,
    *,
    user: User,
    as_user: int | None,
) -> BioTaskRow:
    require_agent_row(agent_id, user=user, as_user=as_user, server=server)
    svc = _service(server)
    user_id = user.id if as_user is None else as_user
    return svc.require_owned_task(task_id, agent_id=agent_id, user_id=user_id)


def _assert_turn_idle(server: OctopServer, task: BioTaskRow) -> None:
    """R5: reject confirmations while a turn is streaming on this thread."""
    assert server.app_runtime is not None
    if server.app_runtime.gateway.ws_hub.is_turn_active(task.thread_id):
        raise OctopError(ErrorCode.AGENT_BUSY, "a turn is active on this thread")


def _poke(server: OctopServer, task: BioTaskRow, prompt: str) -> None:
    """Fire-and-forget internal turn so the agent continues the workflow."""
    assert server.app_runtime is not None
    svc = _service(server)
    thread = server.services.thread_repo.get(task.thread_id) if server.services else None
    if thread is None:
        logger.warning("bio poke skipped: thread %s not found", task.thread_id)
        return

    async def _run() -> None:
        try:
            await svc.poke_agent(
                agent_id=task.agent_id, session_key=thread.session_key, prompt=prompt
            )
        except Exception:
            logger.exception("bio poke failed for task %s", task.id)

    asyncio.get_running_loop().create_task(_run())


# ── queries ──────────────────────────────────────────────────────────────


@router.get("/tasks", summary="List bio analysis tasks for a chat thread")
async def list_tasks(
    agent_id: str,
    thread_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    require_agent_row(agent_id, user=user, as_user=as_user, server=server)
    svc = _service(server)
    rows = svc.list_for_thread(agent_id=agent_id, thread_id=thread_id)
    return {"tasks": [_task_payload(r) for r in rows]}


@router.get("/tasks/{task_id}", summary="Get one bio analysis task")
async def get_task(
    agent_id: str,
    task_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    row = _owned_task(server, agent_id, task_id, user=user, as_user=as_user)
    return {"task": _task_payload(row)}


# ── confirmations ────────────────────────────────────────────────────────


class ConfirmPathBody(BaseModel):
    candidate_id: str
    candidate_data: str = ""


@router.post("/tasks/{task_id}/confirm-path", summary="User picks one candidate path")
async def confirm_path(
    agent_id: str,
    task_id: str,
    body: ConfirmPathBody,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    task = _owned_task(server, agent_id, task_id, user=user, as_user=as_user)
    _assert_turn_idle(server, task)
    svc = _service(server)
    task = svc.select_path(
        task_id, candidate_id=body.candidate_id, candidate_data=body.candidate_data
    )
    return {"task": _task_payload(task)}


class FileMappingBody(BaseModel):
    slot_label: str
    workspace_path: str
    original_name: str


class ConfirmUploadBody(BaseModel):
    file_mappings: list[FileMappingBody]


@router.post("/tasks/{task_id}/confirm-upload", summary="User confirms slot file assignments")
async def confirm_upload(
    agent_id: str,
    task_id: str,
    body: ConfirmUploadBody,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    task = _owned_task(server, agent_id, task_id, user=user, as_user=as_user)
    _assert_turn_idle(server, task)
    ws = await require_running_workspace(agent_id, user=user, as_user=as_user, server=server)
    for m in body.file_mappings:
        if not await ws.aexists(m.workspace_path):
            raise OctopError(
                ErrorCode.BIO_FILE_SLOT_MISMATCH,
                f"uploaded file not found in workspace: {m.workspace_path!r}",
            )
    svc = _service(server)
    mappings = [
        FileMapping(
            slot_label=m.slot_label, workspace_path=m.workspace_path, original_name=m.original_name
        )
        for m in body.file_mappings
    ]
    task = svc.confirm_upload(task_id, mappings)
    _poke(
        server,
        task,
        "用户已完成所有输入文件的上传并确认。请继续分析工作流：根据已选路径生成或组装分析代码，"
        "然后调用 bio_save_generated_code 提交给用户审批。",
    )
    return {"task": _task_payload(task)}


@router.post("/tasks/{task_id}/start-execution", summary="User approves the code; run it")
async def start_execution(
    agent_id: str,
    task_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    task = _owned_task(server, agent_id, task_id, user=user, as_user=as_user)
    _assert_turn_idle(server, task)
    svc = _service(server)
    if svc.executor is None:
        raise OctopError(ErrorCode.BIO_EXECUTION_UNSUPPORTED, "executor not configured")
    task = await svc.executor.start(task)
    return {"task": _task_payload(task)}


# ── execution logs ───────────────────────────────────────────────────────


@router.get("/tasks/{task_id}/execution-logs", summary="Incremental execution log (poll)")
async def execution_logs(
    agent_id: str,
    task_id: str,
    offset: int = 0,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> dict[str, Any]:
    task = _owned_task(server, agent_id, task_id, user=user, as_user=as_user)
    svc = _service(server)
    logs = ""
    new_offset = offset
    if svc.executor is not None and task.run_dir:
        logs, new_offset = await svc.executor.read_logs(task, offset=offset)
    completed = task.status in ("completed", "failed")
    return {
        "logs": logs,
        "offset": new_offset,
        "completed": completed,
        "status": task.status,
        "exec_status": task.exec_status,
        "error_message": task.exec_error,
    }
