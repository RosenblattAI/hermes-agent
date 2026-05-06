"""``hermes copilot`` CLI subcommand — launch and list Copilot sessions.

Simplified interface: launch routes a prompt to a repo, spawns copilot
with ``--remote``, and persists the session.  Use
``copilot --resume=<job_id>`` to resume via the pre-generated session UUID,
or ``copilot --connect=<task_id>`` to re-attach using the cloud relay task
ID printed after a successful launch.
"""

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from typing import Optional

from copilot_remote.github_task_url import build_github_task_web_url
from hermes_constants import display_hermes_home
from hermes_logging import sanitize_for_log as _sanitize_for_log
from hermes_state import SessionDB


def _get_db() -> SessionDB:
    """Get a SessionDB instance using the standard Hermes home."""
    return SessionDB()


def _connect_handle(job: dict) -> Optional[str]:
    """Return the Copilot reconnect handle for connect/resume, or ``None``.

    Reads only the dedicated ``connect_handle`` column populated by the
    launcher. When that handle was not extracted (Copilot CLI changed its
    output, the verification HTTP probe failed, etc.) this returns
    ``None`` so callers can surface an explicit "connect handle
    unavailable" message rather than fabricating an invalid reconnect
    command from the Hermes job UUID. The launcher does not pass the
    Hermes ``job_id`` into Copilot via ``--resume``, so the job UUID is
    not a usable reconnect handle. ``signal_ref`` is intentionally *not*
    consulted either — it stores caller metadata (e.g. a Jira ticket
    ID) and would otherwise produce invalid
    ``copilot --connect=<ticket-id>`` output.
    """
    handle = job.get("connect_handle")
    return handle if handle else None


def _github_task_web_url(job: dict) -> Optional[str]:
    """Build a GitHub task web URL when the stored job metadata is sufficient.

    Returns ``None`` unless the job has a connect handle, a repo slug, and a
    repo path that resolves to an existing directory whose basename matches the
    stored slug. The shared helper also requires a GitHub ``remote.origin.url``
    whose repo segment matches that slug.
    """
    handle = _connect_handle(job)
    if not handle:
        return None
    repo_path = job.get("repo_path", "") or ""
    repo_slug = str(job.get("repo_slug") or job.get("repo") or "")
    return build_github_task_web_url(repo_path, repo_slug, handle)


def _relative_time(ts) -> str:
    """Format a timestamp as relative time."""
    if not ts:
        return "-"
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    if delta < 172800:
        return "yesterday"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _utc_time(ts) -> str:
    """Format a timestamp as a UTC ISO-8601 string."""
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _state_badge(state: str) -> str:
    """Return a colored state indicator."""
    badges = {
        "running": "🟢 running",
        "done": "✅ done",
        "failed": "🔴 failed",
        "stopped": "🛑 stopped",
    }
    return badges.get(state, state)


def copilot_launch(args):
    """Launch a new Copilot session for a repo.

    Routes the prompt to a repo (or uses --repo), spawns copilot with
    --remote, captures the session ID from early output, and returns
    immediately while copilot continues working in the background.
    """
    prompt = getattr(args, "prompt", None) or ""
    repo = getattr(args, "repo", None)
    repo_path = getattr(args, "repo_path", None)
    model = getattr(args, "model", None)

    # If no explicit repo, try the router
    if not repo:
        if not prompt:
            print("Error: --repo or a prompt is required.", file=sys.stderr)
            sys.exit(1)
        from copilot_remote.router import route_repo
        entry = route_repo(prompt)
        if not entry:
            print(
                "Error: Could not determine target repo from prompt. "
                "Use --repo to specify explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
        repo = entry.slug
        repo_path = repo_path or entry.path
        print(f"Router selected: {_sanitize_for_log(repo)} ({_sanitize_for_log(str(repo_path))})")

    if not repo_path:
        # Try to resolve repo_path from the slug via workspace discovery.
        from copilot_remote.router import _discover_repos
        try:
            entries = _discover_repos()
        except OSError as exc:
            print(
                f"Error: workspace discovery failed ({_sanitize_for_log(repr(exc))}). "
                "Use --repo-path to specify the path explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
        matched_all = [e for e in entries if e.slug.lower() == repo.lower()]
        if len(matched_all) > 1:
            paths = ", ".join(_sanitize_for_log(str(e.path)) for e in matched_all)
            print(
                f"Error: slug {_sanitize_for_log(repo)!r} is ambiguous — matches "
                f"multiple workspace repos ({paths}). "
                "Use --repo-path to specify which one.",
                file=sys.stderr,
            )
            sys.exit(1)
        matched = matched_all[0] if matched_all else None
        if matched:
            repo_path = matched.path
            repo = matched.slug  # normalise to the canonical casing stored in the workspace
            print(f"Resolved path for {_sanitize_for_log(repo)}: {_sanitize_for_log(str(repo_path))}")
        else:
            print(
                f"Error: --repo-path is required for {_sanitize_for_log(repo)!r} "
                "(could not resolve via HERMES_WORKSPACE_PATH).",
                file=sys.stderr,
            )
            sys.exit(1)

    db = _get_db()
    job_id = str(uuid.uuid4())

    # Create job record
    db.create_copilot_remote(
        job_id=job_id,
        repo_slug=repo,
        repo_path=repo_path,
        prompt=prompt or None,
        signal_source=getattr(args, "signal_source", None) or "cli",
        signal_ref=getattr(args, "signal_ref", None),
    )

    print(f"Launching copilot remote: {job_id}")
    print(f"  Repo: {repo}")
    if prompt:
        preview = prompt[:80] + ("..." if len(prompt) > 80 else "")
        print(f"  Prompt: {preview}")

    # Completion callback — only used for dry_run and test (_spawn) paths.
    # Real launches use complete_job.py for DB updates.
    def _on_complete(session_id, exit_code):
        try:
            state = "done" if exit_code == 0 else "failed"
            finish_db = _get_db()
            finish_db.finish_copilot_remote(
                job_id,
                state=state,
                exit_code=exit_code,
            )
            finish_db.close()
        except Exception:
            pass  # Best-effort — don't crash the daemon thread

    # Launch copilot
    from copilot_remote.launcher import launch_copilot
    from copilot_remote.models import RepoEntry as _RE

    repo_entry = _RE(slug=repo, path=repo_path)
    try:
        result = launch_copilot(
            repo_entry, prompt,
            session_id=job_id,
            model=model,
            dry_run=getattr(args, "dry_run", False),
            on_complete=_on_complete,
        )
    except Exception as exc:
        from agent.redact import redact_sensitive_text
        error_text = _sanitize_for_log(redact_sensitive_text(str(exc)))
        db.finish_copilot_remote(job_id, state="failed", error_text=error_text)
        db.close()
        raise exc.__class__(error_text).with_traceback(exc.__traceback__) from None

    connect_id = result.get("connect_id")
    if connect_id:
        db.update_copilot_remote_connect_handle(job_id, connect_id)

    proc = result.get("proc")
    if proc is not None:
        try:
            # proc.pid is the bash wrapper / PGID leader spawned by Popen.
            # It is NOT the inner Copilot CLI child PID, but it IS the
            # process-group leader used by _kill_copilot_procs for SIGTERM/SIGKILL.
            db.update_copilot_remote_pid(job_id, proc.pid)
        except Exception:
            pass  # best-effort — pid is informational

    prompt_delivery_warning = result.get("prompt_delivery_warning")

    # For dry-run, the process already completed synchronously.
    if getattr(args, "dry_run", False):
        print(f"  State: {_state_badge('done')}")
    else:
        print(f"  State: 🟢 running")

    if prompt_delivery_warning:
        print(
            f"  Warning: {_sanitize_for_log(prompt_delivery_warning)}",
            file=sys.stderr,
        )

    if connect_id:
        print(f"\n  Connect: copilot --connect={connect_id}")
        print(f"  Resume:  copilot --resume={job_id}")
        job = {
            "repo_path": repo_path,
            "repo_slug": repo,
            "connect_handle": connect_id,
        }
        web = _github_task_web_url(job)
        if web:
            print(f"  Web:     {web}")
    else:
        # connect_id was not extracted — the 5-second task-ID scan timed out.
        # copilot_show reads only the DB, so re-running it will not help.
        # Direct the operator to the log file for the raw Copilot output.
        print(
            f"  Note: connect handle not yet available.\n"
            f"  Check {display_hermes_home()}/logs/copilot-{job_id}.log "
            f"for the raw Copilot session output."
        )

    db.close()


def copilot_list(args):
    """List copilot jobs."""
    state = getattr(args, "state", None)
    # Clamp limit on both CLI (argparse) and slash-command paths so that
    # negative values (e.g. --limit -1) cannot trigger an unbounded SQLite
    # LIMIT and dump the full table.
    try:
        limit = max(1, min(int(getattr(args, "limit", 20) or 20), 1000))
    except (TypeError, ValueError):
        limit = 20

    db = _get_db()
    try:
        jobs = db.list_copilot_remote(state=state, limit=limit)
        if not jobs:
            print("No copilot remote jobs found.")
            return

        fmt = "{:<38s} {:<20s} {:<12s} {:<21s}"
        print(fmt.format("ID", "REPO", "STATE", "CREATED (UTC)"))
        print("-" * 95)
        for job in jobs:
            print(fmt.format(
                job["id"][:38],
                (job["repo_slug"] or "")[:20],
                _state_badge(job["state"])[:12],
                _utc_time(job["created_at"]),
            ))
    finally:
        db.close()


def copilot_show(args):
    """Show details of a copilot job."""
    job_id = args.job_id

    db = _get_db()
    try:
        job = db.get_copilot_remote(job_id)
        if not job:
            print(f"Error: Job not found: {_sanitize_for_log(job_id)}", file=sys.stderr)
            sys.exit(1)

        print(f"Job:      {job['id']}")
        print(f"State:    {_state_badge(job['state'])}")
        print(f"Repo:     {job['repo_slug']}")
        print(f"Path:     {job['repo_path']}")
        print(f"Created:  {_relative_time(job['created_at'])}")

        if job.get("prompt"):
            raw = job["prompt"][:120] + ("..." if len(job["prompt"]) > 120 else "")
            preview = _sanitize_for_log(raw)
            print(f"Prompt:   {preview}")

        sid = _connect_handle(job)
        if sid:
            # --connect and --resume are only valid while the remote session is
            # alive.  For terminal states the relay has shut down; suppress both
            # commands so operators are not handed stale reconnect instructions.
            if job.get("state") == "running":
                print(f"Connect:  copilot --connect={sid}")
                print(f"Resume:   copilot --resume={job['id']}")
            web = _github_task_web_url(job)
            if web:
                print(f"Web:      {web}")
        else:
            print(
                "Connect:  unavailable — Hermes did not extract a Copilot "
                f"remote task ID. Check {display_hermes_home()}/logs/copilot-{job['id']}.log"
            )
            # Only suggest --resume for running jobs; for terminal states the
            # session is gone and --resume would create an unrelated new session.
            if job.get("state") == "running":
                print(f"Resume:   copilot --resume={job['id']}")

        if job.get("exit_code") is not None:
            print(f"Exit:     {job['exit_code']}")
        if job.get("pid"):
            print(f"Launcher PID: {job['pid']} (bash wrapper / PGID leader)")
        if job.get("error_text"):
            print(f"Error:    {_sanitize_for_log(job['error_text'])}")
        if job.get("signal_source"):
            src = _sanitize_for_log(job["signal_source"])
            ref = f" ({_sanitize_for_log(job['signal_ref'])})" if job.get("signal_ref") else ""
            print(f"Signal:   {src}{ref}")

    finally:
        db.close()


def _find_copilot_pids(job_id: str) -> list:
    """Return PIDs of the Copilot CLI process associated with the job.

    Only matches lines containing ``--resume <job_id>`` or
    ``--resume=<job_id>`` (the Copilot CLI process itself).
    ``complete_job.py`` is intentionally excluded: it is a post-exit DB
    callback and may still be running after the Copilot child has finished.
    Killing it would race with its terminal-state write and could permanently
    misclassify a completed job as ``stopped``.  The current process is
    always excluded so ``copilot stop`` never signals itself.

    Raises ``RuntimeError`` when the ``ps`` invocation itself fails (non-zero
    exit, timeout, or binary not found) so callers can distinguish a scan
    failure from "no matching process".
    """
    own_pid = os.getpid()
    ps_bin = shutil.which("ps")
    if ps_bin is None:
        raise RuntimeError("ps not found on PATH; cannot scan for copilot processes")
    try:
        result = subprocess.run(
            # "-A eww" requests all processes with environment + unlimited
            # command-line width so long prompts don't truncate --resume <id>.
            # Mirrors the pattern used in hermes_cli/gateway.py.
            [ps_bin, "-A", "eww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise RuntimeError(f"ps invocation failed: {_sanitize_for_log(repr(exc))}") from exc

    if result.returncode != 0:
        stderr_snippet = _sanitize_for_log(result.stderr.strip()[:200])
        raise RuntimeError(
            f"ps exited with code {result.returncode}: {stderr_snippet}"
        )

    pids = []
    for line in result.stdout.splitlines():
        if job_id not in line:
            continue
        parts = line.split(None, 1)
        args_part = parts[1] if len(parts) == 2 else ""
        # Only match the Copilot CLI process itself (--resume <job_id>).
        #
        # Both wrapper processes embed the Copilot command in their own args:
        #   bash -c "script ... --resume <job_id> ...; complete_job.py ..."
        #   script -eqfc "copilot ... --resume <job_id>" /logpath
        # If Copilot has already exited but one of these wrappers is still alive
        # (e.g. script waiting for its child, or bash running complete_job.py),
        # matching them would signal the whole PGID and race the terminal-state write.
        # Skip any process whose executable is a known shell/pty wrapper.
        executable = args_part.split(None, 1)[0].rsplit("/", 1)[-1] if args_part else ""
        if executable in ("bash", "script"):
            continue
        if (f"--resume {job_id}" not in args_part
                and f"--resume={job_id}" not in args_part):
            continue
        if not parts[0].strip():
            continue
        try:
            found_pid = int(parts[0].strip())
        except ValueError:
            continue
        if found_pid == own_pid:
            continue  # never kill ourselves
        pids.append(found_pid)
    return pids


def _kill_copilot_procs(job_id: str, *, timeout: float = 5.0) -> bool:
    """Kill the process tree associated with a copilot job.

    Sends SIGTERM to every process group that contains processes matching
    the job_id string, waits up to *timeout* seconds for them to exit,
    then sends SIGKILL to any survivors.

    Returns:
        ``True``  — at least one process was found and signaled.
        ``False`` — no matching processes found (job already exited).

    Raises:
        RuntimeError — matching processes were found but every signal
            delivery attempt failed (e.g. PermissionError), meaning the
            job is still running and the caller must *not* mark it stopped.
    """
    pids = _find_copilot_pids(job_id)
    if not pids:
        return False

    # Collect unique process group IDs so we can kill entire groups.
    pgids: set = set()
    for p in pids:
        try:
            pgids.add(os.getpgid(p))
        except OSError:
            pass

    if not pgids:
        # Processes existed but disappeared before we could get pgids —
        # they're gone, so the job is no longer running.
        return False

    # Graceful shutdown first.
    any_signaled = False
    any_vanished = False  # process group was already gone before our signal
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
            any_signaled = True
        except ProcessLookupError:
            # Process group already exited between pgid collection and kill.
            # Do NOT count as a delivered signal — the job may have finished
            # on its own rather than being stopped by us.
            any_vanished = True
        except OSError:
            pass

    if not any_signaled:
        if any_vanished:
            # All pgids were gone before we could signal them — the job
            # finished on its own.  Return False so the caller does not
            # write 'stopped' to the DB.
            return False
        raise RuntimeError(
            f"Signal delivery failed for all process groups {pgids} "
            f"(job {_sanitize_for_log(job_id)}); job may still be running."
        )

    # Wait up to *timeout* seconds for all matched processes to exit.
    deadline = time.time() + timeout
    surviving = list(pids)
    while surviving and time.time() < deadline:
        time.sleep(0.2)
        surviving = [p for p in surviving if _pid_exists(p)]

    # Force-kill entire process groups for any matched survivors.
    if surviving:
        for pgid in pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        # Give the kernel a moment to reap, then re-check matched PIDs.
        time.sleep(0.2)
        still_alive = [p for p in surviving if _pid_exists(p)]
    else:
        still_alive = []

    # Always probe PGID liveness — even when all matched PIDs are gone,
    # an unmatched child in the same process group might still be alive
    # (it could have ignored SIGTERM and not appeared in _find_copilot_pids).
    # Note: os.killpg(pgid, 0) also succeeds for groups that contain ONLY
    # zombie processes; _pgid_has_living_members() filters those out on Linux.
    living_pgids = []
    for pgid in pgids:
        try:
            os.killpg(pgid, 0)  # signal 0 = existence probe
            if _pgid_has_living_members(pgid):
                living_pgids.append(pgid)
        except ProcessLookupError:
            pass  # entire group is gone
        except OSError:
            living_pgids.append(pgid)  # EPERM: group exists but not owned

    if still_alive or living_pgids:
        raise RuntimeError(
            f"PIDs {still_alive} / PGIDs {living_pgids} survived SIGKILL "
            f"for job {_sanitize_for_log(job_id)}; process may still be running."
        )

    return True


def _pgid_has_living_members(pgid: int) -> bool:
    """Return True if the process group contains at least one non-zombie process.

    ``os.killpg(pgid, 0)`` succeeds for groups that consist entirely of
    zombie processes.  This helper scans ``/proc/*/stat`` on Linux (the
    ``stat`` file exposes PGID; ``status`` does not on modern kernels) to
    confirm at least one living member exists.  On non-Linux platforms
    (where ``/proc`` is unavailable) it conservatively returns True so that
    callers treat the group as still alive and attempt a SIGKILL.

    ``/proc/<pid>/stat`` format: ``pid (comm) state ppid pgrp ...``
    We split from the last ``)``) to handle comms that contain spaces/parens.
    """
    proc_root = pathlib.Path("/proc")
    if not proc_root.is_dir():
        # /proc not present (macOS, BSDs) — Path.glob() would silently return
        # no entries on Python ≥ 3.12 without raising OSError, so we must
        # guard explicitly to preserve the conservative-True behaviour.
        return True
    found_living = False
    try:
        for stat_path in proc_root.glob("*/stat"):
            try:
                text = stat_path.read_text()
                # Split on the LAST ')' to safely skip the comm field.
                after_comm = text[text.rfind(")") + 1:].split()
                # after_comm: [state, ppid, pgrp, ...]
                if len(after_comm) < 3:
                    continue
                if int(after_comm[2]) == pgid:
                    state = after_comm[0]
                    if not state.startswith("Z"):
                        found_living = True
                        break
            except (OSError, ValueError):
                continue
    except OSError:
        # /proc not available (non-Linux) — be conservative
        return True
    return found_living


def _pid_exists(pid: int) -> bool:
    """Return True if the process still exists and is not a zombie.

    ``os.kill(pid, 0)`` succeeds for zombie processes (they retain their PID
    entry until waited on).  Since copilot launches are detached and never
    waited on by this process, a dead bash wrapper can linger as a zombie and
    fool the check.  On Linux we confirm via ``/proc/{pid}/stat`` (field layout:
    ``pid (comm) state ppid pgrp ...``); on other platforms we fall back to the
    signal-0 result alone.
    """
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # process exists but we don't own it
    except OSError:
        return False
    # Signal 0 succeeded — verify the process is not a zombie on Linux.
    try:
        stat_text = pathlib.Path(f"/proc/{pid}/stat").read_text()
        after_comm = stat_text[stat_text.rfind(")") + 1:].split()
        # after_comm[0] is the state character; 'Z' means zombie.
        if after_comm and after_comm[0].startswith("Z"):
            return False
    except OSError:
        pass  # non-Linux or /proc entry already gone
    return True


def copilot_stop(args):
    """Stop a running Copilot job.

    Finds the copilot process tree via ``--resume <job_id>`` in the process
    list, sends SIGTERM (then SIGKILL if needed), and marks the DB row as
    stopped.  Uses a conditional UPDATE (``WHERE state = 'running'``) so
    that the transition is atomic even if complete_job.py races to write a
    terminal state at the same moment.
    """
    job_id = args.job_id
    safe_job_id = _sanitize_for_log(job_id)

    db = _get_db()
    try:
        job = db.get_copilot_remote(job_id)
        if not job:
            print(f"Error: Job not found: {safe_job_id}", file=sys.stderr)
            sys.exit(1)

        if job["state"] != "running":
            print(
                f"Job {safe_job_id} is not running (state: {_state_badge(job['state'])})."
            )
            return

        print(f"Stopping copilot job: {safe_job_id}")
        try:
            killed = _kill_copilot_procs(job_id)
        except RuntimeError as exc:
            print(
                f"Error: could not stop job — {exc}\n"
                f"Aborting without modifying the DB state.",
                file=sys.stderr,
            )
            sys.exit(1)

        if killed:
            updated = db.finish_copilot_remote(
                job_id,
                state="stopped",
                exit_code=-1,
                error_text="stopped by user",
            )
            print(f"  Process tree terminated.")
            if updated:
                print(f"  State: {_state_badge('stopped')}")
            else:
                # complete_job.py raced and already wrote a terminal state.
                current = db.get_copilot_remote(job_id)
                current_state = current["state"] if current else "unknown"
                print(
                    f"  Job exited on its own before the DB update; "
                    f"state is now {_state_badge(current_state)}."
                )
        else:
            print(
                f"  No live process found for this job — "
                f"the job may have already exited or is running in the cloud only.\n"
                f"  DB state was not modified."
            )
    finally:
        db.close()


def copilot_command(args):
    """Route copilot subcommands."""
    subcmd = getattr(args, "copilot_action", None)

    if subcmd is None or subcmd in ("list", "ls"):
        copilot_list(args)
        return

    handlers = {
        "launch": copilot_launch,
        "show": copilot_show,
        "stop": copilot_stop,
    }

    handler = handlers.get(subcmd)
    if handler:
        handler(args)
    else:
        print(f"Unknown copilot command: {subcmd}")
        print("Usage: hermes copilot [launch|list|show|stop]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Slash command handler (interactive session: /copilot ...)
# ---------------------------------------------------------------------------

def handle_copilot_slash(raw_command: str) -> None:
    """Handle /copilot slash command from an interactive Hermes session.

    Parses the raw command text and dispatches to the appropriate handler.
    """
    from types import SimpleNamespace
    import shlex

    try:
        parts = shlex.split(raw_command.strip())
    except ValueError as e:
        print(f"Error: could not parse /copilot command: {_sanitize_for_log(str(e))}", file=sys.stderr)
        return
    subcmd = parts[1] if len(parts) > 1 else "list"
    args_rest = parts[2:]

    try:
        if subcmd in ("list", "ls"):
            ns = SimpleNamespace(state=None, limit=20)
            i = 0
            while i < len(args_rest):
                a = args_rest[i]
                if a == "--state" and i + 1 < len(args_rest):
                    ns.state = args_rest[i + 1]
                    i += 2
                elif a == "--limit" and i + 1 < len(args_rest):
                    raw = args_rest[i + 1]
                    try:
                        ns.limit = max(1, min(int(raw), 1000))
                    except ValueError:
                        print(
                            f"Error: --limit requires an integer (got {raw!r})",
                            file=sys.stderr,
                        )
                        return
                    i += 2
                else:
                    i += 1
            copilot_list(ns)

        elif subcmd == "launch":
            ns = SimpleNamespace(
                prompt="", repo=None, repo_path=None, model=None,
                dry_run=False,
                signal_source="slash", signal_ref=None,
            )
            i = 0
            prompt_parts = []
            while i < len(args_rest):
                if args_rest[i] == "--repo" and i + 1 < len(args_rest):
                    ns.repo = args_rest[i + 1]
                    i += 2
                elif args_rest[i] == "--repo-path" and i + 1 < len(args_rest):
                    ns.repo_path = args_rest[i + 1]
                    i += 2
                elif args_rest[i] == "--model" and i + 1 < len(args_rest):
                    ns.model = args_rest[i + 1]
                    i += 2
                elif args_rest[i] == "--dry-run":
                    ns.dry_run = True
                    i += 1
                else:
                    prompt_parts.append(args_rest[i])
                    i += 1
            ns.prompt = " ".join(prompt_parts)
            copilot_launch(ns)

        elif subcmd == "show" and args_rest:
            ns = SimpleNamespace(job_id=args_rest[0])
            copilot_show(ns)

        elif subcmd == "stop" and args_rest:
            ns = SimpleNamespace(job_id=args_rest[0])
            copilot_stop(ns)

        else:
            print("Usage: /copilot [launch|list|show|stop]")
            print()
            print("  /copilot list                        List all jobs")
            print("  /copilot launch <prompt>             Route prompt → repo, launch copilot")
            print("  /copilot launch --model <m> <prompt> Use specific model")
            print("  /copilot launch --repo <slug> <msg>  Launch for specific repo")
            print("  /copilot show <job_id>               Show job details + connect command")
            print("  /copilot stop <job_id>               Stop a running job")

    except SystemExit:
        pass
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Slash command handler for the copilot_remote package
# (interactive session: /copilot_remote ...)
# ---------------------------------------------------------------------------

def handle_copilot_remote_slash(raw_command: str) -> None:
    """Handle /copilot_remote slash command — delegates to handle_copilot_slash.

    Both /copilot and /copilot_remote operate on the same copilot_remote table.
    /copilot_remote is kept as an alias for backward compatibility with existing
    gateway scripts and bot commands that were already using the name.
    """
    handle_copilot_slash(raw_command)
