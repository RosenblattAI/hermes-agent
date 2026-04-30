"""Update copilot job state after the copilot process exits.

Called by the shell wrapper that ``launcher.py`` spawns.  Runs outside
the original hermes process, so it must bootstrap its own DB connection.

Usage::

    python complete_job.py <session_id> <exit_code> [copilot_remote|copilot_jobs]

The optional third argument selects which DB table/method to update.
Defaults to ``copilot_remote`` for backward compatibility.
"""

import sys
from pathlib import Path

# Ensure the hermes-agent root is on sys.path so ``hermes_state`` resolves.
_AGENT_ROOT = str(Path(__file__).resolve().parent.parent)
if _AGENT_ROOT not in sys.path:
    sys.path.insert(0, _AGENT_ROOT)

from hermes_constants import get_hermes_home  # noqa: E402
from hermes_state import SessionDB  # noqa: E402


def finish(session_id: str, exit_code: int, table: str = "copilot_remote") -> None:
    state = "done" if exit_code == 0 else "failed"
    # Use get_hermes_home() so profile-aware DB path is resolved at call time.
    db = SessionDB(db_path=get_hermes_home() / "state.db")
    try:
        if table == "copilot_jobs":
            db.finish_copilot_job(session_id, state=state, exit_code=exit_code)
        else:
            db.finish_copilot_remote(session_id, state=state, exit_code=exit_code)
    finally:
        db.close()


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print(
            f"Usage: {sys.argv[0]} <session_id> <exit_code> [copilot_remote|copilot_jobs]",
            file=sys.stderr,
        )
        sys.exit(1)

    session_id = sys.argv[1]
    try:
        exit_code = int(sys.argv[2])
    except ValueError:
        print(f"Invalid exit_code: {sys.argv[2]}", file=sys.stderr)
        sys.exit(1)

    table = sys.argv[3] if len(sys.argv) == 4 else "copilot_remote"
    finish(session_id, exit_code, table=table)


if __name__ == "__main__":
    main()
