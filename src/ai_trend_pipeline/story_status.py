"""Pure mapping from internal candidate/story states to the five UI statuses.

Standard library only so workflows, services and web code can all import it.
"""

from datetime import timedelta
from enum import StrEnum

STORY_UI_RETENTION = timedelta(hours=48)
DEFAULT_STORY_REVIEW_TIMEOUT_S = 172800

_WRITING_STATES = frozenset(
    {
        "approved",
        "researching",
        "analyzing",
        "writing",
        "checking",
        "media",
    }
)


class PrimaryStoryStatus(StrEnum):
    PENDING = "pending"
    WRITING = "writing"
    REVIEW = "review"
    READY = "ready"
    CLOSED = "closed"


def primary_story_status(value: str) -> PrimaryStoryStatus:
    """Map any candidate or story internal status; unknown values are closed, never an error."""
    if value == "awaiting_review":
        return PrimaryStoryStatus.PENDING
    if value in _WRITING_STATES:
        return PrimaryStoryStatus.WRITING
    if value == "review":
        return PrimaryStoryStatus.REVIEW
    if value == "ready":
        return PrimaryStoryStatus.READY
    return PrimaryStoryStatus.CLOSED
