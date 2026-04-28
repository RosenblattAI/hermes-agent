# Jira E2E Architecture — POC Plan

## Problem

The `copilot_jobs` subsystem can launch and track Copilot remote sessions, but lacks the
architectural primitives needed for a reliable end-to-end Jira-driven workflow:

- No wall-clock timeout on running jobs (a stuck session runs forever)
- `connect_id` (the Copilot cloud relay task ID) is resolved but not persisted in the DB
- No Jira issue linkage on jobs
- No retry/resumability provenance tracking
- No lifecycle hook slots for merge-gate and post-task validation callbacks
- `JobState` enum has no `timed_out` or `cancelled` states

## Deferred

- Jira actionability rubric (comment quality, issue routing scoring)
- HTTP hook delivery implementation (only schema + DB methods in POC)
- PR diff-quality validation logic
- Full Jira webhook ingest flow

---

## Architecture: POC Implementation Slice (landed in Schema v13)

### Schema v13 (`hermes_state.py`, migrations v10–v13)

New columns on `copilot_jobs`:
| Column | Type | Purpose |
|---|---|---|
| `connect_id` | TEXT | Persisted Copilot cloud relay task ID (`--connect` handle) |
| `jira_issue_key` | TEXT | Source Jira issue (e.g. `"PROJ-42"`) |
| `deadline_at` | REAL | Unix timestamp; NULL = no timeout |
| `retry_of` | TEXT | FK → original job (resumability provenance) |
| `retry_count` | INTEGER | Times this original has been retried |

New table: `copilot_job_hooks`
- Rows: `id`, `job_id`, `hook_type` (`merge_gate`/`post_task`), `hook_url`, `hook_payload`, `state`, `fired_at`, `response_code`, `error_text`, `created_at`

### `copilot_jobs/models.py`

- `JobState.TIMED_OUT`, `JobState.STOPPED` added; `is_terminal` property
  *(Note: the DB stores these as the lowercase strings `"timed_out"` and `"cancelled"`.)*
- `HookType` enum (`MERGE_GATE`, `POST_TASK`)
- `HookState` enum (`PENDING`, `FIRED`, `FAILED`, `SKIPPED`)

### New DB methods (`hermes_state.py`)

| Method | Purpose |
|---|---|
| `update_copilot_job_connect_id(job_id, connect_id)` | Persist cloud relay handle post-launch |
| `expire_timed_out_jobs(now)` | Sweep + mark `timed_out`; returns expired IDs |
| `retry_copilot_job(original_id, new_id)` | Create retry row; bump original `retry_count` |
| `register_job_hook(...)` | Register merge-gate or post-task webhook |
| `get_pending_hooks(job_id, hook_type)` | Query pending hooks |
| `fire_job_hook(hook_id, response_code)` | Mark hook fired/failed |
| `skip_job_hooks(job_id, hook_type)` | Mark pending hooks skipped (on cancel) |

### `copilot_jobs/launcher.py`

- `launch_copilot` accepts optional `db` parameter; persists `connect_id` after resolution
- `_log_dir()` now uses `get_hermes_home()` instead of hardcoded `~/.hermes`

---

## Future Work

1. **Timeout enforcer**: cron job or gateway background task calling `expire_timed_out_jobs` periodically
2. **Retry orchestrator**: watch for `timed_out`/`failed` + `retry_count < N`, call `retry_copilot_job` + re-launch
3. **Hook delivery**: HTTP client that calls registered URLs on state transitions
4. **Merge gate integration**: on PR-open webhook → fire `merge_gate` hooks, block merge until response
5. **Post-task validation**: on `done` state → fire `post_task` hooks, parse response for pass/fail
6. **Jira ingest**: webhook adapter in `gateway/platforms/` that creates jobs from Jira issue events
7. **Jira actionability rubric**: score incoming issues before dispatching
