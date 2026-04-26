---
title: "Copilot Remote Jobs"
description: "Launch, route, and track GitHub Copilot remote sessions from Hermes"
---

# Copilot Remote Jobs

Hermes can start standalone GitHub Copilot remote sessions, record them in the Hermes state database, and hand you reconnect commands so the work can move between terminals or people without losing track of the job.

:::info This is separate from Hermes' Copilot providers
- `provider: copilot` means Hermes uses Copilot-hosted models for Hermes turns.
- `provider: copilot-acp` means Hermes delegates Hermes turns to `copilot --acp`.
- `hermes copilot` and `/copilot_remote` launch separate Copilot remote sessions that Hermes tracks as jobs.
:::

## What Hermes Tracks

- Repo slug and absolute repo path
- Original prompt preview
- Job state: `running`, `done`, or `failed`
- Creation and finish timestamps plus exit code
- An external reconnect handle when Hermes can extract Copilot's remote task ID
- A PTY log file at `~/.hermes/logs/copilot-<job_id>.log`

Job records live in the `copilot_remote` table inside `~/.hermes/state.db`.

## Requirements

- GitHub Copilot CLI installed anywhere on `PATH` visible to the Hermes process
- Copilot CLI authenticated in the environment where you will run `copilot --connect` or `copilot --resume`
- `HERMES_WORKSPACE_PATH` set if you want automatic repo routing
- Workspace layout that matches `repos/<org>/<repo>/README.md` for auto-routing
- A GitHub token or authenticated `gh` CLI if you want Hermes to verify or steer initial prompt delivery after launch. Hermes checks `COPILOT_GITHUB_TOKEN`, then `GH_TOKEN`, then `GITHUB_TOKEN`, then `gh auth token`.

:::tip
If you already know the target repository, pass `--repo` and `--repo-path` to skip routing. `--repo-path` must match the filesystem path visible to Hermes, including Docker or SSH sandboxes.
:::

## Shell Workflow

### Launch with Automatic Routing

```bash
hermes copilot launch "Review the failing webhook tests and fix the regression"
```

Hermes scans `HERMES_WORKSPACE_PATH/repos/`, builds a repo catalog from README content, asks the auxiliary model to choose the best match, records a job, and launches `copilot -i ... --remote` in the selected repo.

### Launch with an Explicit Repo

```bash
hermes copilot launch \
  --repo fridai-backend \
  --repo-path /workspace/repos/proservice/fridai-backend \
  "Patch the EventBridge retry bug and open a clean PR"
```

### Useful Flags

| Flag | Meaning |
|------|---------|
| `--repo <slug>` | Skip routing and target a specific repo |
| `--repo-path <abs-path>` | Required with `--repo`; absolute path to that repo in the active environment |
| `--model <name>` | Ask Copilot CLI to use a specific model |
| `--dry-run` | Validate the workflow without spawning Copilot |
| `--signal-source <name>` | Advanced metadata for the origin of the launch |
| `--signal-ref <value>` | Advanced metadata such as a ticket or webhook reference |

### Inspect Jobs

```bash
hermes copilot list
hermes copilot list --state running
hermes copilot show <job-id>
```

`show` prints the stored repo, prompt preview, exit code, error text, and the best reconnect handle Hermes knows about.

## In-Chat Workflow

The same capability is available inside Hermes chats in both the CLI and the messaging gateway:

```text
/copilot_remote launch Review the failing Slack notification path and fix it
/copilot_remote list
/copilot_remote show <job-id>
```

A bare `/copilot_remote` defaults to `list`.

Hermes also exposes this workflow to the agent as the `copilot_remote` tool. In Slack or another gateway chat, plain requests such as "use Copilot to build a static webpage" can be handled as a tracked Copilot remote job without requiring slash-command syntax. If the target repo is not explicit, Hermes uses the same repo router as `hermes copilot launch`.

## Reconnect and Resume

A successful launch prints commands like:

```text
Connect: copilot --connect=<handle>
Resume:  copilot --resume=<handle>
```

- Use `copilot --connect=<handle>` while the remote session is still running.
- Use `copilot --resume=<handle>` to reopen it later.

When Hermes can extract Copilot's exported remote task ID from Copilot logs, it stores that handle and shows it in `launch` and `show`.

## Prompt Delivery Verification

After spawning the detached Copilot session, Hermes tries to confirm that the initial prompt made it into the remote session. If the remote task is visible but has no user message yet, Hermes attempts one follow-up steer through the Copilot API.

If that verification step fails, Hermes still keeps the job and prints a warning. Treat that warning as "session launched, prompt delivery not fully verified" rather than "launch failed."

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| Routing fails | Set `HERMES_WORKSPACE_PATH`, confirm the `repos/<org>/<repo>` layout, or pass `--repo` and `--repo-path` manually |
| Hermes warns about the remote task ID | Check `~/.hermes/logs/copilot-<job_id>.log`; Hermes may not have been able to read Copilot's exported connect handle |
| Hermes warns about GitHub auth | Provide `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, or authenticate `gh` |
| `copilot` cannot be launched | Make sure the Copilot CLI binary is on `PATH` for the environment where Hermes runs |
| Reconnect commands do not work | Re-run `hermes copilot show <job-id>` and inspect the log file to verify which handle Copilot exported |

## See Also

- [CLI Commands Reference](/docs/reference/cli-commands#hermes-copilot)
- [Slash Commands Reference](/docs/reference/slash-commands)
- [CLI Interface](/docs/user-guide/cli)
- [Environment Variables Reference](/docs/reference/environment-variables#copilot-remote-jobs)