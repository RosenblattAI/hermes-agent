#!/usr/bin/env python3
"""
PR #19 — copilot job lifecycle demo
====================================
Exercises the real DB and CLI code paths end-to-end without spawning a
real Copilot process.  All output is printed with section headers so the
execution trace can be read as a narrative.
"""

import json
import os
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

# ─── Setup: point HERMES_HOME at a temp dir so we never touch ~/.hermes ───────
_tmp = tempfile.mkdtemp(prefix="pr19_demo_")
os.environ["HERMES_HOME"] = _tmp

sys.path.insert(0, "/home/andreszabala/.hermes")
from hermes_state import SessionDB
from hermes_cli.copilot_cmd import (
    copilot_list,
    copilot_show,
    copilot_stop,
    handle_copilot_remote_slash,
    _find_copilot_pids,
)
from copilot_remote.models import JobState
from hermes_logging import sanitize_for_log

# ─── Pretty helpers ───────────────────────────────────────────────────────────
W = 70
RESET = "\033[0m"
BOLD  = "\033[1m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
YEL   = "\033[33m"
RED   = "\033[31m"
DIM   = "\033[2m"

def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*W}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*W}{RESET}")

def ok(msg):   print(f"  {GREEN}✓{RESET}  {msg}")
def info(msg): print(f"  {DIM}→{RESET}  {msg}")
def warn(msg): print(f"  {YEL}!{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}"); sys.exit(1)

def capture(fn, *args, **kwargs):
    """Call fn and capture its stdout/stderr."""
    import io
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

# ─── DB fixture ───────────────────────────────────────────────────────────────
db_path = Path(_tmp) / "state.db"
db = SessionDB(db_path=db_path)

# Patch _get_db globally so copilot_cmd uses this DB
import hermes_cli.copilot_cmd as _cmd_mod
_cmd_mod._get_db = lambda: SessionDB(db_path=db_path)

print(f"\n{BOLD}PR #19 — copilot job lifecycle verification{RESET}")
print(f"{DIM}Sandbox HERMES_HOME: {_tmp}{RESET}")

# ══════════════════════════════════════════════════════════════════════════════
section("1 · Schema — SCHEMA_VERSION 13 & pid column")
# ══════════════════════════════════════════════════════════════════════════════

from hermes_state import SCHEMA_VERSION
if SCHEMA_VERSION != 13:
    fail(f"SCHEMA_VERSION expected 13, got {SCHEMA_VERSION}")
ok(f"SCHEMA_VERSION = {SCHEMA_VERSION}")

cols = {r[1] for r in db._conn.execute("PRAGMA table_info(copilot_remote)")}
for expected in ("id","hermes_session_id","repo_slug","repo_path","state",
                 "connect_handle","pid","created_at","finished_at"):
    if expected not in cols:
        fail(f"Missing column: {expected}")
ok(f"copilot_remote columns: {', '.join(sorted(cols))}")

# ══════════════════════════════════════════════════════════════════════════════
section("2 · Job creation & lifecycle: running → done")
# ══════════════════════════════════════════════════════════════════════════════

job_a = str(uuid.uuid4())
db.create_copilot_remote(
    job_id=job_a,
    repo_slug="acme/frontend",
    repo_path="/workspace/acme/frontend",
    prompt="Refactor the login page to use the new design system",
    signal_source="tool",
)
row = db.get_copilot_remote(job_a)
if row["state"] != "running":
    fail(f"Expected state=running, got {row['state']}")
ok(f"Job created  id={job_a[:8]}…  state={row['state']}")

db.update_copilot_remote_pid(job_a, pid=9876)
row = db.get_copilot_remote(job_a)
if row["pid"] != 9876:
    fail(f"pid not persisted: {row['pid']}")
ok(f"PID stored   pid={row['pid']}  (bash wrapper / PGID leader)")

db.update_copilot_remote_connect_handle(job_a, "tasks/abc-123")
row = db.get_copilot_remote(job_a)
ok(f"Handle stored connect_handle={row['connect_handle']}")

db.finish_copilot_remote(job_a, state="done", exit_code=0)
row = db.get_copilot_remote(job_a)
if row["state"] != "done":
    fail(f"Expected state=done, got {row['state']}")
ok(f"Job finished state={row['state']}  exit_code={row['exit_code']}")

# ══════════════════════════════════════════════════════════════════════════════
section("3 · copilot stop — finish_copilot_remote idempotency guard")
# ══════════════════════════════════════════════════════════════════════════════

job_b = str(uuid.uuid4())
db.create_copilot_remote(
    job_id=job_b,
    repo_slug="acme/api",
    repo_path="/workspace/acme/api",
    prompt="Add rate limiting middleware",
    signal_source="cli",
)
ok(f"Job created  id={job_b[:8]}…  state=running")

# First transition: running → stopped (returns 1 row changed)
n = db.finish_copilot_remote(job_b, state="stopped", exit_code=-1)
if n != 1:
    fail(f"Expected 1 row changed, got {n}")
ok(f"Stopped      finish_copilot_remote returns {n}  (1 = first write wins)")

# Idempotency: complete_job.py racing stop — must NOT overwrite stopped
n2 = db.finish_copilot_remote(job_b, state="done", exit_code=0)
if n2 != 0:
    fail(f"Expected 0 row changed on repeat, got {n2}")
row = db.get_copilot_remote(job_b)
if row["state"] != "stopped":
    fail(f"stop was overwritten! state={row['state']}")
ok(f"Idempotent   complete_job.py tried to set state=done → {n2} rows changed (noop)")
ok(f"Final state  state={row['state']}  (complete_job.py cannot overwrite stopped)")

# ══════════════════════════════════════════════════════════════════════════════
section("4 · JobState.is_terminal — state machine boundaries")
# ══════════════════════════════════════════════════════════════════════════════

terminal_states   = ["done", "failed", "stopped"]
non_terminal      = ["running"]
for s in terminal_states:
    js = JobState(s)
    if not js.is_terminal:
        fail(f"is_terminal=False for {s}")
    ok(f"is_terminal=True  for state='{s}'")
for s in non_terminal:
    js = JobState(s)
    if js.is_terminal:
        fail(f"is_terminal=True for {s}")
    ok(f"is_terminal=False for state='{s}'  (still alive)")

# ══════════════════════════════════════════════════════════════════════════════
section("5 · CLI: copilot list & show — terminal state gating")
# ══════════════════════════════════════════════════════════════════════════════

# Use job_a (done, has connect_handle) and job_b (stopped)
list_out = capture(copilot_list, SimpleNamespace(state=None, limit=10))
for jid in (job_a[:8], job_b[:8]):
    if jid not in list_out:
        fail(f"Job {jid}… not shown in list")
ok("copilot list shows both jobs")
if "running" in list_out and "done" not in list_out and "stopped" not in list_out:
    fail("list shows wrong states")
ok(f"States in list:\n" +
   "\n".join(f"      {l}" for l in list_out.strip().splitlines() if "state" in l.lower() or "Job" in l or "acme" in l))

show_done = capture(copilot_show, SimpleNamespace(job_id=job_a))
if "Connect:" in show_done:
    fail("Connect: shown for done job — should be suppressed")
if "Resume:" in show_done:
    fail("Resume: shown for done job — should be suppressed")
ok("copilot show (done): Connect: and Resume: correctly suppressed")

# Create a running job to verify the running path
job_c = str(uuid.uuid4())
db.create_copilot_remote(
    job_id=job_c,
    repo_slug="acme/mobile",
    repo_path="/workspace/acme/mobile",
    signal_source="tool",
)
db.update_copilot_remote_connect_handle(job_c, "tasks/xyz-789")
show_running = capture(copilot_show, SimpleNamespace(job_id=job_c))
if "Connect:" not in show_running:
    fail("Connect: missing for running job")
if "Resume:" not in show_running:
    fail("Resume: missing for running job")
ok("copilot show (running): Connect: and Resume: shown")
info(textwrap.indent(show_running.strip(), "    "))

# ══════════════════════════════════════════════════════════════════════════════
section("6 · _find_copilot_pids — process filter correctness")
# ══════════════════════════════════════════════════════════════════════════════

import unittest.mock as mock

fake_ps = "\n".join([
    f"{os.getpid()} /usr/bin/python3 demo.py",              # own pid — excluded
    f"1001 /usr/bin/copilot --resume={job_b}",              # real copilot match
    f"1002 bash -c 'script ... --resume {job_b}'",          # bash wrapper — excluded
    f"1003 script -eqfc 'copilot --resume {job_b}' /log",   # script wrapper — excluded
    f"1004 python3 complete_job.py --job-id {job_b}",       # complete_job — excluded
    f"1005 /usr/bin/copilot --resume={job_b}",              # second copilot match
    f"1006 /usr/bin/copilot --resume=other-job-id",         # different job — excluded
])

fake_result = mock.Mock()
fake_result.returncode = 0
fake_result.stdout = fake_ps
fake_result.stderr = ""
with mock.patch("hermes_cli.copilot_cmd.subprocess.run", return_value=fake_result):
    pids = _find_copilot_pids(job_b)

expected = {1001, 1005}
if set(pids) != expected:
    fail(f"Expected pids {expected}, got {set(pids)}")
ok(f"Own PID excluded:          pid={os.getpid()}")
ok(f"bash wrapper excluded:     pid=1002")
ok(f"script(1) wrapper excluded: pid=1003")
ok(f"complete_job.py excluded:  pid=1004")
ok(f"Wrong job excluded:        pid=1006")
ok(f"Matched real copilot PIDs: {sorted(pids)}")

# ══════════════════════════════════════════════════════════════════════════════
section("7 · sanitize_for_log — CWE-117 log injection prevention")
# ══════════════════════════════════════════════════════════════════════════════

cases = [
    ("clean string",        "hello world",                "hello world"),
    ("newline injection",   "org/repo\nfake log entry",   "org/repo fake log entry"),
    ("carriage return",     "x\ry",                       "x y"),
    ("null byte",           "abc\x00def",                 "abc def"),
    ("tab (sanitized)",     "a\tb",                       "a b"),
    ("printable ASCII",     "GET /api?k=v&x=1",           "GET /api?k=v&x=1"),
]
for label, inp, expected in cases:
    result = sanitize_for_log(inp)
    if result != expected:
        fail(f"{label}: expected {expected!r}, got {result!r}")
    ok(f"{label:<26} {inp!r:<32} → {result!r}")

# ══════════════════════════════════════════════════════════════════════════════
section("8 · Slug disambiguation — ambiguous repo name rejected")
# ══════════════════════════════════════════════════════════════════════════════

from copilot_remote.router import RepoEntry

def _launch_slug(slug, entries):
    with mock.patch("copilot_remote.router._discover_repos",
                    return_value=[RepoEntry(slug=e[0], path=e[1]) for e in entries]):
        ns = SimpleNamespace(repo=slug, repo_path=None, prompt="test", dry_run=True)
        from hermes_cli.copilot_cmd import copilot_launch
        return capture(copilot_launch, ns)

out_unique = _launch_slug("frontend", [("frontend", "/workspace/org-a/frontend")])
if "ambiguous" in out_unique.lower():
    fail("Unique slug incorrectly flagged as ambiguous")
ok("Unique slug resolves without error")

out_ambig = _launch_slug("frontend", [
    ("frontend", "/workspace/org-a/frontend"),
    ("frontend", "/workspace/org-b/frontend"),
])
if "ambiguous" not in out_ambig.lower():
    fail("Ambiguous slug not rejected")
if "--repo-path" not in out_ambig:
    fail("Error missing --repo-path hint")
ok("Ambiguous slug (two orgs, same name) → error with --repo-path hint")
info(out_ambig.strip())

# ══════════════════════════════════════════════════════════════════════════════
section("9 · Gateway slash handler — /copilot_remote stop")
# ══════════════════════════════════════════════════════════════════════════════

job_d = str(uuid.uuid4())
db.create_copilot_remote(
    job_id=job_d,
    repo_slug="acme/backend",
    repo_path="/workspace/acme/backend",
    signal_source="gateway",
)

# Simulate /copilot_remote stop with process killed successfully
with mock.patch("hermes_cli.copilot_cmd._kill_copilot_procs", return_value=True):
    out = capture(handle_copilot_remote_slash, f"/copilot_remote stop {job_d}")

row = db.get_copilot_remote(job_d)
if row["state"] != "stopped":
    fail(f"Gateway stop did not mark job stopped: {row['state']}")
ok(f"Gateway /copilot_remote stop → state={row['state']}")
info(out.strip())

# ══════════════════════════════════════════════════════════════════════════════
section("Summary")
# ══════════════════════════════════════════════════════════════════════════════

print(f"""
  {GREEN}{BOLD}All 9 verification checks passed.{RESET}

  {BOLD}What was verified:{RESET}
  1. Schema v13  — pid column present in copilot_remote
  2. Lifecycle   — running → done, pid/connect_handle persisted
  3. Stop guard  — finish_copilot_remote is write-once (idempotent)
  4. State model — is_terminal correct for done/failed/stopped/running
  5. CLI output  — Connect:/Resume: suppressed for terminal jobs
  6. PID filter  — bash/script/complete_job/wrong-job excluded from kill list
  7. CWE-117     — sanitize_for_log strips \\n/\\r/\\x00 injection vectors
  8. Slug check  — ambiguous slug rejected with --repo-path guidance
  9. Gateway     — /copilot_remote stop marks job stopped end-to-end

  {DIM}Sandbox cleaned up: {_tmp}{RESET}
""")

import shutil
shutil.rmtree(_tmp, ignore_errors=True)
