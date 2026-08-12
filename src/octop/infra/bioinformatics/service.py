"""Bio analysis workflow service — the single driver of task state transitions.

Both the agent-facing tools (``tools.py``) and the user-facing REST router
(``api/routers/bio.py``) go through this service; state rules live here only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from octop.infra.bioinformatics.library import BioScriptLibrary, script_available
from octop.infra.bioinformatics.models import (
    CandidateChain,
    FileMapping,
    FileSlot,
    candidate_from_json,
    candidate_to_json,
    candidates_from_json,
    candidates_to_json,
    mappings_to_json,
    slots_from_json,
    slots_to_json,
)
from octop.infra.db.repos.bio_analysis_tasks import BioTaskRepo, BioTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

# status -> allowed next statuses (service-enforced transitions)
_TRANSITIONS: dict[str, set[str]] = {
    "planning": {"pathSelection", "failed"},
    "pathSelection": {"uploading", "generating", "failed"},
    "uploading": {"generating", "failed"},
    "generating": {"validating", "failed"},
    "validating": {"executing", "generating", "failed"},
    "executing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


class BioWorkflowService:
    def __init__(self, *, task_repo: BioTaskRepo, library: BioScriptLibrary) -> None:
        self.tasks = task_repo
        self.library = library
        # Set by infra/server.py once the gateway exists; used to push
        # "continue the workflow" internal turns back into the chat session.
        self._push: Callable[[str, str, str], Awaitable[None]] | None = None
        # Set by infra/server.py in Phase 5 (LocalShellExecutor); the
        # BioExecutor Protocol seam keeps a future qsub backend pluggable.
        self._executor: Any | None = None

    @property
    def executor(self) -> Any | None:
        return self._executor

    def set_executor(self, executor: Any) -> None:
        """Attach the execution backend (BioExecutor protocol)."""
        self._executor = executor

    def set_session_pusher(self, push: Callable[[str, str, str], Awaitable[None]]) -> None:
        """push(agent_id, session_key, prompt) — schedule an internal agent turn."""
        self._push = push

    # ── lookups ──────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> BioTaskRow | None:
        return self.tasks.get(task_id)

    def require_task(self, task_id: str) -> BioTaskRow:
        row = self.tasks.get(task_id)
        if row is None:
            raise OctopError(ErrorCode.BIO_TASK_NOT_FOUND, f"task not found: {task_id}")
        return row

    def require_owned_task(self, task_id: str, *, agent_id: str, user_id: int) -> BioTaskRow:
        row = self.require_task(task_id)
        if row.agent_id != agent_id or row.user_id != user_id:
            raise OctopError(ErrorCode.BIO_TASK_NOT_FOUND, f"task not found: {task_id}")
        return row

    def list_for_thread(self, *, agent_id: str, thread_id: str) -> list[BioTaskRow]:
        return self.tasks.list_for_thread(agent_id=agent_id, thread_id=thread_id)

    def active_for_thread(self, *, agent_id: str, thread_id: str) -> BioTaskRow | None:
        return self.tasks.find_active_for_thread(agent_id=agent_id, thread_id=thread_id)

    # ── transitions ──────────────────────────────────────────────────────

    def _transition(self, task: BioTaskRow, target: str, **fields: object) -> BioTaskRow:
        allowed = _TRANSITIONS.get(task.status, set())
        if target not in allowed:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"cannot move task {task.id} from {task.status} to {target}",
            )
        self.tasks.update(task.id, status=target, **fields)
        return self.require_task(task.id)

    # ── workflow steps ───────────────────────────────────────────────────

    def create_task(
        self,
        *,
        agent_id: str,
        user_id: int,
        thread_id: str,
        need_text: str,
        title: str = "",
    ) -> BioTaskRow:
        existing = self.active_for_thread(agent_id=agent_id, thread_id=thread_id)
        if existing is not None:
            raise OctopError(
                ErrorCode.BIO_TASK_ACTIVE_EXISTS,
                f"thread {thread_id} already has active task {existing.id}",
                details={"task_id": existing.id},
            )
        return self.tasks.create(
            id=new_ulid(),
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            title=title,
            need_text=need_text,
        )

    def validate_candidates(self, candidates: list[CandidateChain]) -> list[CandidateChain]:
        """Check every referenced script exists and is currently plannable."""
        if not candidates:
            raise OctopError(ErrorCode.BIO_TASK_STATE_INVALID, "no candidates provided")
        for cand in candidates:
            if not cand.id or not cand.tool_chain:
                raise OctopError(
                    ErrorCode.BIO_TASK_STATE_INVALID,
                    f"candidate {cand.id!r} has an empty tool chain",
                )
            for tool in cand.tool_chain:
                row = self.library.require_script(tool.id)
                if not script_available(row):
                    raise OctopError(
                        ErrorCode.BIO_SCRIPT_UNAVAILABLE, f"script unavailable: {tool.id}"
                    )
        return candidates

    def mark_previously_used(
        self, candidates: list[CandidateChain], *, agent_id: str, user_id: int
    ) -> list[CandidateChain]:
        """Flag chains identical to ones this user already completed with this agent."""
        seen: set[tuple[str, ...]] = set()
        for row in self.tasks.list_for_user(user_id=user_id):
            if row.agent_id != agent_id or not row.selected_candidate_json:
                continue
            past = candidate_from_json(row.selected_candidate_json)
            if past is not None:
                seen.add(tuple(t.id for t in past.tool_chain))
        return [
            CandidateChain(
                id=c.id,
                tool_chain=c.tool_chain,
                previously_used=tuple(t.id for t in c.tool_chain) in seen,
                total_runtime=c.total_runtime,
                total_cost=c.total_cost,
            )
            for c in candidates
        ]

    def present_candidates(
        self,
        task_id: str,
        candidates: list[CandidateChain],
        *,
        agent_id: str,
        user_id: int,
    ) -> BioTaskRow:
        task = self.require_task(task_id)
        validated = self.validate_candidates(candidates)
        flagged = self.mark_previously_used(validated, agent_id=agent_id, user_id=user_id)
        return self._transition(task, "pathSelection", candidates_json=candidates_to_json(flagged))

    def derive_slots(self, chain: CandidateChain) -> list[FileSlot]:
        """Merge the input slot declarations of every script in the chain."""
        merged: dict[str, FileSlot] = {}
        for tool in chain.tool_chain:
            row = self.library.scripts.get(tool.id)
            if row is None:
                continue
            for slot in slots_from_json(row.inputs):
                if slot.label not in merged:
                    merged[slot.label] = slot
        return list(merged.values())

    def select_path(self, task_id: str, *, candidate_id: str, candidate_data: str) -> BioTaskRow:
        task = self.require_task(task_id)
        candidates = candidates_from_json(task.candidates_json)
        match = next((c for c in candidates if c.id == candidate_id), None)
        if match is None:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"candidate {candidate_id!r} does not belong to task {task_id}",
            )
        slots = self.derive_slots(match)
        return self._transition(
            task,
            "uploading",
            selected_candidate_json=candidate_data or candidate_to_json(match),
            required_files_json=slots_to_json(slots),
        )

    def request_files(self, task_id: str, slots: list[FileSlot]) -> BioTaskRow:
        task = self.require_task(task_id)
        if task.status not in ("uploading", "pathSelection"):
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"task {task_id} is not awaiting files (status={task.status})",
            )
        self.tasks.update(task_id, status="uploading", required_files_json=slots_to_json(slots))
        return self.require_task(task_id)

    def confirm_upload(self, task_id: str, mappings: list[FileMapping]) -> BioTaskRow:
        task = self.require_task(task_id)
        slots = slots_from_json(task.required_files_json)
        slot_by_label = {s.label: s for s in slots}
        covered: dict[str, int] = {}
        for m in mappings:
            slot = slot_by_label.get(m.slot_label)
            if slot is None:
                raise OctopError(
                    ErrorCode.BIO_FILE_SLOT_MISMATCH, f"unknown slot: {m.slot_label!r}"
                )
            if slot.extensions:
                suffix = (
                    m.original_name.rsplit(".", 1)[-1].lower() if "." in m.original_name else ""
                )
                if suffix not in slot.extensions and f".{suffix}" not in slot.extensions:
                    raise OctopError(
                        ErrorCode.BIO_FILE_SLOT_MISMATCH,
                        f"file {m.original_name!r} does not match slot {m.slot_label!r}",
                    )
            covered[m.slot_label] = covered.get(m.slot_label, 0) + 1
        missing = [s.label for s in slots if covered.get(s.label, 0) == 0]
        if missing:
            raise OctopError(
                ErrorCode.BIO_FILE_SLOT_MISMATCH,
                f"missing files for slots: {', '.join(missing)}",
            )
        return self._transition(task, "generating", file_mappings_json=mappings_to_json(mappings))

    def save_generated_code(self, task_id: str, *, code: str, origin: str) -> BioTaskRow:
        task = self.require_task(task_id)
        if origin not in ("library", "llm"):
            raise OctopError(ErrorCode.BIO_TASK_STATE_INVALID, f"bad code origin: {origin!r}")
        return self._transition(task, "validating", generated_code=code, code_origin=origin)

    def start_execution(self, task_id: str, *, run_dir: str) -> BioTaskRow:
        task = self.require_task(task_id)
        if not task.generated_code and not task.selected_candidate_json:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID, f"task {task_id} has no code to execute"
            )
        import time  # noqa: PLC0415

        return self._transition(
            task,
            "executing",
            run_dir=run_dir,
            exec_status="running",
            exec_started_at=int(time.time()),
        )

    def finish_execution(self, task_id: str, *, exit_code: int, error: str = "") -> BioTaskRow:
        import time  # noqa: PLC0415

        task = self.require_task(task_id)
        ok = exit_code == 0 and not error
        target = "completed" if ok else "failed"
        return self._transition(
            task,
            target,
            exec_status="done" if ok else "failed",
            exec_finished_at=int(time.time()),
            exec_exit_code=exit_code,
            exec_error=error,
        )

    def fail(self, task_id: str, *, error: str) -> BioTaskRow:
        task = self.require_task(task_id)
        if task.status in ("completed", "failed"):
            return task
        self.tasks.update(task_id, status="failed", exec_error=error)
        return self.require_task(task_id)

    # ── internal-turn trigger ────────────────────────────────────────────

    async def poke_agent(self, *, agent_id: str, session_key: str, prompt: str) -> None:
        """Ask the agent to continue the workflow after a user confirmation."""
        if self._push is not None:
            await self._push(agent_id, session_key, prompt)
