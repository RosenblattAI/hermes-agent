"""``hermes copilot`` CLI subcommand — launch and list Copilot sessions.

Simplified interface: launch routes a prompt to a repo, spawns copilot
with ``--remote``, and persists the session.  Use
``copilot --resume=<job_id>`` to resume via the pre-generated session UUID,
or ``copilot --connect=<task_id>`` to re-attach using the cloud relay task
ID printed after a successful launch.
"""

import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from copilot_remote.router import _sanitize_for_log
from hermes_state import SessionDB


def _get_db() -> SessionDB:
    """Get a SessionDB instance using the standard Hermes home."""
    return SessionDB()



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
        "timed_out": "⏱️ timed_out",
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
        print(f"Router selected: {repo} ({repo_path})")

    if not repo_path:
        # Try to resolve repo_path from the slug via workspace discovery.
        from copilot_remote.router import _discover_repos
        entries = _discover_repos()
        matched = next((e for e in entries if e.slug == repo), None)
        if matched:
            repo_path = matched.path
            print(f"Resolved path for {repo}: {repo_path}")
        else:
            print(
                f"Error: --repo-path is required for {repo!r} "
                "(could not resolve via HERMES_WORKSPACE_PATH).",
                file=sys.stderr,
            )
            sys.exit(1)

    db = _get_db()
    job_id = str(uuid.uuid4())

    # Create job record
    db.create_copilot_job(
        job_id=job_id,
        repo_slug=repo,
        repo_path=repo_path,
        prompt=prompt or None,
        signal_source=getattr(args, "signal_source", None) or "cli",
        signal_ref=getattr(args, "signal_ref", None),
    )

    print(f"Launching copilot job: {job_id}")
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
            finish_db.finish_copilot_job(
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
            table="copilot_jobs",
        )
    except Exception as exc:
        from agent.redact import redact_sensitive_text
        error_text = _sanitize_for_log(redact_sensitive_text(str(exc)))
        db.finish_copilot_job(job_id, state="failed", error_text=error_text)
        db.close()
        raise exc.__class__(error_text).with_traceback(exc.__traceback__) from None

    connect_id = result.get("connect_id")
    if connect_id:
        db.update_copilot_job_connect_id(job_id, connect_id)

    # For dry-run, the process already completed synchronously.
    if getattr(args, "dry_run", False):
        print(f"  State: {_state_badge('done')}")
    else:
        print(f"  State: 🟢 running")

    if connect_id:
        print(f"\n  Connect: copilot --connect={connect_id}")
    print(f"  Resume:  copilot --resume={job_id}")

    db.close()


def copilot_list(args):
    """List copilot jobs."""
    state = getattr(args, "state", None)
    limit = getattr(args, "limit", 20)

    db = _get_db()
    try:
        jobs = db.list_copilot_jobs(state=state, limit=limit)
        if not jobs:
            print("No copilot jobs found.")
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
        job = db.get_copilot_job(job_id)
        if not job:
            print(f"Error: Job not found: {job_id}", file=sys.stderr)
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

        sid = job.get("connect_id")
        if sid:
            print(f"Connect:  copilot --connect={sid}")
        print(f"Resume:   copilot --resume={job['id']}")

        if job.get("exit_code") is not None:
            print(f"Exit:     {job['exit_code']}")
        if job.get("error_text"):
            print(f"Error:    {job['error_text']}")
        if job.get("signal_source"):
            src = _sanitize_for_log(job["signal_source"])
            ref = f" ({_sanitize_for_log(job['signal_ref'])})" if job.get("signal_ref") else ""
            print(f"Signal:   {src}{ref}")

    finally:
        db.close()


def _find_copilot_pids(job_id: str) -> list:
    """Return PIDs of processes that are part of the copilot job.

    Matches lines containing ``--resume <job_id>`` (the copilot process) or
    ``complete_job.py`` with the job_id (the watcher process).  The current
    process is always excluded so ``copilot stop`` never signals itself.

    Raises ``RuntimeError`` when the ``ps`` invocation itself fails (non-zero
    exit, timeout, or binary not found) so callers can distinguish a scan
    failure from "no matching process".
    """
    own_pid = os.getpid()
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise RuntimeError(f"ps invocation failed: {exc}") from exc

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
        # Only match known copilot process patterns to avoid false positives.
        if (f"--resume {job_id}" not in args_part
                and f"--resume={job_id}" not in args_part
                and f"complete_job.py" not in args_part):
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
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
            any_signaled = True
        except ProcessLookupError:
            # Process group already exited between pgid collection and kill —
            # treat as "gone", not a signal failure.
            any_signaled = True
        except OSError:
            pass

    if not any_signaled:
        raise RuntimeError(
            f"Signal delivery failed for all process groups {pgids} "
            f"(job {job_id}); job may still be running."
        )

    # Wait up to *timeout* seconds for all matched processes to exit.
    deadline = time.time() + timeout
    surviving = list(pids)
    while surviving and time.time() < deadline:
        time.sleep(0.2)
        surviving = [p for p in surviving if _pid_exists(p)]

    # Force-kill entire process groups for any survivors so that child
    # processes that don't match the ps filter are also terminated.
    if surviving:
        for pgid in pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass

    return True


def _pid_exists(pid: int) -> bool:
    """Return True if the process still exists."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # Process exists but we don't own it
    except OSError:
        return False


def copilot_stop(args):
    """Stop a running Copilot job.

    Finds the copilot process tree via ``--resume <job_id>`` in the process
    list, sends SIGTERM (then SIGKILL if needed), and marks the DB row as
    stopped.  Uses a conditional UPDATE (``WHERE state = 'running'``) so
    that the transition is atomic even if complete_job.py races to write a
    terminal state at the same moment.
    """
    job_id = args.job_id

    db = _get_db()
    try:
        job = db.get_copilot_job(job_id)
        if not job:
            print(f"Error: Job not found: {job_id}", file=sys.stderr)
            sys.exit(1)

        if job["state"] != "running":
            print(
                f"Job {job_id} is already stopped (state: {_state_badge(job['state'])})."
            )
            return

        print(f"Stopping copilot job: {job_id}")
        try:
            killed = _kill_copilot_procs(job_id)
        except RuntimeError as exc:
            print(
                f"Error: process discovery failed — cannot safely stop job.\n"
                f"  {exc}\n"
                f"Aborting without modifying the DB state.",
                file=sys.stderr,
            )
            sys.exit(1)

        updated = db.finish_copilot_job(
            job_id,
            state="stopped",
            exit_code=-1,
            error_text="stopped by user",
        )
        if updated:
            db.skip_job_hooks(job_id)

        if killed:
            print(f"  Process tree terminated.")
        else:
            print(
                f"  No live process found for this job — "
                f"the job may have already exited."
            )

        if updated:
            print(f"  State: {_state_badge('stopped')}")
        else:
            # complete_job.py raced and already wrote a terminal state.
            current = db.get_copilot_job(job_id)
            current_state = current["state"] if current else "unknown"
            print(
                f"  Job exited on its own before the DB update; "
                f"state is now {_state_badge(current_state)}."
            )
    finally:
        db.close()


def copilot_command(args):
    """Route copilot subcommands."""
    subcmd = getattr(args, "copilot_action", None)

    if subcmd is None or subcmd == "list":
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
        print(f"Error: could not parse /copilot command: {e}", file=sys.stderr)
        return
    subcmd = parts[1] if len(parts) > 1 else "list"
    args_rest = parts[2:]

    try:
        if subcmd == "list":
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
    """Handle /copilot_remote slash command from an interactive Hermes session.

    Uses the ``copilot_remote`` package and DB table (distinct from the
    ``copilot_jobs`` implementation above).  Parses the raw command text
    and dispatches to the appropriate handler.  Uses ``shlex.split`` so
    quoted prompts (and any path argument containing spaces) are
    preserved as a single token instead of being shattered on whitespace.
    """
    import shlex
    import uuid as _uuid
    from types import SimpleNamespace
    from agent.redact import redact_sensitive_text

    def _remote_connect_handle(job):
        handle = job.get("connect_handle")
        return handle if handle else None

    def _remote_list(ns):
        state = getattr(ns, "state", None)
        limit = getattr(ns, "limit", 20)
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

    def _remote_launch(ns):
        prompt = getattr(ns, "prompt", None) or ""
        repo = getattr(ns, "repo", None)
        repo_path = getattr(ns, "repo_path", None)
        model = getattr(ns, "model", None)

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
            print(f"Router selected: {repo} ({repo_path})")

        if not repo_path:
            # Try to resolve repo_path from the slug via workspace discovery.
            from copilot_remote.router import _discover_repos
            entries = _discover_repos()
            matched = next((e for e in entries if e.slug == repo), None)
            if matched:
                repo_path = matched.path
                print(f"Resolved path for {repo}: {repo_path}")
            else:
                print(
                    f"Error: --repo-path is required for {repo!r} "
                    "(could not resolve via HERMES_WORKSPACE_PATH).",
                    file=sys.stderr,
                )
                sys.exit(1)

        db = _get_db()
        job_id = str(_uuid.uuid4())

        db.create_copilot_remote(
            job_id=job_id,
            repo_slug=repo,
            repo_path=repo_path,
            prompt=prompt or None,
            signal_source=getattr(ns, "signal_source", None) or "slash",
            signal_ref=getattr(ns, "signal_ref", None),
        )

        print(f"Launching copilot remote: {job_id}")
        print(f"  Repo: {repo}")
        if prompt:
            preview = prompt[:80] + ("..." if len(prompt) > 80 else "")
            print(f"  Prompt: {preview}")

        def _on_complete(session_id, exit_code):
            try:
                state = "done" if exit_code == 0 else "failed"
                finish_db = _get_db()
                finish_db.finish_copilot_remote(job_id, state=state, exit_code=exit_code)
                finish_db.close()
            except Exception:
                pass

        from copilot_remote.launcher import launch_copilot
        from copilot_remote.models import RepoEntry as _RE

        repo_entry = _RE(slug=repo, path=repo_path)
        try:
            result = launch_copilot(
                repo_entry, prompt,
                session_id=job_id,
                model=model,
                dry_run=getattr(ns, "dry_run", False),
                on_complete=_on_complete,
            )
        except Exception as exc:
            redacted = _sanitize_for_log(redact_sensitive_text(str(exc)))
            db.finish_copilot_remote(job_id, state="failed", error_text=redacted)
            db.close()
            raise exc.__class__(redacted).with_traceback(exc.__traceback__) from None

        connect_handle = result.get("connect_id")
        if connect_handle:
            db.update_copilot_remote_connect_handle(job_id, connect_handle)

        prompt_delivery_warning = result.get("prompt_delivery_warning")

        if getattr(ns, "dry_run", False):
            print(f"  State: {_state_badge('done')}")
        else:
            print(f"  State: 🟢 running")

        if prompt_delivery_warning:
            print(
                f"  Warning: {_sanitize_for_log(prompt_delivery_warning)}",
                file=sys.stderr,
            )

        if connect_handle:
            print(f"\n  Connect: copilot --connect={connect_handle}")
            print(f"  Resume:  copilot --resume={job_id}")
        else:
            print(
                "\n  Connect handle unavailable — Hermes could not extract "
                "Copilot's remote task ID.\n"
                f"  Inspect ~/.hermes/logs/copilot-{job_id}.log and re-run "
                f"`hermes copilot show {job_id}` once the handle is recorded."
            )

        db.close()

    def _remote_show(ns):
        job_id = ns.job_id
        db = _get_db()
        try:
            job = db.get_copilot_remote(job_id)
            if not job:
                print(f"Error: Job not found: {job_id}", file=sys.stderr)
                sys.exit(1)

            print(f"Job:      {job['id']}")
            print(f"State:    {_state_badge(job['state'])}")
            print(f"Repo:     {job['repo_slug']}")
            print(f"Path:     {job['repo_path']}")
            print(f"Created:  {_relative_time(job['created_at'])}")

            if job.get("prompt"):
                preview = job["prompt"][:120] + ("..." if len(job["prompt"]) > 120 else "")
                print(f"Prompt:   {preview}")

            sid = _remote_connect_handle(job)
            if sid:
                print(f"Connect:  copilot --connect={sid}")
                print(f"Resume:   copilot --resume={sid}")
            else:
                print(
                    "Connect:  unavailable — Hermes did not extract a Copilot "
                    "reconnect handle for this job.\n"
                    f"          Inspect ~/.hermes/logs/copilot-{job['id']}.log "
                    "to recover it."
                )

            if job.get("exit_code") is not None:
                print(f"Exit:     {job['exit_code']}")
            if job.get("error_text"):
                print(f"Error:    {job['error_text']}")
            if job.get("signal_source"):
                ref = f" ({job['signal_ref']})" if job.get("signal_ref") else ""
                print(f"Signal:   {job['signal_source']}{ref}")
        finally:
            db.close()

    try:
        parts = shlex.split(raw_command.strip())
    except ValueError as exc:
        print(f"Error: could not parse /copilot_remote command: {exc}", file=sys.stderr)
        return

    subcmd = parts[1] if len(parts) > 1 else "list"
    args_rest = parts[2:]

    try:
        if subcmd == "list":
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
                        parsed = int(raw)
                    except ValueError:
                        print(
                            f"Error: --limit requires an integer (got {raw!r})",
                            file=sys.stderr,
                        )
                        return
                    ns.limit = max(1, min(parsed, 1000))
                    i += 2
                else:
                    i += 1
            _remote_list(ns)

        elif subcmd == "launch":
            ns = SimpleNamespace(
                prompt="", repo=None, repo_path=None, model=None,
                dry_run=False, signal_source="slash", signal_ref=None,
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
            _remote_launch(ns)

        elif subcmd == "show" and args_rest:
            ns = SimpleNamespace(job_id=args_rest[0])
            _remote_show(ns)

        else:
            print("Usage: /copilot_remote [launch|list|show]")
            print()
            print("  /copilot_remote list                        List all jobs")
            print("  /copilot_remote launch <prompt>             Route prompt → repo, launch copilot")
            print("  /copilot_remote launch --model <m> <prompt> Use specific model")
            print("  /copilot_remote launch --repo <slug> <msg>  Launch for specific repo")
            print("  /copilot_remote show <job_id>               Show job details + connect command")

    except SystemExit:
        pass
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
