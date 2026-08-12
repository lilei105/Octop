"""Built-in LangChain tools for the bioinformatics expert agent.

Wired via ``HarnessAgentConfig.tools`` (like the cron tools), injected only
for agents whose ``template_name`` is ``bioinformatics`` — see
``infra/agents/manager.py`` ``_build_harness_config``.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from octop.infra.bioinformatics.models import CandidateChain, FileSlot
from octop.infra.bioinformatics.service import BioWorkflowService
from octop.infra.bioinformatics.stream import ack_or_sentinel, emit_bio_chunk


def _tool_ctx() -> tuple[str, int, str, str]:
    """(agent_id, user_id, session_key, thread_id) from the running turn."""
    cfg = get_config().get("configurable") or {}
    agent_id = cfg.get("agent_id")
    user_raw = cfg.get("user")
    thread_id = cfg.get("thread_id")
    if not agent_id:
        raise ValueError("missing configurable.agent_id")
    if user_raw is None:
        raise ValueError("missing configurable.user")
    if not thread_id:
        raise ValueError("missing configurable.thread_id")
    session_key = str(cfg.get("session_key") or "")
    return str(agent_id), int(user_raw), session_key, str(thread_id)


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _err(exc: Exception) -> str:
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


_PLAN_DESC = (
    "Query the bioinformatics script library for planning an analysis. "
    "Returns the catalog of available scripts (id, name, description, category, "
    "input/output signatures, runtime, cost, Markdown docs). "
    "ALWAYS call this first when the user describes a bioinformatics analysis need. "
    "If status is 'no_match' or no returned script can satisfy the need, "
    "write a new script yourself instead of planning from the library."
)

_PRESENT_DESC = (
    "Present 2-4 candidate analysis paths to the user for selection. "
    "Call after bio_plan_candidates once you have composed candidate tool chains. "
    "candidates: JSON array string, each item "
    '{"id": "c1", "tool_chain": [{"id": "<script_id>", "name": "<name>"}]}. '
    "Every script id must come from the library catalog. "
    "The user picks one path in the UI; wait for their selection."
)

_REQUEST_FILES_DESC = (
    "Declare the input files the selected analysis path needs and ask the user "
    "to upload them. required_files: JSON array string of "
    '{"label": "R1 reads", "extensions": ["fastq", "fq", "fastq.gz"], "multiple": false}. '
    "Usually unnecessary — slots are derived automatically from the selected "
    "path's script metadata; use only to override."
)

_GET_TASK_DESC = (
    "Get the current bio analysis task for this conversation (status, selected "
    "path, required files, uploaded file mappings, generated code). "
    "Call to resume context after the user confirms a step."
)

_SAVE_CODE_DESC = (
    "Save the analysis code you generated (either assembled from library scripts "
    "or written from scratch) and present it to the user for approval. "
    "origin: 'library' when the code runs library scripts, 'llm' when you wrote "
    "new code. The user must explicitly approve before execution."
)


def build_bio_tools(service: BioWorkflowService) -> list[StructuredTool]:
    """Return bio workflow tools (not MCP — wired via HarnessAgentConfig.tools)."""
    svc = service

    async def bio_plan_candidates(need: str) -> str:
        try:
            _tool_ctx()
            catalog = svc.library.catalog_for_planner()
            if not catalog:
                return _ok(
                    {
                        "status": "no_match",
                        "scripts": [],
                        "hint": "script library is empty or has no available scripts; "
                        "write a new script yourself",
                    }
                )
            return _ok({"status": "ok", "scripts": catalog})
        except Exception as exc:
            return _err(exc)

    async def bio_present_candidates(need: str, candidates: str) -> str:
        try:
            agent_id, user_id, _sk, thread_id = _tool_ctx()
            try:
                raw = json.loads(candidates)
                chains = [CandidateChain.from_dict(c) for c in raw]
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                raise ValueError(f"invalid candidates JSON: {exc}") from exc
            task = svc.active_for_thread(agent_id=agent_id, thread_id=thread_id)
            if task is None:
                task = svc.create_task(
                    agent_id=agent_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    need_text=need,
                    title=need[:60],
                )
            task = svc.present_candidates(task.id, chains, agent_id=agent_id, user_id=user_id)
            payload = {
                "kind": "workflow",
                "task_id": task.id,
                "candidates": json.loads(task.candidates_json),
            }
            emitted = emit_bio_chunk(payload)
            return ack_or_sentinel(
                f"已向用户呈现 {len(json.loads(task.candidates_json))} 条候选分析路径，"
                "请等待用户在界面中选择。",
                payload,
                emitted=emitted,
            )
        except Exception as exc:
            return _err(exc)

    async def bio_request_files(task_id: str, required_files: str) -> str:
        try:
            _tool_ctx()
            try:
                raw = json.loads(required_files)
                slots = [FileSlot.from_dict(s) for s in raw]
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                raise ValueError(f"invalid required_files JSON: {exc}") from exc
            task = svc.request_files(task_id, slots)
            payload = {
                "kind": "file_request",
                "task_id": task.id,
                "required_files": json.loads(task.required_files_json),
            }
            emitted = emit_bio_chunk(payload)
            return ack_or_sentinel(
                "已请求用户上传输入文件，请等待上传完成。",
                payload,
                emitted=emitted,
            )
        except Exception as exc:
            return _err(exc)

    async def bio_get_task(task_id: str = "") -> str:
        try:
            agent_id, _uid, _sk, thread_id = _tool_ctx()
            task = (
                svc.get_task(task_id)
                if task_id
                else svc.active_for_thread(agent_id=agent_id, thread_id=thread_id)
            )
            if task is None:
                return _ok({"status": "none"})
            return _ok(
                {
                    "id": task.id,
                    "status": task.status,
                    "need_text": task.need_text,
                    "candidates": json.loads(task.candidates_json),
                    "selected_candidate": task.selected_candidate_json,
                    "required_files": json.loads(task.required_files_json),
                    "file_mappings": json.loads(task.file_mappings_json),
                    "code_origin": task.code_origin,
                    "has_code": bool(task.generated_code),
                    "exec_status": task.exec_status,
                    "exec_error": task.exec_error,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def bio_save_generated_code(task_id: str, code: str, origin: str) -> str:
        try:
            _tool_ctx()
            task = svc.save_generated_code(task_id, code=code, origin=origin)
            payload = {
                "kind": "code_preview",
                "task_id": task.id,
                "code_origin": task.code_origin,
            }
            emitted = emit_bio_chunk(payload)
            return ack_or_sentinel(
                "代码已生成并提交给用户审批，等待用户批准执行。",
                payload,
                emitted=emitted,
            )
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=bio_plan_candidates,
            name="bio_plan_candidates",
            description=_PLAN_DESC,
        ),
        StructuredTool.from_function(
            coroutine=bio_present_candidates,
            name="bio_present_candidates",
            description=_PRESENT_DESC,
        ),
        StructuredTool.from_function(
            coroutine=bio_request_files,
            name="bio_request_files",
            description=_REQUEST_FILES_DESC,
        ),
        StructuredTool.from_function(
            coroutine=bio_get_task,
            name="bio_get_task",
            description=_GET_TASK_DESC,
        ),
        StructuredTool.from_function(
            coroutine=bio_save_generated_code,
            name="bio_save_generated_code",
            description=_SAVE_CODE_DESC,
        ),
    ]


__all__ = ["build_bio_tools"]
