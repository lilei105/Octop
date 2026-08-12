"""CRUD round-trip tests for the BioFlow repos."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.bio_analysis_tasks import BioTaskRepo
from octop.infra.db.repos.bio_script_folders import BioScriptFolderRepo
from octop.infra.db.repos.bio_scripts import BioScriptRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def test_script_folder_crud(db: SqlitePool) -> None:
    repo = BioScriptFolderRepo(db)
    root = repo.create(id="f1", name="扩增子", sort_order=1)
    child = repo.create(id="f2", name="16S", parent_id="f1")
    assert root.parent_id == ""
    assert child.parent_id == "f1"

    repo.update("f2", name="16S rRNA", sort_order=2)
    updated = repo.get("f2")
    assert updated is not None
    assert updated.name == "16S rRNA"
    assert updated.sort_order == 2

    assert {f.id for f in repo.list_all()} == {"f1", "f2"}
    repo.delete("f2")
    assert repo.get("f2") is None


def test_script_crud_and_flags(db: SqlitePool) -> None:
    repo = BioScriptRepo(db)
    row = repo.create(
        id="s1",
        name="fastp 质控",
        folder_id="f1",
        category="qc",
        weight=10,
        valid_from=100,
        valid_until=200,
        uploaded_by=1,
    )
    assert row.verified is False
    assert row.is_active is True
    assert row.valid_from == 100
    assert row.valid_until == 200

    repo.set_verified("s1", verified=True, verified_by=2)
    got = repo.get("s1")
    assert got is not None
    assert got.verified is True
    assert got.verified_by == 2

    repo.set_active("s1", is_active=False)
    assert repo.get("s1").is_active is False  # type: ignore[union-attr]

    repo.update("s1", description="去接头+质控", runtime_min=15.5, md_content="# fastp")
    got = repo.get("s1")
    assert got is not None
    assert got.description == "去接头+质控"
    assert got.runtime_min == 15.5

    repo.create(id="s2", name="QIIME2 去噪", folder_id="f1")
    assert [s.id for s in repo.list_all(folder_id="f1")] == ["s1", "s2"]

    repo.delete("s2")
    assert {s.id for s in repo.list_all()} == {"s1"}


def test_task_crud_and_active_lookup(db: SqlitePool) -> None:
    repo = BioTaskRepo(db)
    task = repo.create(
        id="t1",
        agent_id="agent1",
        user_id=1,
        thread_id="th1",
        title="16S 分析",
        need_text="帮我分析这批 16S 数据",
    )
    assert task.status == "planning"

    active = repo.find_active_for_thread(agent_id="agent1", thread_id="th1")
    assert active is not None
    assert active.id == "t1"

    repo.update(
        "t1",
        status="executing",
        generated_code="print('hi')",
        code_origin="llm",
        exec_status="running",
        exec_started_at=111,
    )
    got = repo.get("t1")
    assert got is not None
    assert got.status == "executing"
    assert got.code_origin == "llm"
    assert got.exec_started_at == 111

    repo.update("t1", status="completed", exec_status="done", exec_exit_code=0)
    assert repo.find_active_for_thread(agent_id="agent1", thread_id="th1") is None

    repo.create(id="t2", agent_id="agent1", user_id=1, thread_id="th1")
    # created_at is second-resolution — t1/t2 may share a timestamp; assert membership.
    assert {t.id for t in repo.list_for_thread(agent_id="agent1", thread_id="th1")} == {"t1", "t2"}
    assert len(repo.list_for_user(user_id=1)) == 2

    repo.delete("t1")
    assert repo.get("t1") is None
