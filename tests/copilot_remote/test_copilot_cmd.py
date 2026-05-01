"""Tests for hermes_cli.copilot_cmd — slash command parsing and dispatch."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    """Provide a fresh SessionDB with HERMES_HOME pointed at tmp_path."""
    db_path = tmp_path / ".hermes" / "state.db"
    db_path.parent.mkdir(parents=True)
    _db = SessionDB(db_path=db_path)
    _real_close = _db.close
    _db.close = lambda: None
    yield _db
    _real_close()


@pytest.fixture(autouse=True)
def _patch_get_db(db, monkeypatch):
    """Patch _get_db in copilot_cmd to use the test DB."""
    monkeypatch.setattr(
        "hermes_cli.copilot_cmd._get_db", lambda: db
    )


def _capture_slash(cmd: str) -> str:
    """Run handle_copilot_remote_slash and capture combined stdout+stderr."""
    from hermes_cli.copilot_cmd import handle_copilot_remote_slash
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        handle_copilot_remote_slash(cmd)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return buf.getvalue()


class TestSlashList:
    def test_empty_list(self):
        out = _capture_slash("/copilot_remote list")
        assert "No copilot remote jobs found" in out

    def test_list_shows_job(self, db):
        db.create_copilot_remote(
            job_id="aaaaaaaa-0000-0000-0000-000000000001", repo_slug="my-repo", repo_path="/test"
        )
        out = _capture_slash("/copilot_remote list")
        assert "aaaaaaaa-0000-0000-0000-000000000001" in out
        assert "my-repo" in out

    def test_default_subcommand_is_list(self, db):
        """Bare /copilot_remote with no subcommand should show the job list."""
        db.create_copilot_remote(
            job_id="bbbbbbbb-0000-0000-0000-000000000001", repo_slug="bare-repo", repo_path="/test"
        )
        out = _capture_slash("/copilot_remote")
        assert "bbbbbbbb-0000-0000-0000-000000000001" in out

    def test_list_state_filter(self, db):
        db.create_copilot_remote(
            job_id="cccccccc-0000-0000-0000-000000000001", repo_slug="repo-a", repo_path="/a"
        )
        db.create_copilot_remote(
            job_id="cccccccc-0000-0000-0000-000000000002", repo_slug="repo-b", repo_path="/b"
        )
        db.finish_copilot_remote("cccccccc-0000-0000-0000-000000000002", state="done", exit_code=0)

        out = _capture_slash("/copilot_remote list --state running")
        assert "cccccccc-0000-0000-0000-000000000001" in out
        assert "cccccccc-0000-0000-0000-000000000002" not in out


class TestSlashLaunchDryRun:
    def test_dry_run_launches(self, db):
        out = _capture_slash(
            "/copilot_remote launch --dry-run --repo dr-repo --repo-path /dr Do something"
        )
        assert "done" in out.lower() or "connect" in out.lower()

        jobs = db.list_copilot_remote(state="done")
        assert len(jobs) == 1

    def test_model_flag(self, db):
        out = _capture_slash(
            "/copilot_remote launch --dry-run --model gpt-5 --repo m-repo --repo-path /m Test"
        )
        assert "done" in out.lower() or "connect" in out.lower()

    def test_launch_surfaces_prompt_delivery_warning(self, db):
        fake_result = {
            "session_id": "job-123",
            "connect_id": "task-123",
            "cmd": ["copilot"],
            "proc": None,
            "prompt_delivery_status": "unverified",
            "prompt_delivery_warning": "Hermes could not determine the remote task ID.",
        }

        with patch("copilot_remote.launcher.launch_copilot", return_value=fake_result):
            out = _capture_slash(
                "/copilot_remote launch --repo warn-repo --repo-path /warn Respond"
            )

        assert "warning:" in out.lower()
        assert "remote task id" in out.lower()
        assert "copilot --connect=task-123" in out


class TestSlashShow:
    def test_show_existing(self, db):
        db.create_copilot_remote(
            job_id="dddddddd-0000-0000-0000-000000000001", repo_slug="show-repo", repo_path="/show"
        )
        out = _capture_slash("/copilot_remote show dddddddd-0000-0000-0000-000000000001")
        assert "dddddddd-0000-0000-0000-000000000001" in out
        assert "show-repo" in out
        assert "connect" in out.lower()

    def test_show_prefers_external_connect_handle(self, db):
        db.create_copilot_remote(
            job_id="eeeeeeee-0000-0000-0000-000000000001",
            repo_slug="show-repo",
            repo_path="/show",
            connect_handle="task-123",
        )
        out = _capture_slash("/copilot_remote show eeeeeeee-0000-0000-0000-000000000001")
        assert "copilot --connect=task-123" in out

    def test_show_nonexistent(self):
        out = _capture_slash("/copilot_remote show dddddddd-0000-0000-0000-999999999999")
        assert "not found" in out.lower()


def _capture_copilot_slash(cmd: str) -> str:
    """Run handle_copilot_slash and capture combined stdout+stderr."""
    from hermes_cli.copilot_cmd import handle_copilot_slash
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        handle_copilot_slash(cmd)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return buf.getvalue()


def _capture_fn(fn, *args, **kwargs) -> str:
    """Call fn(*args, **kwargs) and capture combined stdout+stderr."""
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        fn(*args, **kwargs)
    except SystemExit:
        pass
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return buf.getvalue()


class TestSlashErrorPaths:
    def test_empty_launch(self):
        out = _capture_slash("/copilot_remote launch")
        assert "required" in out.lower()

    def test_unknown_subcommand_shows_help(self):
        out = _capture_slash("/copilot_remote foobar")
        assert "usage" in out.lower()
        assert "launch" in out


class TestStopCommand:
    """Tests for copilot_stop and the stop → list/show state transition."""

    JOB_ID = "ffffffff-0000-0000-0000-000000000001"

    def _make_running_job(self, db):
        db.create_copilot_remote(
            job_id=self.JOB_ID, repo_slug="stop-repo", repo_path="/stop"
        )
        return self.JOB_ID

    def test_stop_marks_job_stopped(self, db):
        """stop writes 'stopped' state and list/show reflect it immediately."""
        self._make_running_job(db)

        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True):
            out = _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        assert "stopped" in out.lower()

        # DB state must be "stopped" (not "running" or "failed").
        job = db.get_copilot_remote(self.JOB_ID)
        assert job["state"] == "stopped"
        assert job["error_text"] == "stopped by user"

    def test_list_shows_stopped_not_running_after_stop(self, db):
        """After stop, list must NOT show the job as running."""
        self._make_running_job(db)

        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True):
            _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        out = _capture_copilot_slash(f"/copilot list")
        # "running" state badge must NOT appear for this job.
        assert "running" not in out

    def test_show_reflects_stopped_state(self, db):
        """show must display 'stopped' state after a successful stop."""
        self._make_running_job(db)

        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True):
            _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        out = _capture_copilot_slash(f"/copilot show {self.JOB_ID}")
        assert "stopped" in out.lower()

    def test_stop_on_already_terminal_job_is_noop(self, db):
        """Calling stop on a 'done' job reports it is not running."""
        self._make_running_job(db)
        db.finish_copilot_remote(self.JOB_ID, state="done", exit_code=0)

        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=False) as mock_kill:
            out = _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        # kill should never be called for a non-running job.
        mock_kill.assert_not_called()
        assert "not running" in out.lower()
        # State unchanged.
        assert db.get_copilot_remote(self.JOB_ID)["state"] == "done"

    def test_complete_job_cannot_overwrite_stopped_state(self, db):
        """finish_copilot_remote is a no-op when the job is already in a terminal state."""
        self._make_running_job(db)

        # Simulate stop setting state to "stopped".
        rows = db.finish_copilot_remote(self.JOB_ID, state="stopped", exit_code=-1,
                                        error_text="stopped by user")
        assert rows == 1

        # Simulate complete_job.py arriving late and trying to overwrite with "failed".
        rows2 = db.finish_copilot_remote(self.JOB_ID, state="failed", exit_code=1)
        assert rows2 == 0  # No rows updated — already terminal.

        job = db.get_copilot_remote(self.JOB_ID)
        assert job["state"] == "stopped"          # unchanged
        assert job["error_text"] == "stopped by user"  # preserved

    def test_finish_copilot_remote_returns_1_on_first_transition(self, db):
        """finish_copilot_remote returns 1 when the job transitions from running."""
        self._make_running_job(db)
        rows = db.finish_copilot_remote(self.JOB_ID, state="done", exit_code=0)
        assert rows == 1

    def test_finish_copilot_remote_returns_0_on_repeat(self, db):
        """finish_copilot_remote returns 0 when called a second time."""
        self._make_running_job(db)
        db.finish_copilot_remote(self.JOB_ID, state="done", exit_code=0)
        rows = db.finish_copilot_remote(self.JOB_ID, state="done", exit_code=0)
        assert rows == 0

    def test_stop_no_process_found_still_updates_db(self, db):
        """Even with no live process, stop must write 'stopped' to the DB."""
        self._make_running_job(db)

        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=False):
            out = _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        assert "no live process" in out.lower()
        assert db.get_copilot_remote(self.JOB_ID)["state"] == "stopped"

    def test_state_badge_stopped(self):
        """The 'stopped' state has a distinct badge."""
        from hermes_cli.copilot_cmd import _state_badge
        badge = _state_badge("stopped")
        assert "stopped" in badge
        # Must not fall through to the raw state name (i.e., it IS in the map).
        assert badge != "stopped"

    def test_stop_race_complete_job_wins_first(self, db):
        """If complete_job.py commits before stop's DB write, stop reports current state."""
        self._make_running_job(db)

        # Simulate: complete_job.py wins the write lock and sets state="done".
        db.finish_copilot_remote(self.JOB_ID, state="done", exit_code=0)

        # Now stop is called; kill succeeds but the DB update is a no-op.
        with patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True):
            out = _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        # stop already checked state="running" and saw "done" from get_copilot_remote
        # — so it reported "already stopped" before even trying to kill.
        assert "not running" in out.lower()
        assert db.get_copilot_remote(self.JOB_ID)["state"] == "done"

    def test_stop_calls_kill_with_job_id(self, db):
        """copilot_stop forwards the job_id to _kill_copilot_procs.

        Guards against the call site drifting from the function signature.
        The pid= kwarg is a PR#15 (schema v14) feature and is not present here.
        """
        self._make_running_job(db)

        with patch(
            "hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True
        ) as mock_kill:
            _capture_copilot_slash(f"/copilot stop {self.JOB_ID}")

        mock_kill.assert_called_once_with(self.JOB_ID)

    def test_stop_aborts_db_write_on_ps_failure(self, db):
        """copilot_stop does NOT mark the job stopped when _kill_copilot_procs raises."""
        self._make_running_job(db)

        with patch(
            "hermes_cli.copilot_cmd._kill_copilot_procs",
            side_effect=RuntimeError("ps exited with code 1: permission denied"),
        ):
            out = _capture_fn(
                __import__("hermes_cli.copilot_cmd", fromlist=["copilot_stop"]).copilot_stop,
                __import__("types").SimpleNamespace(job_id=self.JOB_ID),
            )

        assert "process discovery failed" in out
        # DB state must still be 'running' — we did not transition it.
        assert db.get_copilot_remote(self.JOB_ID)["state"] == "running"

    def test_stop_aborts_db_write_on_signal_failure(self, db):
        """copilot_stop does NOT mark the job stopped when signals are rejected (e.g. PermissionError)."""
        self._make_running_job(db)

        with patch(
            "hermes_cli.copilot_cmd._kill_copilot_procs",
            side_effect=RuntimeError("Signal delivery failed for all process groups"),
        ):
            out = _capture_fn(
                __import__("hermes_cli.copilot_cmd", fromlist=["copilot_stop"]).copilot_stop,
                __import__("types").SimpleNamespace(job_id=self.JOB_ID),
            )

        # Must abort without touching the DB.
        assert "process discovery failed" in out
        assert db.get_copilot_remote(self.JOB_ID)["state"] == "running"


