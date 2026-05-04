"""State enums and dataclasses for copilot remote jobs."""

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
    # Explicitly stopped (e.g. by operator or hermes copilot stop).
    STOPPED = "stopped"

    @property
    def is_terminal(self) -> bool:
        """Return True if this state is a terminal (non-resumable) state."""
        return self in (
            JobState.DONE,
            JobState.FAILED,
            JobState.STOPPED,
        )

@dataclass
class RepoEntry:
    """A repository entry discovered from the workspace filesystem."""
    slug: str
    path: str
    readme_summary: str = ""
    description: str = ""
    default_branch: str = "main"
