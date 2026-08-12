"""Unit tests for the bio planner/validation logic and tool scoping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.bioinformatics.library import BioScriptLibrary
from octop.infra.bioinformatics.models import CandidateChain, ToolRef
from octop.infra.bioinformatics.service import BioWorkflowService
from octop.infra.bioinformatics.stream import (
    ack_or_sentinel,
    extract_bio_custom,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def svc(tmp_path: Path) -> BioWorkflowService:
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
    return BioWorkflowService(task_repo=services.bio_task_repo, library=library)


def _add_available_script(svc: BioWorkflowService, script_id: str, *, inputs: str = "[]") -> None:
    svc.library.scripts.create(id=script_id, name=script_id, inputs=inputs)
    svc.library.scripts.set_verified(script_id, verified=True, verified_by=1)


def test_validate_candidates_ok(svc: BioWorkflowService) -> None:
    _add_available_script(svc, "s1")
    _add_available_script(svc, "s2")
    chains = [
        CandidateChain(
            id="c1", tool_chain=[ToolRef(id="s1", name="s1"), ToolRef(id="s2", name="s2")]
        )
    ]
    assert svc.validate_candidates(chains) == chains


def test_validate_candidates_rejects_empty(svc: BioWorkflowService) -> None:
    with pytest.raises(OctopError) as excinfo:
        svc.validate_candidates([])
    assert excinfo.value.code == ErrorCode.BIO_TASK_STATE_INVALID

    with pytest.raises(OctopError) as excinfo:
        svc.validate_candidates([CandidateChain(id="c1", tool_chain=[])])
    assert excinfo.value.code == ErrorCode.BIO_TASK_STATE_INVALID


def test_validate_candidates_rejects_unknown_or_unavailable(svc: BioWorkflowService) -> None:
    with pytest.raises(OctopError) as excinfo:
        svc.validate_candidates(
            [CandidateChain(id="c1", tool_chain=[ToolRef(id="nope", name="x")])]
        )
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_NOT_FOUND

    svc.library.scripts.create(id="draft", name="draft")  # unverified
    with pytest.raises(OctopError) as excinfo:
        svc.validate_candidates(
            [CandidateChain(id="c1", tool_chain=[ToolRef(id="draft", name="d")])]
        )
    assert excinfo.value.code == ErrorCode.BIO_SCRIPT_UNAVAILABLE


def test_present_candidates_marks_previously_used(svc: BioWorkflowService) -> None:
    _add_available_script(svc, "s1")
    _add_available_script(svc, "s2")
    task = svc.create_task(agent_id="a1", user_id=1, thread_id="th1", need_text="分析")
    chain = CandidateChain(id="c1", tool_chain=[ToolRef(id="s1", name="s1")])
    task = svc.present_candidates(task.id, [chain], agent_id="a1", user_id=1)
    candidates = json.loads(task.candidates_json)
    assert candidates[0]["previously_used"] is False

    task = svc.select_path(task.id, candidate_id="c1", candidate_data="")
    svc._transition(task, "generating")  # walk the machine to a second round
    svc.save_generated_code(task.id, code="print(1)", origin="library")

    # New task in another thread with the same chain -> previously_used
    task2 = svc.create_task(agent_id="a1", user_id=1, thread_id="th2", need_text="再来一次")
    chain2 = CandidateChain(id="c1", tool_chain=[ToolRef(id="s1", name="s1")])
    task2 = svc.present_candidates(task2.id, [chain2], agent_id="a1", user_id=1)
    assert json.loads(task2.candidates_json)[0]["previously_used"] is True


def test_select_path_derives_slots(svc: BioWorkflowService) -> None:
    _add_available_script(
        svc,
        "s1",
        inputs=json.dumps(
            [
                {"label": "R1 reads", "extensions": ["fastq", "fq"], "multiple": False},
                {"label": "R2 reads", "extensions": ["fastq", "fq"], "multiple": False},
            ]
        ),
    )
    _add_available_script(
        svc,
        "s2",
        inputs=json.dumps([{"label": "R1 reads", "extensions": ["fastq"], "multiple": False}]),
    )
    task = svc.create_task(agent_id="a1", user_id=1, thread_id="th1", need_text="x")
    chain = CandidateChain(
        id="c1", tool_chain=[ToolRef(id="s1", name="s1"), ToolRef(id="s2", name="s2")]
    )
    task = svc.present_candidates(task.id, [chain], agent_id="a1", user_id=1)
    task = svc.select_path(task.id, candidate_id="c1", candidate_data="")
    slots = json.loads(task.required_files_json)
    # merged by label, first occurrence wins
    assert [s["label"] for s in slots] == ["R1 reads", "R2 reads"]
    assert task.status == "uploading"


def test_single_active_task_per_thread(svc: BioWorkflowService) -> None:
    svc.create_task(agent_id="a1", user_id=1, thread_id="th1", need_text="x")
    with pytest.raises(OctopError) as excinfo:
        svc.create_task(agent_id="a1", user_id=1, thread_id="th1", need_text="y")
    assert excinfo.value.code == ErrorCode.BIO_TASK_ACTIVE_EXISTS


def test_sentinel_roundtrip() -> None:
    payload = {"kind": "workflow", "task_id": "t1", "candidates": []}
    text = ack_or_sentinel("已呈现", payload, emitted=False)
    chunk = {"type": "tool_result", "messages": [{"role": "tool", "content": text}]}
    extracted = extract_bio_custom(chunk)
    assert extracted == {"bio": payload}
    assert chunk["messages"][0]["content"] == "已呈现"

    # streamed path -> plain ack, nothing to extract
    plain = ack_or_sentinel("已呈现", payload, emitted=True)
    assert plain == "已呈现"
    chunk2 = {"type": "tool_result", "messages": [{"content": plain}]}
    assert extract_bio_custom(chunk2) is None


# ── tool scoping (bio tools only for the bioinformatics template) ──────────


@pytest.fixture
def manager(tmp_path: Path) -> AgentManager:
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    mgr = AgentManager(repos=services.repos, paths=services.paths)
    library = BioScriptLibrary(
        script_repo=services.bio_script_repo,
        folder_repo=services.bio_script_folder_repo,
        library_root=paths.bio_library_dir,
    )
    mgr.set_bio_service(BioWorkflowService(task_repo=services.bio_task_repo, library=library))
    return mgr


def _tool_names(cfg: object) -> set[str]:
    return {getattr(t, "name", "") for t in getattr(cfg, "tools", []) or []}


def test_bio_tools_scoped_to_template(manager: AgentManager) -> None:
    manager._repos.agent_repo.create(
        agent_id="BIO1", user_id=None, name="bio", template_name="bioinformatics"
    )
    manager._repos.agent_repo.create(agent_id="PLAIN1", user_id=None, name="plain")

    bio_row = manager.get_row("BIO1")
    plain_row = manager.get_row("PLAIN1")
    assert bio_row is not None and plain_row is not None

    bio_names = _tool_names(manager._build_harness_config(bio_row))
    plain_names = _tool_names(manager._build_harness_config(plain_row))

    assert "bio_plan_candidates" in bio_names
    assert "bio_present_candidates" in bio_names
    assert "bio_save_generated_code" in bio_names
    assert not any(n.startswith("bio_") for n in plain_names)
