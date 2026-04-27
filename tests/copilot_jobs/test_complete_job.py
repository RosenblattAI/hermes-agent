"""Tests for copilot_jobs.complete_job — the shell-exit DB callback."""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from copilot_jobs.complete_job import finish
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hermes_db_path() -> Path:
    """Return the DB path for the current test's isolated HERMES_HOME.

    DEFAULT_DB_PATH in hermes_state is frozen at module import time (before
    the conftest autouse fixture sets HERMES_HOME).  Reading os.environ at
    call time gives the correct per-test isolated path.
    """
    import os
    return Path(os.environ["HERMES_HOME"]) / "state.db"


def _make_job(db: SessionDB) -> str:
    sid = str(uuid.uuid4())
    db.create_copilot_job(sid, repo_slug="acme/test", repo_path="/tmp", prompt="test")
    return sid


# ---------------------------------------------------------------------------
# Unit tests for finish()
# ---------------------------------------------------------------------------

class TestFinish:
    """Direct calls to finish() — exercises the DB transition, no subprocess."""

    def test_exit_zero_marks_done(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_job(db)
        db.close()

        finish(sid, exit_code=0)

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_job(sid)
        db2.close()
        assert row["state"] == "done"
        assert row["exit_code"] == 0

    def test_nonzero_exit_marks_failed(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_job(db)
        db.close()

        finish(sid, exit_code=2)

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_job(sid)
        db2.close()
        assert row["state"] == "failed"
        assert row["exit_code"] == 2

    def test_unknown_job_does_not_raise(self):
        """finish() on a missing job should not propagate — the shell caller ignores errors."""
        db = SessionDB(db_path=_hermes_db_path())
        db.close()
        finish(str(uuid.uuid4()), exit_code=0)  # must not raise


# ---------------------------------------------------------------------------
# Integration tests: real subprocess invocation (the actual shell exit path)
# ---------------------------------------------------------------------------

_COMPLETE_JOB_SCRIPT = str(
    (Path(__file__).resolve().parent.parent.parent / "copilot_jobs" / "complete_job.py")
)


class TestCompleteJobSubprocess:
    """Invoke complete_job.py as a real subprocess, mirroring what launcher.py does.

    We deliberately reuse the conftest-isolated HERMES_HOME (set by the autouse
    ``_isolate_hermes_home`` fixture) so that both the in-process SessionDB calls
    and the subprocess share the same frozen DEFAULT_DB_PATH.  Fighting the
    module-level constant with monkeypatch.setenv causes the subprocess to write
    to a different file than the test reads.
    """

    @staticmethod
    def _run(*args):
        import os
        result = subprocess.run(
            [sys.executable, _COMPLETE_JOB_SCRIPT, *args],
            env=dict(os.environ),  # inherits conftest-isolated HERMES_HOME
            capture_output=True,
            text=True,
        )
        return result

    def test_subprocess_marks_done(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_job(db)
        db.close()

        result = self._run(sid, "0")
        assert result.returncode == 0, result.stderr

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_job(sid)
        db2.close()
        assert row["state"] == "done"
        assert row["exit_code"] == 0

    def test_subprocess_marks_failed(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_job(db)
        db.close()

        result = self._run(sid, "1")
        assert result.returncode == 0, result.stderr

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_job(sid)
        db2.close()
        assert row["state"] == "failed"
        assert row["exit_code"] == 1

    def test_subprocess_bad_args_exits_nonzero(self):
        result = self._run()
        assert result.returncode != 0

    def test_subprocess_invalid_exit_code_exits_nonzero(self):
        result = self._run(str(uuid.uuid4()), "not-a-number")
        assert result.returncode != 0
