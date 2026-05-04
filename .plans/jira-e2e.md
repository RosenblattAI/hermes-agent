# Jira E2E Architecture — POC Plan

## Problem (pre-v13 gaps that this POC addresses)

The `copilot_remote` subsystem could launch and track Copilot remote sessions,
but lacked the architectural primitives needed for a reliable end-to-end
Jira-driven workflow.  This PR extends `copilot_remote/` and adds schema v13
to fill those gaps:

- No wall-clock timeout on running sessions (a stuck session ran forever)
- `connect_handle` (the Copilot cloud relay task ID) was resolved but not persisted in the DB
- No Jira issue linkage on sessions
- No retry/resumability provenance tracking
- No lifecycle hook slots for merge-gate and post-task validation callbacks
- `JobState` enum lacked `timed_out` and `stopped` states

## Deferred

- Jira actionability rubric (comment quality, issue routing scoring)
- HTTP hook delivery implementation (only schema + DB methods in POC)
- PR diff-quality validation logic
- Full Jira webhook ingest flow

---

## Architecture: POC Implementation Slice (landed in Schema v13)

### Schema v13 (`hermes_state.py`, migrations v10–v13)

New columns on `copilot_remote`:
| Column | Type | Purpose |
|---|---|---|
| `connect_handle` | TEXT | Persisted Copilot cloud relay task ID (`--connect` handle) |
| `jira_issue_key` | TEXT | Source Jira issue (e.g. `"PROJ-42"`) |
| `deadline_at` | REAL | Unix timestamp; NULL = no timeout |
| `retry_of` | TEXT | FK → original session (resumability provenance) |
| `retry_count` | INTEGER | Times this original has been retried |
| `pid` | INTEGER | OS process ID of the launched copilot process |

New table: `copilot_remote_hooks`
- Rows: `id`, `remote_id`, `hook_type` (`merge_gate`/`post_task`), `hook_url`, `hook_payload`, `state`, `fired_at`, `response_code`, `error_text`, `created_at`

Schema v13 adds new columns to `copilot_remote` and creates `copilot_remote_hooks`.
No legacy data migration is required — `copilot_jobs` never shipped in production.

### `copilot_remote/models.py`

- `JobState.TIMED_OUT`, `JobState.STOPPED` added; `is_terminal` property
  *(Note: the DB stores these as the lowercase strings `"timed_out"` and `"stopped"`.)*
- `HookType` enum (`MERGE_GATE`, `POST_TASK`)
- `HookState` enum (`PENDING`, `FIRED`, `FAILED`, `SKIPPED`)

### New DB methods (`hermes_state.py`)

| Method | Purpose |
|---|---|
| `update_copilot_remote_connect_handle(remote_id, connect_handle)` | Persist cloud relay handle post-launch |
| `update_copilot_remote_pid(remote_id, pid)` | Persist OS process ID post-launch |
| `expire_timed_out_remotes(now)` | Sweep + mark `timed_out`; returns expired IDs |
| `retry_copilot_remote(original_id, new_id)` | Create retry row; bump original `retry_count` |
| `register_remote_hook(...)` | Register merge-gate or post-task webhook |
| `get_pending_remote_hooks(remote_id, hook_type)` | Query pending hooks |
| `fire_remote_hook(hook_id, response_code)` | Mark hook fired/failed |
| `skip_remote_hooks(remote_id, hook_type)` | Mark pending hooks skipped (on cancel) |

### `copilot_remote/launcher.py`

- `connect_id` is persisted via `db.update_copilot_remote_connect_handle()` in `copilot_cmd.py` after `launch_copilot()` returns
- `pid` is persisted via `db.update_copilot_remote_pid()` from `proc.pid` after launch
- `_log_dir()` uses `get_hermes_home()` instead of hardcoded `~/.hermes`

---

## Future Work

1. **Timeout enforcer**: cron job or gateway background task calling `expire_timed_out_remotes` periodically
2. **Retry orchestrator**: watch for `timed_out`/`failed` + `retry_count < N`, call `retry_copilot_remote` + re-launch
3. **Hook delivery**: HTTP client that calls registered URLs on state transitions
4. **Merge gate integration**: on PR-open webhook → fire `merge_gate` hooks, block merge until response
5. **Post-task validation**: on `done` state → fire `post_task` hooks, parse response for pass/fail
6. **Jira ingest**: webhook adapter in `gateway/platforms/` that creates sessions from Jira issue events
7. **Jira actionability rubric**: score incoming issues before dispatching
