"""Local execution backend for approved bio analysis code.

``BioExecutor`` is the seam: Phase 1 ships only :class:`LocalShellExecutor`
(asyncio subprocess on this host); a future qsub/cluster backend implements
the same protocol. Boundary rule: the executor runs *only* the approved
``generated_code`` materialized into ``bio_runs/<task_id>/`` inside the
agent's host workspace — never arbitrary shell from anywhere else.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from octop.infra.db.repos.bio_analysis_tasks import BioTaskRow
from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from octop.infra.bioinformatics.service import BioWorkflowService
    from octop.infra.db.repos.threads import ThreadRepo
    from octop.infra.utils.paths import PathLayout

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 3600

# Crude denylist — the HITL approval is the real gate; this catches obvious
# disasters in generated code before it ever reaches a subprocess.
_DANGEROUS_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",
    "shutdown",
    "reboot",
    "format c:",
    "del /f /s /q c:\\",
)


class BioExecutor(Protocol):
    """Execution backend seam (local shell now, qsub later)."""

    async def start(self, task: BioTaskRow) -> BioTaskRow: ...

    async def read_logs(self, task: BioTaskRow, *, offset: int = 0) -> tuple[str, int]: ...


def _script_kind(code: str) -> str:
    """'python' (default, cross-platform) or 'bash' (POSIX only)."""
    first = code.lstrip().split("\n", 1)[0]
    if first.startswith("#!") and ("bash" in first or first.rstrip().endswith("sh")):
        return "bash"
    return "python"


def validate_code(code: str) -> None:
    """Static gate before execution: denylist + syntax check."""
    lowered = code.lower()
    for pat in _DANGEROUS_PATTERNS:
        if pat in lowered:
            raise OctopError(
                ErrorCode.BIO_EXECUTION_UNSUPPORTED,
                f"generated code matches denied pattern: {pat!r}",
            )
    kind = _script_kind(code)
    if kind == "bash":
        if os.name != "posix":
            raise OctopError(
                ErrorCode.BIO_EXECUTION_UNSUPPORTED,
                "bash scripts are only supported on POSIX hosts",
            )
        import subprocess  # noqa: PLC0415

        probe = subprocess.run(
            ["bash", "-n", "/dev/stdin"],
            input=code.encode(),
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"bash syntax check failed: {probe.stderr.decode(errors='replace')[:500]}",
            )
    else:
        import py_compile  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        except py_compile.PyCompileError as exc:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"python syntax check failed: {str(exc)[:500]}",
            ) from exc
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class LocalShellExecutor:
    """Runs approved code as a local subprocess; logs to ``run.log``."""

    def __init__(
        self,
        *,
        paths: PathLayout,
        service: BioWorkflowService,
        thread_repo: ThreadRepo,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._paths = paths
        self._service = service
        self._threads = thread_repo
        self._timeout = timeout_seconds
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    # ── BioExecutor protocol ─────────────────────────────────────────────

    async def start(self, task: BioTaskRow) -> BioTaskRow:
        if not task.generated_code:
            raise OctopError(
                ErrorCode.BIO_TASK_STATE_INVALID,
                f"task {task.id} has no approved code to execute",
            )
        validate_code(task.generated_code)

        kind = _script_kind(task.generated_code)
        run_dir = self._paths.agent_workspace(task.agent_id) / "bio_runs" / task.id
        run_dir.mkdir(parents=True, exist_ok=True)
        script_name = "main.sh" if kind == "bash" else "main.py"
        (run_dir / script_name).write_text(task.generated_code, encoding="utf-8")

        # Wire declared input files into the run dir via symlinks so the
        # script sees a flat ./inputs/ layout regardless of inbound naming.
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        self._link_inputs(task, inputs_dir)

        updated = self._service.start_execution(
            task.id, run_dir=str(run_dir.relative_to(self._paths.agent_workspace(task.agent_id)))
        )
        argv = ["bash", script_name] if kind == "bash" else [sys.executable, script_name]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._procs[task.id] = proc
        asyncio.get_running_loop().create_task(self._watch(task, proc, run_dir / "run.log"))
        return updated

    async def read_logs(self, task: BioTaskRow, *, offset: int = 0) -> tuple[str, int]:
        if not task.run_dir:
            return "", offset
        log_path = self._paths.agent_workspace(task.agent_id) / task.run_dir / "run.log"
        if not log_path.exists():
            return "", offset
        data = await asyncio.to_thread(log_path.read_bytes)
        if offset >= len(data):
            return "", offset
        return data[offset:].decode("utf-8", errors="replace"), len(data)

    # ── restart recovery ─────────────────────────────────────────────────

    def recover_incomplete(self) -> int:
        """Mark DB rows left in 'executing' by a server restart as failed.

        Called once at boot; in-memory process handles do not survive.
        """
        recovered = 0
        for row in self._service.tasks.list_by_exec_status("running"):
            if row.id in self._procs:
                continue
            self._service.fail(row.id, error="server restarted during execution")
            recovered += 1
        if recovered:
            logger.info("bio executor: marked %d interrupted run(s) failed", recovered)
        return recovered

    # ── internals ────────────────────────────────────────────────────────

    def _link_inputs(self, task: BioTaskRow, inputs_dir: Path) -> None:
        from octop.infra.bioinformatics.models import mappings_from_json  # noqa: PLC0415

        ws_root = self._paths.agent_workspace(task.agent_id)
        for m in mappings_from_json(task.file_mappings_json):
            src = ws_root / m.workspace_path
            if not src.exists():
                continue
            dst = inputs_dir / m.original_name
            if dst.exists():
                continue
            try:
                dst.symlink_to(src)
            except OSError:
                # Windows without symlink privilege / exotic FS: copy instead.
                import shutil  # noqa: PLC0415

                shutil.copy2(src, dst)

    async def _watch(
        self, task: BioTaskRow, proc: asyncio.subprocess.Process, log_path: Path
    ) -> None:
        """Stream stdout into run.log, enforce timeout, finalize the task."""
        timed_out = False
        assert proc.stdout is not None
        with log_path.open("ab") as log:
            try:

                async def _pump() -> None:
                    assert proc.stdout is not None
                    while True:
                        chunk = await proc.stdout.read(65536)
                        if not chunk:
                            break
                        log.write(chunk)
                        log.flush()

                await asyncio.wait_for(_pump(), timeout=self._timeout)
            except TimeoutError:
                timed_out = True
                proc.kill()
                log.write(b"\n[bio] execution timed out; process killed\n")
            except Exception:
                logger.exception("bio run %s log pump failed", task.id)
        exit_code = await proc.wait()
        self._procs.pop(task.id, None)

        updated = self._service.finish_execution(
            task.id,
            exit_code=exit_code if not timed_out else -1,
            error="execution timed out"
            if timed_out
            else ("" if exit_code == 0 else f"exit code {exit_code}"),
        )
        await self._notify_done(updated)

    async def _notify_done(self, task: BioTaskRow) -> None:
        """Push a completion turn so the expert interprets results in chat."""
        thread = self._threads.get(task.thread_id)
        if thread is None:
            logger.warning("bio completion push skipped: thread %s gone", task.thread_id)
            return
        ok = task.status == "completed"
        prompt = (
            f"分析任务 {task.id} 已执行完成（退出码 {task.exec_exit_code}）。请读取运行日志和输出文件，"
            "向用户总结分析结果，并告知输出文件位置。"
            if ok
            else f"分析任务 {task.id} 执行失败（{task.exec_error}）。请读取 run.log 诊断原因，"
            "向用户解释失败原因并建议修复方案。"
        )
        try:
            await self._service.poke_agent(
                agent_id=task.agent_id,
                session_key=thread.session_key,
                prompt=prompt,
            )
        except Exception:
            logger.exception("bio completion push failed for task %s", task.id)


__all__ = [
    "BioExecutor",
    "DEFAULT_TIMEOUT_SECONDS",
    "LocalShellExecutor",
    "validate_code",
]
