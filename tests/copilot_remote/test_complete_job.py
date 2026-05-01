"""Tests for copilot_remote.complete_job — the shell-exit DB callback."""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from copilot_remote.complete_job import finish
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hermes_db_path() -> Path:
    """Return the DB path for the current test's isolated HERMES_HOME."""
    import os
    return Path(os.environ["HERMES_HOME"]) / "state.db"


def _make_remote(db: SessionDB) -> str:
    sid = str(uuid.uuid4())
    db.create_copilot_remote(sid, repo_slug="acme/test", repo_path="/tmp", prompt="test")
    return sid


# ---------------------------------------------------------------------------
# Unit tests for finish() — copilot_remote table (default)
# ---------------------------------------------------------------------------

class TestFinishRemote:
    def test_exit_zero_marks_done(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_remote(db)
        db.close()

        finish(sid, exit_code=0)

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_remote(sid)
        db2.close()
        assert row["state"] == "done"
        assert row["exit_code"] == 0

    def test_nonzero_exit_marks_failed(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_remote(db)
        db.close()

        finish(sid, exit_code=2)

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_remote(sid)
        db2.close()
        assert row["state"] == "failed"
        assert row["exit_code"] == 2

    def test_unknown_job_does_not_raise(self):
        db = SessionDB(db_path=_hermes_db_path())
        db.close()
        finish(str(uuid.uuid4()), exit_code=0)  # must not raise


# ---------------------------------------------------------------------------
# Integration tests: real subprocess invocation
# ---------------------------------------------------------------------------

_COMPLETE_JOB_SCRIPT = str(
    (Path(__file__).resolve().parent.parent.parent / "copilot_remote" / "complete_job.py")
)


class TestCompleteJobSubprocess:
    @staticmethod
    def _run(*args):
        import os
        return subprocess.run(
            [sys.executable, _COMPLETE_JOB_SCRIPT, *args],
            env=dict(os.environ),
            capture_output=True,
            text=True,
        )

    def test_subprocess_marks_remote_done(self):
        db = SessionDB(db_path=_hermes_db_path())
        sid = _make_remote(db)
        db.close()

        result = self._run(sid, "0")
        assert result.returncode == 0, result.stderr

        db2 = SessionDB(db_path=_hermes_db_path())
        row = db2.get_copilot_remote(sid)
        db2.close()
        assert row["state"] == "done"

    def test_subprocess_bad_args_exits_nonzero(self):
        result = self._run()
        assert result.returncode != 0

    def test_subprocess_invalid_exit_code_exits_nonzero(self):
        result = self._run(str(uuid.uuid4()), "not-a-number")
        assert result.returncode != 0

    def test_subprocess_unknown_table_exits_nonzero(self):
        result = self._run(str(uuid.uuid4()), "0", "unknown_table")
        assert result.returncode != 0
        assert "unknown_table" in result.stderr


class TestFinishValidation:
    def test_unknown_table_raises_system_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            finish(str(uuid.uuid4()), exit_code=0, table="bad_table")
        assert exc_info.value.code == 2
