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

New column added by this PR on `copilot_remote`:
| Column | Type | Purpose |
|---|---|---|
| `pid` | INTEGER | OS process ID of the bash wrapper / PGID leader (not the inner Copilot CLI child PID) |

`connect_handle` (Copilot cloud relay task ID) was already present on
`rosenblatt/main` and is **not** a v13 addition.

Schema v13 adds `pid` to `copilot_remote` (and `connect_handle` was already
present on `rosenblatt/main`).  No legacy data migration is required —
`copilot_remote` never shipped in production.

### `copilot_remote/models.py`

- `JobState.STOPPED` added; `is_terminal` property added
  *(The DB stores state as the lowercase string `"stopped"`.)*

> **Deferred to `feat/copilot-remote-lifecycle-ext`:** `JobState.TIMED_OUT`,
> `HookType` enum (`MERGE_GATE`, `POST_TASK`), `HookState` enum
> (`PENDING`, `FIRED`, `FAILED`, `SKIPPED`).

### New DB methods (`hermes_state.py`)

| Method | Purpose |
|---|---|
| `update_copilot_remote_pid(remote_id, pid)` | Persist bash wrapper / PGID leader PID post-launch |

> **Deferred to `feat/copilot-remote-lifecycle-ext`:**
> `expire_timed_out_remotes`, `retry_copilot_remote`, `register_remote_hook`,
> `get_pending_remote_hooks`, `fire_remote_hook`, `skip_remote_hooks`.
> (`update_copilot_remote_connect_handle` was already on `rosenblatt/main`.)

### `copilot_remote/launcher.py`

- `connect_id` is persisted via `db.update_copilot_remote_connect_handle()` in `copilot_cmd.py` after `launch_copilot()` returns
- `pid` is persisted via `db.update_copilot_remote_pid()` from `proc.pid` (bash wrapper / PGID leader) after launch
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
