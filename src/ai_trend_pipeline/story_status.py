"""Pure mapping from internal candidate/story states to the six UI statuses.

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
    PUBLISHED = "published"
    CLOSED = "closed"


def primary_story_status(value: str) -> PrimaryStoryStatus:
    """Map any candidate or story internal status; unknown values are closed, never an error."""
    if value == "awaiting_review":
        return PrimaryStoryStatus.PENDING
    if value in _WRITING_STATES:
        return PrimaryStoryStatus.WRITING
    if value == "review":
        return PrimaryStoryStatus.REVIEW
    if value in ("ready", "publishing", "publish_failed"):
        return PrimaryStoryStatus.READY
    if value == "published":
        return PrimaryStoryStatus.PUBLISHED
    return PrimaryStoryStatus.CLOSED
