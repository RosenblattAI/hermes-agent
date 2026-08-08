---
name: copilot-review-loop
description: "Autonomously iterate on GitHub Copilot PR reviews — request review, parse comments, delegate fixes to Copilot remote, re-request until clean."
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [github, copilot, review, automation, pr]
    related_skills: [github-pr-workflow, github-code-review, requesting-code-review]
---

# Copilot Review Loop

Autonomously iterate on GitHub Copilot PR reviews until the PR receives a clean
review with zero new comments. Hermes orchestrates the loop; Copilot remote does
the code fixes.

## When to Use

- After opening a PR (manually, via Copilot remote, or via webhook trigger)
- When you want a PR to pass Copilot's automated review without manual intervention
- As part of an end-to-end autonomous PR delivery pipeline

## Input Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `owner` | yes | — | GitHub org/user (e.g. `RosenblattAI`) |
| `repo` | yes | — | Repository name (e.g. `hermes-agent`) |
| `pr_number` | yes | — | Pull request number |
| `max_rounds` | no | `5` | Maximum review-fix iterations before aborting |

## Architecture

```
Hermes (orchestrator)
  │
  ├─ 1. gh api: request Copilot review
  ├─ 2. poll: wait for review to complete (30s intervals, 10min timeout)
  ├─ 3. gh api: fetch review comments
  │     └─ zero comments? → exit SUCCESS ✅
  │
  ├─ 4. copilot_remote: delegate code fixes
  │     └─ Copilot edits code, commits, pushes to PR branch
  │
  ├─ 5. gh api: re-request Copilot review
  └─ 6. loop → back to step 2 (until clean or max_rounds)
```

## Prerequisites

- `gh` CLI authenticated with access to the target repository
- `jq` installed (used by the helper script for JSON parsing)
- `md5sum` installed (standard on GNU/Linux; on macOS use `md5 -r` or install coreutils)
- `copilot_remote` tool available and functional for the target repo (this is a
  built-in Hermes tool registered in the `copilot` toolset — it is NOT a standalone
  CLI command; it is invoked by the Hermes agent via `copilot_remote(action="launch", ...)`)
- PR must be open and not in draft state

---

## Step-by-Step Instructions

Follow these steps exactly. Each step includes the commands to run and how to
interpret the results.

### Step 0: Validate PR State

Before starting the loop, confirm the PR is open and get the branch name.

```bash
# Get PR state and branch
PR_DATA=$(gh api /repos/{owner}/{repo}/pulls/{pr_number} \
  --jq '{state: .state, draft: .draft, branch: .head.ref, mergeable_state: .mergeable_state}')

echo "$PR_DATA"
```

**Checks:**
- `state` must be `"open"` → if not, abort: "PR is not open (state: {state})"
- `draft` must be `false` → if true, abort: "PR is a draft — mark as ready for review first"
- Save `branch` for use in subsequent steps

### Step 1: Capture Baseline Review ID

Before requesting a new review, record the latest Copilot review ID so we can
detect when a *new* review arrives.

```bash
# Get the latest Copilot review ID (may be empty if no prior reviews)
BASELINE_REVIEW_ID=$(gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --jq '[.[] | select(.user.login == "Copilot")] | sort_by(.submitted_at) | last | .id // 0')

echo "Baseline review ID: $BASELINE_REVIEW_ID"
```

### Step 2: Request Copilot Review

```bash
gh api /repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers \
  -X POST -f 'reviewers[]=Copilot'
```

**Success:** Response returns 201. Note: the `requested_reviewers` array in the
response body will be empty — this is a known quirk with bot reviewers. A 201
status confirms the request was accepted.

**Known issue:** The API request may not reliably trigger Copilot re-reviews on
subsequent rounds. If polling times out after a re-request, the agent should
notify the user to manually request the review from the GitHub UI as a fallback.

**Error handling:**
- `422 Unprocessable Entity` → Copilot may already be requested or not available on this repo. Check the error message.
- `404` → PR doesn't exist or you don't have access.
- `403` with rate limit → wait and retry (see Guardrails section).

### Step 3: Poll for Review Completion

Copilot reviews typically take 1–5 minutes. Poll with 30-second intervals.

```bash
# Poll loop — run this repeatedly with 30s sleep between attempts
NEW_REVIEW=$(gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --jq "[.[] | select(.user.login == \"Copilot\" and (.id > $BASELINE_REVIEW_ID))] | sort_by(.submitted_at) | last // empty")

echo "$NEW_REVIEW"
```

**Polling rules:**
- Wait 30 seconds before the first poll (give Copilot time to start)
- Poll every 30 seconds after that
- **Timeout after 10 minutes** (20 polls). If no new review appears:
  - Notify: "⏰ Copilot review timed out after 10 minutes. Check the PR manually."
  - Exit the loop

**When a new review appears:**
- Extract `REVIEW_ID` from the response: `.id`
- Extract `REVIEW_STATE` from the response: `.state`
- Regardless of `state` (`APPROVED`, `COMMENTED`, or `CHANGES_REQUESTED`), always
  proceed to Step 4 to check the actual inline comment count. An `APPROVED` review
  can still contain inline comments, so never skip the comment check based on state alone.

### Step 4: Parse Review Comments

Fetch the inline comments from the new review.

```bash
# Get all comments for this specific review
COMMENTS=$(gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}/comments \
  --jq '.[] | {path, line: .original_line, body, diff_hunk}')

# Count comments
COMMENT_COUNT=$(gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}/comments \
  --jq 'length')

echo "Review has $COMMENT_COUNT inline comments"
```

**Zero comments:** If `COMMENT_COUNT` is 0, this is a clean review (Copilot submitted
a review with no inline feedback). Exit the loop with SUCCESS.

**Format comments for Copilot remote prompt:**

For each comment, create a structured block:

````
## Review Comment {n}/{total}
**File:** `{path}`
**Line:** {line}
**Copilot says:** {body}
**Diff context:**
```diff
{diff_hunk}
```
````

Concatenate all blocks into a single `FORMATTED_COMMENTS` string.

### Step 5: Delegate Fixes to Copilot Remote

Construct a prompt and launch `copilot_remote`.

> **Note:** `copilot_remote` is a built-in Hermes agent tool (registered in the
> `copilot` toolset at runtime). It is NOT a CLI command or script in this repo.
> The Hermes agent invokes it programmatically during conversation. If `copilot_remote`
> is unavailable, fall back to `delegate_task` with `toolsets=["terminal", "file"]`
> and pass the same prompt — the subagent can clone the repo and apply fixes directly.

**Prompt template:**

```
You are working on PR #{pr_number} in {owner}/{repo} on branch `{branch}`.

A GitHub Copilot code review has flagged the following issues. Address EVERY
comment with an actual code change — do not dismiss, skip, or just add code
comments. Fix the underlying issue each comment raises.

After making all fixes, commit with message:
"fix: address copilot review comments (round {round})"

Then push to the branch `{branch}`.

--- REVIEW COMMENTS ---

{formatted_comments}
```

**Launch:**

```python
copilot_remote(
    action="launch",
    repo="{repo}",           # e.g. "hermes-agent"
    prompt="<constructed prompt above>"
)
```

**After launch, poll for completion:**

```python
# Poll every 30 seconds
copilot_remote(action="show", job_id="{job_id}")
```

- Wait for `state: "done"` → proceed to next round
- If `state: "failed"` → notify user and exit loop:
  "❌ Copilot remote failed on round {round}. Manual intervention needed."

### Step 6: Loop Back

After Copilot remote completes successfully:

1. Increment `round` counter
2. Check if `round >= max_rounds` → if yes, exit with MAX_ROUNDS notification
3. Check PR is still open (Step 0 validation)
4. Go back to Step 1 (capture new baseline, request review, poll, parse, fix)

---

## Notification Messages

Send these to the user at each stage (via Slack or whatever delivery target
is active):

| Event | Message |
|-------|---------|
| Loop start | 🔄 Starting Copilot review loop for PR #{pr} ({owner}/{repo}), max {max_rounds} rounds |
| Review requested | 🔍 Round {round}/{max_rounds}: Copilot review requested, polling... |
| Comments found | 📝 Round {round}: Copilot raised {n} comments. Delegating fixes to Copilot remote... |
| Fixes delegated | 🔧 Round {round}: Copilot remote job {job_id} launched. Waiting for completion... |
| Fixes complete | ✅ Round {round}: Fixes pushed. Re-requesting Copilot review... |
| Clean review | 🎉 PR #{pr} passed Copilot review after {round} round(s)! Ready for human merge approval. |
| Timeout | ⏰ Copilot review timed out after 10 minutes on round {round}. Check PR manually. |
| Job failed | ❌ Copilot remote failed on round {round}. Manual intervention needed. |
| Max rounds | ⚠️ Max rounds ({max_rounds}) reached. PR #{pr} still has review comments. Remaining: {summary} |

---

## Guardrails

### Max Iteration Cap

Default 5 rounds. If Copilot keeps raising new comments after 5 fix rounds,
something structural is wrong — a human should look.

### Duplicate Comment Detection

Track comments across rounds by hashing `(path, line, body)`. If more than 50%
of comments in a new round are duplicates of the previous round's comments, abort:

"🔁 Copilot is raising the same issues repeatedly. The fixes aren't addressing
the root cause. Manual review needed."

### PR State Checks

Before each round, verify:
- PR is still open (`state: "open"`)
- PR is mergeable (`mergeable_state` is not `"dirty"` — which means conflicts)

If merge conflicts are detected:
"⚠️ PR #{pr} has merge conflicts. Resolve conflicts before continuing the review loop."

### API Rate Limiting

If any `gh api` call returns 403 with rate limit headers:
1. Wait 30 seconds, then retry
2. If still rate-limited, wait 60 seconds (doubling — exponential backoff)
3. Third retry after 120 seconds
4. If still rate-limited after 3 retries, abort with notification

### Copilot Remote Failure

If `copilot_remote` returns `state: "failed"`:
1. Retry once (launch a new job with the same prompt)
2. If the retry also fails, abort with error details

### Non-Copilot Reviews

Only process reviews where `user.login == "Copilot"`. Ignore human reviews,
other bots, and GitHub Actions check annotations.

---

## Example Invocation

### Manual (Slack/CLI)

```
Run the copilot-review-loop skill for PR #42 in RosenblattAI/hermes-agent
with max 3 rounds.
```

### Webhook (Automatic)

Triggered by GitHub `pull_request.opened` webhook → Hermes receives the event,
loads this skill, and starts the loop automatically. See the `webhook-subscriptions`
skill for setup.

---

## Verification Checklist

After running the loop, verify:
- [ ] All Copilot review comments were addressed with code changes
- [ ] The final Copilot review has zero new inline comments
- [ ] All commits are on the correct PR branch
- [ ] PR CI checks are still passing (the loop doesn't check CI — that's a separate concern)
