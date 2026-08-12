"""Unit tests for the local bio executor (cross-platform: python scripts)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.bioinformatics.executor import LocalShellExecutor, validate_code
from octop.infra.bioinformatics.library import BioScriptLibrary
from octop.infra.bioinformatics.service import BioWorkflowService
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def env(tmp_path: Path):
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    library = BioScriptLibrary(
        script_repo=services.bio_script_repo,
        folder_repo=services.bio_script_folder_repo,
        library_root=paths.bio_library_dir,
    )
    svc = BioWorkflowService(task_repo=services.bio_task_repo, library=library)
    pushed: list[tuple[str, str, str]] = []

    async def _push(agent_id: str, session_key: str, prompt: str) -> None:
        pushed.append((agent_id, session_key, prompt))

    svc.set_session_pusher(_push)
    executor = LocalShellExecutor(
        paths=paths,
        service=svc,
        thread_repo=services.repos.thread_repo,
        timeout_seconds=30,
    )
    svc.set_executor(executor)
    return svc, executor, services, pushed


def _seed_agent(services) -> None:
    """user/agent/thread rows needed by FK constraints on thread_repo.insert."""
    services.repos.user_repo.create(username="u1", password_hash="x", role="user")
    services.repos.agent_repo.create(agent_id="a1", user_id=1, name="bio")
    services.repos.thread_repo.insert(
        thread_id="th1", agent_id="a1", user_id=1, channel_type="dashboard", session_key="sk1"
    )


def _ready_task(svc, code: str, *, thread_id: str = "th1"):
    svc.tasks.create(id="task1", agent_id="a1", user_id=1, thread_id=thread_id, need_text="x")
    svc._transition(svc.require_task("task1"), "pathSelection", candidates_json="[]")
    svc._transition(svc.require_task("task1"), "uploading")
    svc._transition(svc.require_task("task1"), "generating")
    svc.save_generated_code("task1", code=code, origin="llm")
    return svc.require_task("task1")


def test_validate_code_rejects_dangerous() -> None:
    with pytest.raises(OctopError) as excinfo:
        validate_code("import os\nos.system('rm -rf / --no-preserve-root')")
    assert excinfo.value.code == ErrorCode.BIO_EXECUTION_UNSUPPORTED


def test_validate_code_rejects_bad_python() -> None:
    with pytest.raises(OctopError) as excinfo:
        validate_code("def broken(:\n")
    assert excinfo.value.code == ErrorCode.BIO_TASK_STATE_INVALID


def test_validate_code_bash_requires_posix() -> None:
    if sys.platform == "win32":
        with pytest.raises(OctopError) as excinfo:
            validate_code("#!/bin/bash\necho hi\n")
        assert excinfo.value.code == ErrorCode.BIO_EXECUTION_UNSUPPORTED
    else:
        validate_code("#!/bin/bash\necho hi\n")


async def _wait_done(svc, task_id: str, timeout: float = 20.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = svc.require_task(task_id)
        if row.status in ("completed", "failed"):
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("task did not finish in time")


@pytest.mark.asyncio
async def test_run_success_logs_and_push(env) -> None:
    svc, executor, services, pushed = env
    _seed_agent(services)
    task = _ready_task(svc, "print('hello bio')\n")
    await executor.start(task)
    done = await _wait_done(svc, "task1")
    assert done.status == "completed"
    assert done.exec_exit_code == 0

    logs, offset = await executor.read_logs(done, offset=0)
    assert "hello bio" in logs
    assert offset > 0
    # incremental read from the previous offset returns nothing new
    more, _ = await executor.read_logs(done, offset=offset)
    assert more == ""
    # completion pushed a follow-up turn into the chat session
    assert pushed and pushed[0][0] == "a1" and pushed[0][1] == "sk1"


@pytest.mark.asyncio
async def test_run_nonzero_exit_marks_failed(env) -> None:
    svc, executor, services, pushed = env
    _seed_agent(services)
    task = _ready_task(svc, "import sys; print('boom'); sys.exit(3)\n")
    await executor.start(task)
    done = await _wait_done(svc, "task1")
    assert done.status == "failed"
    assert done.exec_exit_code == 3
    logs, _ = await executor.read_logs(done, offset=0)
    assert "boom" in logs


@pytest.mark.asyncio
async def test_input_files_linked_into_run_dir(env, tmp_path: Path) -> None:
    svc, executor, services, _ = env
    _seed_agent(services)
    ws = services.paths.ensure_agent_workspace("a1")
    inbound = ws / "inbound"
    inbound.mkdir(parents=True, exist_ok=True)
    (inbound / "reads.fastq").write_text("@r1\nACGT\n", encoding="utf-8")
    task = _ready_task(
        svc,
        "from pathlib import Path; print(Path('inputs/reads.fastq').read_text().strip())\n",
    )
    svc.tasks.update(
        task.id,
        file_mappings_json='[{"slot_label":"reads","workspace_path":"inbound/reads.fastq","original_name":"reads.fastq"}]',
    )
    task = svc.require_task(task.id)
    await executor.start(task)
    done = await _wait_done(svc, "task1")
    assert done.status == "completed"
    logs, _ = await executor.read_logs(done, offset=0)
    assert "@r1" in logs


def test_recover_incomplete_marks_interrupted_failed(env) -> None:
    svc, executor, services, _ = env
    task = _ready_task(svc, "print(1)\n")
    # simulate a run orphaned by a server restart
    svc.start_execution(task.id, run_dir="bio_runs/task1")
    assert svc.require_task(task.id).exec_status == "running"
    recovered = executor.recover_incomplete()
    assert recovered == 1
    row = svc.require_task(task.id)
    assert row.status == "failed"
    assert "restart" in row.exec_error
