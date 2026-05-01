---
applyTo: "**"
description: >
  Patch-authoring guidance for changes inside the hermes-agent fork. Derived
  from the canonical Rosenblatt standard at the monorepo level and tailored to
  hermes-agent's extension points (plugins, tools, skills, toolsets, env-var
  config), code style, and file layout. Use for every PR that modifies this
  fork's source.
canonical_hash: c5dd4c5ac8769144
canonical_source: rosenblatt monorepo — docs/standards/contribution/upstream-fork-contribution.md
generated_by: rosenblatt monorepo — .claude/skills/fork-instruction-sync
generated_at: 2026-05-01T17:30:00Z
---

> **Generated file — do not edit by hand.** Produced by the `fork-instruction-sync` skill in the Rosenblatt monorepo from the canonical standard at `docs/standards/contribution/upstream-fork-contribution.md`. Edits made directly here will be overwritten on the next sync. To change the guidance, edit the canonical doc and re-run the skill. Tier-routing rules (T1/T2/T3) live in the monorepo's `upstream-vs-harness.instructions.md` — this file only covers *how to write* a patch once you've decided it belongs in this fork.

# Contributing patches to hermes-agent

You are writing a patch that will land in the Rosenblatt fork of `NousResearch/hermes-agent`. Every line you change is a line a future maintainer (often you) will have to re-resolve when upstream evolves. The principles below all reduce to one thing: **make patches that survive upstream evolution with the least possible friction**.

## The five principles, in priority order

### 1. Use upstream's extensibility seams before patching core

`hermes-agent` has a deep extension surface. Before modifying any existing file, check whether one of these mechanisms fits your use case:

- **Tools registry** (`tools/registry.py`). Tools are self-registering — each tool file calls `registry.register(name=..., toolset=..., schema=..., handler=..., check_fn=...)` at import time. Adding a new tool is a new file in `tools/` plus one line in `model_tools.py`'s `_modules` list. **Never patch an existing tool when you can add a new one.**
- **Toolsets** (`toolsets.py`). Tools are grouped into named bundles (`web`, `terminal`, `file`, `browser`, etc.) that can be enabled per platform. Add a toolset entry rather than hardcoding tool lists at call sites.
- **Bundled skills** (`skills/<category>/<skill>/SKILL.md`). Most "new capabilities" should be skills, not tools or core code — see `CONTRIBUTING.md`'s "Should it be a Skill or a Tool?" section. A skill is a SKILL.md plus optional `scripts/` and `references/`. Zero source patches required.
- **Optional skills** (`optional-skills/`). Same structure as `skills/` but ship un-activated; users opt in via `hermes skills install`. Use this for skills that are official-quality but not universal.
- **Plugins — context engines** (`plugins/context_engine/<name>/`). Discoverable subdirectories implementing the `ContextEngine` ABC. Selected via `context.engine` in `config.yaml`. One active at a time. See `plugins/context_engine/__init__.py` for the discovery contract.
- **Plugins — memory providers** (`plugins/memory/<name>/`). Discoverable subdirectories implementing the `MemoryProvider` ABC. Bundled providers live in-tree; user-installed providers live in `$HERMES_HOME/plugins/`. Selected via `memory.provider` in `config.yaml`. See `plugins/memory/__init__.py`.
- **Plugins — example-dashboard** (`plugins/example-dashboard/`). A reference implementation of a dashboard plugin — copy as the starting template for new dashboard-style plugins.
- **Project plugins gate** (`HERMES_ENABLE_PROJECT_PLUGINS`). Whole-feature gating via env var; the env-var-gating pattern is the canonical way to keep an integration default-off and upstream-acceptable.
- **Env-var configuration**. Hermes reads ~30+ `HERMES_*` env vars (gateway, agent, session, platform, container behavior, timezone, etc.) plus dozens of provider-specific vars (`ANTHROPIC_*`, `API_SERVER_*`, `AI_GATEWAY_*`). If your patch's behavior can be flipped on/off or parameterized, add a new `HERMES_*` env var read in the relevant module rather than hardcoding. Document it in `cli-config.yaml.example` and `website/docs/user-guide/configuration.md` if user-facing.
- **Gateway platform adapters** (`gateway/platforms/`). Each messaging platform is a self-contained adapter inheriting from `gateway/platforms/base.py`. New platform support is a new file, not a patch to existing platforms.
- **Terminal execution backends** (`tools/environments/`). Backends (local, docker, ssh, singularity, modal, daytona) all subclass `BaseEnvironment`. New execution targets go here as new files.
- **Provider abstraction**. Hermes works with any OpenAI-compatible API. Provider resolution lives in `hermes_cli/auth.py` and `agent/auxiliary_client.py`. Custom providers should plug in via the existing resolution flow, not a parallel code path.

If your change can be expressed as one of the above, it should be — even when a direct edit would be slightly shorter. The verbosity is paid once; the merge debt is paid forever.

If after a thorough check no extension point fits, that's a real signal. Consider opening a T1 PR upstream that adds the extension point you need, then implementing your feature against it. Two PRs is more work upfront but produces a clean upstream contribution and a zero-merge-debt local implementation.

### 2. Minimal diff principle

Every patch is the **smallest change that achieves the stated goal**. Specifically:

- **No reformatting** of existing code, even if it offends your style sensibilities. The upstream file's style is the contract.
- **No import reordering** unless your change requires a new import.
- **No whitespace-only changes**, including trailing-whitespace cleanup or line-ending normalization.
- **No "while I'm here" refactors.** Open a separate T1 PR if the refactor is genuinely valuable.
- **No dependency upgrades** bundled with feature work. `pyproject.toml` dependency bumps are their own T1 PR with their own review (Hermes pins to known-good ranges to limit supply-chain attack surface — bumps need careful review).
- **No test-framework changes** bundled with feature work. New tests using `pytest` are fine; switching frameworks is not.

A reviewer should be able to read your diff and answer "what does this change do?" in one sentence. If they can't, the diff is too big.

### 3. Modularity / isolation

When you must add Rosenblatt-specific code:

- **Prefer new files** over editing existing ones. A new file is a clean addition; edits to existing files are merge-conflict surface.
- **When you must edit an existing file**, change the minimum surface and delegate to a new file. Pattern: add one import + one call site that dispatches into your new module.
- **Avoid editing the high-churn files below** unless you genuinely have no other seam. These files change frequently upstream and any patch in them will conflict on most upstream merges:
  - `run_agent.py`
  - `gateway/run.py`
  - `cli.py`
  - `hermes_cli/main.py`, `hermes_cli/config.py`, `hermes_cli/setup.py`, `hermes_cli/models.py`, `hermes_cli/gateway.py`
  - `tools/terminal_tool.py`
  - `agent/auxiliary_client.py`
  - `gateway/platforms/base.py`, `gateway/platforms/telegram.py`, `gateway/platforms/discord.py`
  - `cron/scheduler.py`
  - `website/docs/user-guide/configuration.md`

  For changes that need to *invoke* logic from these files, add your logic in a new file under `tools/`, `plugins/`, `agent/`, or `gateway/platforms/` and add the **smallest possible** dispatch hook (one import + one call) into the high-churn file.
- **Group related patches into the smallest number of files.** One patched file with five edits is easier to merge than five patched files with one edit each.

### 4. Match upstream conventions

Inside `upstream/hermes-agent/`, **upstream's conventions win** — full stop:

- **Style:** PEP 8 with practical exceptions; line length is not strictly enforced. No formatter is run in CI (no Black, no Ruff format) — match the surrounding file's apparent style. Don't run a formatter across files you didn't otherwise touch.
- **Comments:** Only when explaining non-obvious intent, trade-offs, or API quirks. Don't narrate what the code does.
- **Error handling:** Catch specific exceptions. Use `logger.warning()` / `logger.error()` (Python `logging`, not `print`). Pass `exc_info=True` for unexpected errors.
- **Cross-platform:** Never assume Unix. Test against Windows + macOS + Linux paths and processes wherever practical. The upstream project values cross-platform portability highly.
- **Tests:** Use `pytest`. Tests live under `tests/` mirroring the source layout (`tests/agent/`, `tests/cli/`, `tests/gateway/`, etc.). Mark tests requiring external services with `@pytest.mark.integration`. CI runs `pytest tests/ -q --ignore=tests/integration --ignore=tests/e2e -n auto` — keep your tests fast and parallelizable. The default `addopts` excludes the `integration` marker.
- **Type annotations:** Used inconsistently across the codebase — match the surrounding file. Don't introduce `mypy` strictness or annotation backfills as part of feature work.
- **No pre-commit, no linter, no formatter in CI** — the only enforced check is `pytest` + a contributor check workflow + a docs site check + a supply-chain audit. This means the burden of style consistency is on you as the patch author. Read the file you're editing first.

### 5. Make patches conspicuous

Every Rosenblatt patch must be **trivially identifiable** by anyone (human or AI) doing a future upstream merge.

**Commit hygiene (monorepo-wide policy):**
- Prefix every Rosenblatt-only commit with `[rosenblatt]`. This is the standard across all forks — do not vary it. `git log --grep '\[rosenblatt\]'` must reliably surface every internal patch.
- Each commit message explains **why** the patch exists, not just what it does. The future merge-conflict resolver needs the reasoning.
- Reference the upstream issue/PR (if T1-pending) or the internal ticket / Slack discussion (if T2-permanent).
- One logical change per commit.

**Code markers:**
- For non-trivial in-source patches, add sentinel comments marking the start and end of Rosenblatt-modified regions:
  ```python
  # === ROSENBLATT PATCH START: short description ===
  # Reason: why this patch exists
  # Upstream: link or "internal-only"
  ...patched code...
  # === ROSENBLATT PATCH END ===
  ```
- Sentinel comments are **mandatory** for any patch larger than ~10 lines or any patch in a high-churn file (see Principle 3).
- For one-line edits, an inline `# ROSENBLATT: <reason>` is sufficient.

These markers serve three purposes: (a) `git log -G "ROSENBLATT"` and `grep -r ROSENBLATT` find every patch in seconds, (b) merge-conflict resolvers immediately see the patch boundary, and (c) periodic audits to drop accepted-upstream patches become trivial.

## Forward-port mindset

Write every patch assuming it will be rebased on a future upstream version you haven't seen yet:

- **Depend on documented public APIs**, not private internals. If you must use a private (leading-underscore) function, comment why and what the public alternative would be. The tools registry, plugin discovery functions, env vars, and `BaseEnvironment` / `ContextEngine` / `MemoryProvider` ABCs are the safest dependencies.
- **Pin upstream version assumptions** in the commit message ("requires hermes-agent ≥ 0.10 because the X plugin contract landed in 0.10.0") so a future merger knows when the patch can be re-evaluated.
- **Don't copy upstream code into your patch.** If you find yourself duplicating a function to wrap it, you're creating a future drift bug. Use a hook, subclass, or registry registration instead.

## Merge-debt awareness

Every line of fork patch is a permanent tax on every future upstream sync. Budget accordingly:

- **Audit `rosenblatt/main` patches** at every upstream version bump. For each patch: has upstream merged this? Is it still needed? Can it now be replaced by a newly-existing extension point or env var?
- **Drop patches as soon as upstream accepts the equivalent T1 PR.** The next `git merge upstream/main` is the natural place — accept upstream's version and remove your patch.
- **Re-bias toward harness (T3) at every audit.** Hermes adds new env vars and config options frequently. A patch that was necessary six months ago may now be expressible as a `HERMES_*` env var read by `entrypoint-hermes.sh` in the monorepo harness.

## When this guidance bends

A few cases override the principles above:

- **Security fixes.** Ship immediately, minimize later. Open the upstream PR in parallel.
- **Build breaks.** If upstream is broken on `main` and we need a working build, patch first, file the upstream issue second. Drop the patch as soon as upstream resolves the underlying issue.
- **`.gitmodules` rewrites for transitive forks** (e.g., the existing rewrite that points `tinker-atropos` at our fork). Structurally Rosenblatt-only, can't go upstream, no extensibility seam applies.

## This fork at a glance

| Aspect | Value |
|---|---|
| Upstream | [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) |
| Language | Python ≥ 3.11 |
| Package manager | `uv` |
| Test framework | `pytest` (with `pytest-asyncio`, `pytest-xdist`); tests under `tests/` mirror source layout |
| Lint / format | None enforced. PEP 8 informally, no Black/Ruff/mypy in CI |
| CI workflows | `tests.yml`, `contributor-check.yml`, `docker-publish.yml`, `nix.yml`, `supply-chain-audit.yml`, `docs-site-checks.yml`, `skills-index.yml`, `deploy-site.yml` |
| Public extension surfaces | Tools registry, toolsets, bundled & optional skills, context-engine plugins, memory-provider plugins, gateway platform adapters, terminal execution backends, `HERMES_*` env vars |
| Contributor docs | `CONTRIBUTING.md` (root), `AGENTS.md` (AI assistant guide), `website/docs/` |
| User config dir | `~/.hermes/` (config.yaml, .env, skills/, memories/, state.db, sessions/, cron/) |
| User plugin dir | `$HERMES_HOME/plugins/` (for memory providers; bundled providers take precedence on name collisions) |

## Cross-references

- **Canonical contribution standard:** `docs/standards/contribution/upstream-fork-contribution.md` in the Rosenblatt monorepo.
- **Tier classification (T1 / T2 / T3):** `.github/instructions/upstream-vs-harness.instructions.md` in the Rosenblatt monorepo.
- **Submodule sync mechanics:** `upstream/README.md` in the Rosenblatt monorepo.
- **Upstream contributor guide:** `CONTRIBUTING.md` and `AGENTS.md` in this repo.
