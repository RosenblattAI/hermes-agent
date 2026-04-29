"""Copilot subprocess launcher: spawns ``copilot -i`` with ``--remote``.

Uses the GitHub Copilot CLI in interactive mode (``-i <prompt>``) with
``--allow-all --remote``.  Interactive mode is required because
``--remote`` only works against a persistent session — the
non-interactive ``-p`` flag exits as soon as the prompt completes and
never registers with the cloud relay.

Because interactive mode renders a TUI, copilot is wrapped in
``script -eqfc`` to allocate a PTY, with stdout/stderr captured to a log
file.  The ``-e`` flag propagates the child exit code so ``complete_job.py``
can record the correct terminal state.  The session ID is pre-generated and passed via
``--resume=<uuid>`` so it is known immediately, while the remote task ID
used by ``--connect`` is extracted from copilot output/logs after the
remote session is registered (see :func:`_wait_for_remote_task_id`).

Reconnecting later via ``copilot --connect=<handle>`` uses the remote
task ID (``.../tasks/<uuid>`` handle), **not** the pre-generated local
session ID used for ``--resume``.

When launched for real (not via ``_spawn`` or ``dry_run``), the wrapper
is fully detached (``start_new_session=True``).  A shell wrapper runs
copilot and then invokes ``complete_job.py`` to update the DB — no
daemon thread required, so the parent ``hermes`` process can exit
immediately without killing copilot.
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from copilot_jobs.models import RepoEntry

logger = logging.getLogger(__name__)

_DEFAULT_COPILOT_PATHS = [
    "/usr/local/share/npm-global/bin/copilot",
    "/usr/local/bin/copilot",
    "/usr/bin/copilot",
]

# Patterns to extract session info from copilot output (JSONL or plain text).
SESSION_ID_PATTERN = re.compile(
    r"session[_\s-]?id[:\s]+([a-zA-Z0-9_-]+)", re.IGNORECASE
)
REMOTE_TASK_ID_PATTERN = re.compile(
    r"Remote session active \(steerable\): .*?/tasks/([0-9a-f-]+)",
    re.IGNORECASE,
)


def _parse_line_for_session_id(line: str) -> Optional[str]:
    """Try to extract a session ID from a single output line."""
    line = line.strip()
    if not line:
        return None

    # Try JSON first.
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            sid = obj.get("sessionId") or obj.get("session_id")
            if sid:
                return str(sid)
    except (json.JSONDecodeError, ValueError):
        pass

    # Regex fallback.
    m = SESSION_ID_PATTERN.search(line)
    return m.group(1) if m else None


def parse_copilot_output(output: str) -> Dict[str, Optional[str]]:
    """Parse copilot stdout for session handles.

    Checks JSONL lines first (``--output-format json``), then falls back
    to regex matching on plain text.

    Returns ``{"session_id": ... or None}``.
    """
    for line in output.splitlines():
        sid = _parse_line_for_session_id(line)
        if sid:
            return {"session_id": sid}
    return {"session_id": None}


def _parse_remote_task_id(log_text: str, requested_session_id: str) -> Optional[str]:
    """Extract the exported remote task ID for a launched Copilot session."""
    if requested_session_id not in log_text:
        return None

    match = REMOTE_TASK_ID_PATTERN.search(log_text)
    return match.group(1) if match else None


def _resolve_copilot_bin(copilot_bin: str) -> str:
    """Resolve the Copilot executable path with sensible fallbacks."""
    resolved = shutil.which(copilot_bin)
    if resolved:
        return resolved

    if os.path.sep in copilot_bin:
        return copilot_bin

    for candidate in _DEFAULT_COPILOT_PATHS:
        if Path(candidate).exists():
            return candidate

    return copilot_bin


def _log_mtime(p: Path) -> float:
    """Return mtime for sorting; 0.0 if the file was removed during rotation."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _wait_for_remote_task_id(
    requested_session_id: str,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.1,
) -> Optional[str]:
    """Poll Copilot process logs for the exported remote task ID."""
    logs_dir = Path.home() / ".copilot" / "logs"
    deadline = time.time() + timeout

    while time.time() < deadline:
        for path in sorted(
            logs_dir.glob("process-*.log"),
            key=_log_mtime,
            reverse=True,
        ):
            try:
                task_id = _parse_remote_task_id(
                    path.read_text(encoding="utf-8", errors="ignore"),
                    requested_session_id,
                )
            except OSError:
                continue
            if task_id:
                return task_id
        time.sleep(poll_interval)

    return None


def build_copilot_command(
    prompt: str,
    *,
    copilot_bin: str = "copilot",
    model: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """Build the ``copilot -i`` command list.

    Flags used:
      -i <prompt>       interactive mode, auto-execute prompt (persists)
      --allow-all       auto-approve all tool use
      --remote          enable cloud relay for --connect
      --resume=<uuid>   pin session to a pre-generated ID
      --no-auto-update  skip update check
      --no-ask-user     fully autonomous

    Note: ``--silent`` and ``--output-format json`` are intentionally
    omitted because they conflict with the interactive TUI required by
    ``--remote``/``--connect``.
    """
    cmd = [
        copilot_bin,
        "-i", prompt,
        "--allow-all",
        "--remote",
        "--no-auto-update",
        "--no-ask-user",
    ]
    if session_id:
        cmd.extend(["--resume", session_id])
    if model:
        cmd.extend(["--model", model])
    return cmd


def _log_dir() -> Path:
    """Return (and create) the copilot log directory."""
    from hermes_constants import get_hermes_home
    d = get_hermes_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def launch_copilot(
    repo: RepoEntry,
    prompt: str,
    *,
    session_id: str,
    copilot_bin: str = "copilot",
    model: Optional[str] = None,
    dry_run: bool = False,
    on_complete: Optional[Callable[[str, int], None]] = None,
    db: Any = None,
    _spawn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Launch ``copilot -i`` with ``--remote`` for a repo.

    *session_id* is the pre-generated UUID used as both the hermes job ID
    and the copilot session (passed via ``--resume=<uuid>``).

    *db* is an optional :class:`~hermes_state.SessionDB` instance.  When
    provided, the resolved ``connect_id`` is persisted via
    :meth:`~hermes_state.SessionDB.update_copilot_job_connect_id` so it
    survives process restarts and is available for ``--connect`` resumption
    and distributed tracing.

    **Real launches** (no ``_spawn``): copilot runs fully detached via a
    shell wrapper that redirects stdout to a log file and calls
    ``complete_job.py`` on exit.  The parent process can exit immediately.

    **Test launches** (``_spawn`` provided): a daemon thread waits for the
    fake process and calls ``on_complete`` so tests can assert on exit
    behaviour synchronously.

    If *dry_run* is True, skips the subprocess and returns placeholders.

    Returns ``{"session_id": str, "cmd": [...], "proc": Popen|None,
    "connect_id": str|None}``.
    """
    cmd = build_copilot_command(
        prompt,
        copilot_bin=_resolve_copilot_bin(copilot_bin),
        model=model,
        session_id=session_id,
    )

    if dry_run:
        if on_complete:
            on_complete(session_id, 0)
        return {"session_id": session_id, "exit_code": 0, "cmd": cmd, "proc": None}

    try:
        if _spawn:
            # Test path: use the fake process with a daemon thread.
            proc = _spawn(cmd, repo.path)

            def _wait_and_finish():
                try:
                    proc.stdout.read()
                    proc.wait()
                    if on_complete:
                        on_complete(session_id, proc.returncode)
                except Exception as exc:
                    logger.error("Background wait error: %s", exc)
                    if on_complete:
                        on_complete(session_id, -1)

            waiter = threading.Thread(
                target=_wait_and_finish,
                daemon=True,
                name="copilot-wait",
            )
            waiter.start()
        else:
            # Real path: fully detached process via shell wrapper.
            # Interactive mode (-i) needs a PTY for its TUI to render and
            # for --remote to register with the cloud relay, so wrap with
            # ``script -eqfc`` which allocates a PTY, captures output, and
            # propagates the child exit code via ``-e``.
            log_path = _log_dir() / f"copilot-{session_id}.log"
            complete_script = str(
                Path(__file__).resolve().parent / "complete_job.py"
            )
            python_bin = sys.executable

            script_inner = shlex.join(cmd)
            script_cmd = [
                "script",
                "-eqfc",
                script_inner,
                str(log_path),
            ]

            # Shell command: run copilot under script(1), capture exit
            # code, then update the DB via complete_job.py.
            shell_cmd = (
                f'{shlex.join(script_cmd)} > /dev/null 2>&1; '
                f'_ec=$?; '
                f'{shlex.quote(python_bin)} {shlex.quote(complete_script)} '
                f'{shlex.quote(session_id)} $_ec'
            )

            bash_bin = shutil.which("bash") or "bash"
            proc = subprocess.Popen(
                [bash_bin, "-c", shell_cmd],
                cwd=repo.path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            connect_id = _wait_for_remote_task_id(session_id)
            if connect_id and db is not None:
                try:
                    db.update_copilot_job_connect_id(session_id, connect_id)
                except Exception:
                    logger.warning(
                        "Failed to persist connect_id %s for job %s",
                        connect_id, session_id, exc_info=True,
                    )

        return {
            "session_id": session_id,
            "connect_id": connect_id if not _spawn else None,
            "cmd": cmd,
            "proc": proc,
        }

    except Exception as exc:
        logger.error("Failed to launch copilot: %s", exc)
        raise
