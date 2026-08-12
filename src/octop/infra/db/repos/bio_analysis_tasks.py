"""Bio analysis workflow tasks — one row per analysis run (BioFlow integration)."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, optional_updates

ACTIVE_STATUSES = (
    "planning",
    "pathSelection",
    "uploading",
    "generating",
    "validating",
    "executing",
)

TERMINAL_STATUSES = ("completed", "failed")


@dataclass(frozen=True)
class BioTaskRow:
    id: str
    agent_id: str
    user_id: int
    thread_id: str
    title: str
    status: str
    need_text: str
    candidates_json: str
    selected_candidate_json: str
    required_files_json: str
    file_mappings_json: str
    generated_code: str
    code_origin: str
    run_dir: str
    exec_status: str
    exec_started_at: int | None
    exec_finished_at: int | None
    exec_exit_code: int | None
    exec_error: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, r: DbRow) -> BioTaskRow:
        return cls(
            id=r["id"],
            agent_id=r["agent_id"],
            user_id=int(r["user_id"]),
            thread_id=r["thread_id"],
            title=r["title"],
            status=r["status"],
            need_text=r["need_text"],
            candidates_json=r["candidates_json"],
            selected_candidate_json=r["selected_candidate_json"],
            required_files_json=r["required_files_json"],
            file_mappings_json=r["file_mappings_json"],
            generated_code=r["generated_code"],
            code_origin=r["code_origin"],
            run_dir=r["run_dir"],
            exec_status=r["exec_status"],
            exec_started_at=(
                int(r["exec_started_at"]) if r["exec_started_at"] is not None else None
            ),
            exec_finished_at=(
                int(r["exec_finished_at"]) if r["exec_finished_at"] is not None else None
            ),
            exec_exit_code=(int(r["exec_exit_code"]) if r["exec_exit_code"] is not None else None),
            exec_error=r["exec_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


class BioTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, task_id: str) -> BioTaskRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM bio_analysis_tasks WHERE id = ?", (task_id,)).fetchone()
        return BioTaskRow.from_row(r) if r else None

    def list_for_thread(self, *, agent_id: str, thread_id: str) -> list[BioTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bio_analysis_tasks"
                " WHERE agent_id = ? AND thread_id = ? ORDER BY created_at DESC",
                (agent_id, thread_id),
            ).fetchall()
        return map_rows(rows, BioTaskRow)

    def list_for_user(self, *, user_id: int, limit: int = 50) -> list[BioTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bio_analysis_tasks WHERE user_id = ? ORDER BY created_at DESC"
                " LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return map_rows(rows, BioTaskRow)

    def find_active_for_thread(self, *, agent_id: str, thread_id: str) -> BioTaskRow | None:
        placeholders = ", ".join("?" * len(ACTIVE_STATUSES))
        with self._db.connect() as conn:
            r = conn.execute(
                f"SELECT * FROM bio_analysis_tasks WHERE agent_id = ? AND thread_id = ?"
                f" AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                (agent_id, thread_id, *ACTIVE_STATUSES),
            ).fetchone()
        return BioTaskRow.from_row(r) if r else None

    def list_by_exec_status(self, exec_status: str) -> list[BioTaskRow]:
        """Rows whose subprocess was mid-flight (restart recovery scans these)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bio_analysis_tasks WHERE exec_status = ?",
                (exec_status,),
            ).fetchall()
        return map_rows(rows, BioTaskRow)

    def create(
        self,
        *,
        id: str,
        agent_id: str,
        user_id: int,
        thread_id: str,
        title: str = "",
        need_text: str = "",
    ) -> BioTaskRow:
        ts = str(now_ts())
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO bio_analysis_tasks("
                "id, agent_id, user_id, thread_id, title, status, need_text, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 'planning', ?, ?, ?)",
                (id, agent_id, user_id, thread_id, title, need_text, ts, ts),
            )
        row = self.get(id)
        if row is None:
            raise RuntimeError(f"bio task insert failed: {id}")
        return row

    def update(self, task_id: str, **changes: object) -> None:
        """Patch arbitrary columns; keys must be bio_analysis_tasks column names."""
        allowed = {
            "title",
            "status",
            "need_text",
            "candidates_json",
            "selected_candidate_json",
            "required_files_json",
            "file_mappings_json",
            "generated_code",
            "code_origin",
            "run_dir",
            "exec_status",
            "exec_started_at",
            "exec_finished_at",
            "exec_exit_code",
            "exec_error",
        }
        fields, params = optional_updates([(k, v) for k, v in changes.items() if k in allowed])
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(str(now_ts()))
        params.append(task_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE bio_analysis_tasks SET {', '.join(fields)} WHERE id = ?",
                params,
            )

    def delete(self, task_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM bio_analysis_tasks WHERE id = ?", (task_id,))
