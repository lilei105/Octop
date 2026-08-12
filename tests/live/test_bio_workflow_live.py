"""Live end-to-end BioFlow test — real LLM drives the full workflow.

Chain: 2 verified scripts in the library → user states a need in chat →
agent plans candidates → user picks a path → uploads the input file →
agent writes analysis code → user approves → local execution → logs →
completion push.

Run::

    uv run pytest tests/live/test_bio_workflow_live.py -m live -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from octop.infra.agents.manager import AgentCreateSpec
from octop.infra.bioinformatics.executor import LocalShellExecutor
from octop.infra.bioinformatics.library import BioScriptLibrary
from octop.infra.bioinformatics.service import BioWorkflowService

if TYPE_CHECKING:
    from tests.live.conftest import LiveOpenAIConfig

    from octop.infra.agents.manager import AgentManager

pytestmark = pytest.mark.live

FASTQ = "@r1\nACGTACGT\n+\nFFFFFFFF\n@r2\nTTGGCCAA\n+\nFFFFFFFF\n"


def _request(text: str, *, agent_id: str, thread_id: str, user_id: int = 1) -> dict:
    return {
        "messages": [{"role": "user", "content": text}],
        "thread_id": thread_id,
        "user": str(user_id),
        "source": "live-test",
        "agent_id": agent_id,
        "configurable": {"session_key": f"sk-{thread_id}"},
    }


async def _wait_status(
    svc: BioWorkflowService, task_id: str, targets: set[str], timeout: float = 300.0
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = svc.require_task(task_id)
        if row.status in targets or row.status == "failed":
            return row
        await asyncio.sleep(1.0)
    raise AssertionError(f"task {task_id} did not reach {targets} in time")


@pytest.mark.asyncio
async def test_bio_workflow_end_to_end(
    live_agent_manager: AgentManager,
    live_openai_config: LiveOpenAIConfig,
    tmp_path: Path,
) -> None:
    manager = live_agent_manager
    services = manager._repos  # noqa: SLF001
    paths = manager._paths  # noqa: SLF001

    # ── wire the bio service + local executor into the manager ────────────
    library = BioScriptLibrary(
        script_repo=services.bio_script_repo,
        folder_repo=services.bio_script_folder_repo,
        library_root=paths.bio_library_dir,
    )
    svc = BioWorkflowService(task_repo=services.bio_task_repo, library=library)

    async def _push(agent_id: str, session_key: str, prompt: str) -> None:
        thread_id = session_key.removeprefix("sk-")
        await manager.call(
            agent_id,
            _request(prompt, agent_id=agent_id, thread_id=thread_id),
        )

    svc.set_session_pusher(_push)
    executor = LocalShellExecutor(
        paths=paths, service=svc, thread_repo=services.thread_repo, timeout_seconds=300
    )
    svc.set_executor(executor)
    manager.set_bio_service(svc)

    # ── seed two verified scripts ─────────────────────────────────────────
    library.scripts.create(
        id="qc",
        name="read counter",
        description="Count reads in a FASTQ file",
        inputs=json.dumps([{"label": "reads", "extensions": ["fastq"], "multiple": False}]),
        md_content="# read counter\nCounts the number of reads in a FASTQ file.",
    )
    library.scripts.create(
        id="gc",
        name="gc content",
        description="Compute GC content of sequences",
        inputs=json.dumps([{"label": "reads", "extensions": ["fastq"], "multiple": False}]),
        md_content="# gc content\nComputes the GC fraction of FASTQ sequences.",
    )
    for sid in ("qc", "gc"):
        library.scripts.set_verified(sid, verified=True, verified_by=1)

    # ── create the bioinformatics expert agent ────────────────────────────
    row = await manager.create(
        AgentCreateSpec(
            name="live-bio",
            default_model=live_openai_config.default_model,
            template_name="bioinformatics",
        ),
    )
    agent_id = row.agent_id
    thread_id = "bio-live-thread"
    services.thread_repo.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=1,
        channel_type="dashboard",
        session_key=f"sk-{thread_id}",
    )

    try:
        # ── 1. user states a need -> agent plans candidates ───────────────
        await manager.call(
            agent_id,
            _request(
                "我有一个 FASTQ 文件，想统计里面的 reads 数量。请帮我规划分析路径。",
                agent_id=agent_id,
                thread_id=thread_id,
            ),
        )
        task = svc.active_for_thread(agent_id=agent_id, thread_id=thread_id)
        assert task is not None, "agent did not create a bio task"
        assert task.status == "pathSelection", f"unexpected status {task.status}"
        candidates = json.loads(task.candidates_json)
        assert candidates, "no candidates presented"

        # ── 2. user picks a path (REST equivalent: service call) ─────────
        chain = candidates[0]
        task = svc.select_path(task.id, candidate_id=chain["id"], candidate_data="")
        assert task.status == "uploading"
        slots = json.loads(task.required_files_json)
        assert slots and slots[0]["label"] == "reads"

        # ── 3. user uploads the input file into the workspace ─────────────
        agent = manager.get_agent(agent_id)
        await agent.workspace.aupload_bytes("inbound/reads.fastq", FASTQ.encode())
        from octop.infra.bioinformatics.models import FileMapping  # noqa: PLC0415

        task = svc.confirm_upload(
            task.id,
            [
                FileMapping(
                    slot_label="reads",
                    workspace_path="inbound/reads.fastq",
                    original_name="reads.fastq",
                )
            ],
        )
        assert task.status == "generating"

        # ── 4. internal turn: agent writes the analysis code ──────────────
        await svc.poke_agent(
            agent_id=agent_id,
            session_key=f"sk-{thread_id}",
            prompt=(
                "用户已上传所有输入文件并确认。请根据已选路径生成一段 Python 分析代码"
                "（读取 inputs/reads.fastq，统计 reads 数量并 print 结果），"
                "然后调用 bio_save_generated_code 提交给用户审批。"
            ),
        )
        task = svc.require_task(task.id)
        assert task.status == "validating", f"expected validating, got {task.status}"
        assert task.generated_code.strip(), "agent submitted empty code"

        # ── 5. user approves -> local execution -> completion push ────────
        await executor.start(task)
        done = await _wait_status(svc, task.id, {"completed"}, timeout=300.0)
        assert done.status == "completed", f"run failed: {done.exec_error}"
        logs, _ = await executor.read_logs(done, offset=0)
        assert logs.strip(), "run.log is empty"
        # the FASTQ fixture has exactly 2 reads; the generated code must print it
        assert "2" in logs
    finally:
        await manager.delete(agent_id)
