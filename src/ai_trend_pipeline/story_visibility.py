"""Shared Temporal visibility attributes for candidate and story workflows.

Only bounded metadata belongs here: never story text, evidence, prompts or URLs.
"""

import re
from datetime import datetime
from typing import Any

from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    SearchAttributeUpdate,
    TypedSearchAttributes,
)

MAX_STATUS_DETAIL_CHARS = 200

STORY_STATUS_ATTRIBUTE_NAME = "StoryStatus"
STORY_TITLE_ATTRIBUTE_NAME = "StoryTitle"
STORY_SOURCE_ATTRIBUTE_NAME = "StorySource"
STORY_CANDIDATE_ID_ATTRIBUTE_NAME = "StoryCandidateId"
STORY_CREATED_AT_ATTRIBUTE_NAME = "StoryCreatedAt"
STORY_UPDATED_AT_ATTRIBUTE_NAME = "StoryUpdatedAt"
STORY_STATUS_DETAIL_ATTRIBUTE_NAME = "StoryStatusDetail"

STORY_STATUS_ATTRIBUTE = SearchAttributeKey.for_keyword(STORY_STATUS_ATTRIBUTE_NAME)
STORY_TITLE_ATTRIBUTE = SearchAttributeKey.for_keyword(STORY_TITLE_ATTRIBUTE_NAME)
STORY_SOURCE_ATTRIBUTE = SearchAttributeKey.for_keyword(STORY_SOURCE_ATTRIBUTE_NAME)
STORY_CANDIDATE_ID_ATTRIBUTE = SearchAttributeKey.for_keyword(STORY_CANDIDATE_ID_ATTRIBUTE_NAME)
STORY_CREATED_AT_ATTRIBUTE = SearchAttributeKey.for_datetime(STORY_CREATED_AT_ATTRIBUTE_NAME)
STORY_UPDATED_AT_ATTRIBUTE = SearchAttributeKey.for_datetime(STORY_UPDATED_AT_ATTRIBUTE_NAME)
STORY_STATUS_DETAIL_ATTRIBUTE = SearchAttributeKey.for_keyword(STORY_STATUS_DETAIL_ATTRIBUTE_NAME)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def bounded_detail(value: str | None) -> str | None:
    """Strip, collapse whitespace, remove control characters and cap at 200 characters."""
    if value is None:
        return None
    cleaned = _CONTROL_CHARS.sub("", value)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned[:MAX_STATUS_DETAIL_CHARS]


def story_start_attributes(
    status: str,
    title: str,
    source: str,
    candidate_id: str,
    created_at: datetime,
    updated_at: datetime,
) -> TypedSearchAttributes:
    """The bounded metadata every story (and candidate) row carries at start."""
    return TypedSearchAttributes(
        [
            SearchAttributePair(STORY_STATUS_ATTRIBUTE, status),
            SearchAttributePair(STORY_TITLE_ATTRIBUTE, title),
            SearchAttributePair(STORY_SOURCE_ATTRIBUTE, source),
            SearchAttributePair(STORY_CANDIDATE_ID_ATTRIBUTE, candidate_id),
            SearchAttributePair(STORY_CREATED_AT_ATTRIBUTE, created_at),
            SearchAttributePair(STORY_UPDATED_AT_ATTRIBUTE, updated_at),
        ]
    )


def story_status_updates(
    status: str, updated_at: datetime, detail: str | None = None
) -> list[SearchAttributeUpdate[Any]]:
    """The upsert payload for one transition: status and time, plus a sanitized detail."""
    # The update type is generic per value type; this list deliberately mixes keyword and datetime.
    updates: list[SearchAttributeUpdate[Any]] = [
        STORY_STATUS_ATTRIBUTE.value_set(status),
        STORY_UPDATED_AT_ATTRIBUTE.value_set(updated_at),
    ]
    cleaned = bounded_detail(detail)
    if cleaned is not None:
        updates.append(STORY_STATUS_DETAIL_ATTRIBUTE.value_set(cleaned))
    return updates
