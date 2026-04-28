"""State enums and dataclasses for copilot jobs."""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class JobState(str, Enum):
    """Valid states for a copilot job.

    Simplified: copilot sessions are cloud-managed via --remote/--connect,
    so we only track whether we've launched and whether it finished.
    """
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # Wall-clock deadline elapsed before the job finished.
    TIMED_OUT = "timed_out"
    # Explicitly stopped (e.g. by operator or merge-gate veto).
    STOPPED = "stopped"

    @property
    def is_terminal(self) -> bool:
        """Return True if this state is a terminal (non-resumable) state."""
        return self in (
            JobState.DONE,
            JobState.FAILED,
            JobState.TIMED_OUT,
            JobState.STOPPED,
        )


class HookType(str, Enum):
    """Hook types supported by the copilot job lifecycle."""
    # Fired when a child session opens a PR — used to gate merge readiness.
    MERGE_GATE = "merge_gate"
    # Fired after the job reaches a terminal state — used for validation.
    POST_TASK = "post_task"


class HookState(str, Enum):
    """State of a registered lifecycle hook."""
    PENDING = "pending"
    FIRED = "fired"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class RepoEntry:
    """A repository entry discovered from the workspace filesystem."""
    slug: str
    path: str
    readme_summary: str = ""
    description: str = ""
    default_branch: str = "main"
