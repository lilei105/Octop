"""tests/integration/test_bio_api.py — user-facing bio confirmation flow endpoints."""

from __future__ import annotations

import json
from typing import Any

from octop.infra.bioinformatics.models import CandidateChain, ToolRef
from tests.support.auth import create_agent, create_user


async def _make_task(srv: Any, agent_id: str, *, thread_id: str = "th-bio-1") -> Any:
    """Verified script + task at pathSelection status for ``agent_id``."""
    svc = srv.app_runtime.agent_registry.bio_service
    # admin user id = 1 (bootstrap_admin); thread row for session_key lookup
    srv.services.thread_repo.insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=1,
        channel_type="dashboard",
        session_key=f"sk-{thread_id}",
    )
    svc.library.scripts.create(
        id="s1",
        name="fastp",
        inputs='[{"label":"reads","extensions":["fastq"],"multiple":false}]',
    )
    svc.library.scripts.set_verified("s1", verified=True, verified_by=1)
    task = svc.create_task(agent_id=agent_id, user_id=1, thread_id=thread_id, need_text="质控")
    chain = CandidateChain(id="c1", tool_chain=[ToolRef(id="s1", name="fastp")])
    svc.present_candidates(task.id, [chain], agent_id=agent_id, user_id=1)
    return svc.require_task(task.id)


async def test_confirm_full_flow(env_with_agent: Any) -> None:
    c, srv, auth, agent_id = env_with_agent
    task = await _make_task(srv, agent_id)

    # list by thread
    r = await c.get(
        f"/api/agents/{agent_id}/bio/tasks", headers=auth, params={"thread_id": "th-bio-1"}
    )
    assert r.status_code == 200
    assert [t["id"] for t in r.json()["tasks"]] == [task.id]

    # confirm path -> uploading with derived slots
    r = await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task.id}/confirm-path",
        headers=auth,
        json={"candidate_id": "c1"},
    )
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["status"] == "uploading"
    slots = json.loads(task["required_files_json"])
    assert [s["label"] for s in slots] == ["reads"]

    # upload a bio file into the workspace
    r = await c.post(
        f"/api/agents/{agent_id}/upload-bio",
        headers=auth,
        files={"file": ("sample.fastq", b"@r1\nACGT\n+\n!!!!\n", "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    ws_path = r.json()["workspace_path"]
    assert ws_path.startswith("inbound/")

    # confirm upload -> generating (fires internal turn; harness is faked)
    r = await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task['id']}/confirm-upload",
        headers=auth,
        json={
            "file_mappings": [
                {"slot_label": "reads", "workspace_path": ws_path, "original_name": "sample.fastq"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "generating"

    # execution logs endpoint works pre-execution (empty)
    r = await c.get(f"/api/agents/{agent_id}/bio/tasks/{task['id']}/execution-logs", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is False
    assert body["status"] == "generating"


async def test_start_execution_end_to_end(env_with_agent: Any) -> None:
    c, srv, auth, agent_id = env_with_agent
    task = await _make_task(srv, agent_id)
    svc = srv.app_runtime.agent_registry.bio_service
    await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task.id}/confirm-path",
        headers=auth,
        json={"candidate_id": "c1"},
    )
    # agent submits code for approval (tool path, exercised directly here)
    svc._transition(svc.require_task(task.id), "generating")  # post-upload state
    task = svc.save_generated_code(task.id, code="print('integration ok')\n", origin="llm")
    assert task.status == "validating"

    r = await c.post(f"/api/agents/{agent_id}/bio/tasks/{task.id}/start-execution", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "executing"

    # poll logs until the local subprocess completes
    import asyncio

    for _ in range(100):
        r = await c.get(f"/api/agents/{agent_id}/bio/tasks/{task.id}/execution-logs", headers=auth)
        body = r.json()
        if body["completed"]:
            break
        await asyncio.sleep(0.1)
    assert body["completed"] is True
    assert body["status"] == "completed"
    assert "integration ok" in body["logs"]


async def test_confirm_path_bad_candidate(env_with_agent: Any) -> None:
    c, srv, auth, agent_id = env_with_agent
    task = await _make_task(srv, agent_id)
    r = await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task.id}/confirm-path",
        headers=auth,
        json={"candidate_id": "nope"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BIO_TASK_STATE_INVALID"


async def test_confirm_upload_slot_mismatch(env_with_agent: Any) -> None:
    c, srv, auth, agent_id = env_with_agent
    task = await _make_task(srv, agent_id)
    await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task.id}/confirm-path",
        headers=auth,
        json={"candidate_id": "c1"},
    )
    r = await c.post(
        f"/api/agents/{agent_id}/upload-bio",
        headers=auth,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 200, r.text
    ws_path = r.json()["workspace_path"]
    r = await c.post(
        f"/api/agents/{agent_id}/bio/tasks/{task.id}/confirm-upload",
        headers=auth,
        json={
            "file_mappings": [
                {"slot_label": "reads", "workspace_path": ws_path, "original_name": "notes.txt"}
            ]
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BIO_FILE_SLOT_MISMATCH"


async def test_task_ownership_enforced(env_with_agent: Any) -> None:
    c, srv, auth, agent_id = env_with_agent
    task = await _make_task(srv, agent_id)
    await create_user(c, auth, username="eve")
    tok = (await c.post("/api/auth/login", json={"username": "eve", "password": "pw"})).json()[
        "access_token"
    ]
    eve_auth = {"Authorization": f"Bearer {tok}"}

    # eve can't see the admin's agent at all
    r = await c.get(f"/api/agents/{agent_id}/bio/tasks/{task.id}", headers=eve_auth)
    assert r.status_code == 403

    # admin hitting another agent's task gets 404 (no cross-agent task leak)
    other_agent = await create_agent(c, auth, name="other")
    r = await c.get(f"/api/agents/{other_agent}/bio/tasks/{task.id}", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BIO_TASK_NOT_FOUND"


async def test_upload_bio_rejects_bad_extension(env_with_agent: Any) -> None:
    c, _srv, auth, agent_id = env_with_agent
    r = await c.post(
        f"/api/agents/{agent_id}/upload-bio",
        headers=auth,
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400
