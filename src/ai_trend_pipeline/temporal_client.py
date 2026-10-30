"""Temporal client helpers."""

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE_NAME,
    CANDIDATE_STATUS_ATTRIBUTE_NAME,
)
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE_NAME,
    STORY_CREATED_AT_ATTRIBUTE_NAME,
    STORY_SOURCE_ATTRIBUTE_NAME,
    STORY_STATUS_ATTRIBUTE_NAME,
    STORY_STATUS_DETAIL_ATTRIBUTE_NAME,
    STORY_TITLE_ATTRIBUTE_NAME,
    STORY_UPDATED_AT_ATTRIBUTE_NAME,
)

CANDIDATE_SEARCH_ATTRIBUTES: dict[str, IndexedValueType.ValueType] = {
    CANDIDATE_STATUS_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    CANDIDATE_SOURCE_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
}

STORY_SEARCH_ATTRIBUTES: dict[str, IndexedValueType.ValueType] = {
    **CANDIDATE_SEARCH_ATTRIBUTES,
    STORY_STATUS_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    STORY_TITLE_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    STORY_SOURCE_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    STORY_CANDIDATE_ID_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    STORY_CREATED_AT_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
    STORY_UPDATED_AT_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
    STORY_STATUS_DETAIL_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
}


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, data_converter=pydantic_data_converter)


async def ensure_story_search_attributes(client: Client) -> None:
    """Register the two candidate and seven story attributes; safe on every worker start."""
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=client.namespace,
                search_attributes=STORY_SEARCH_ATTRIBUTES,
            )
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.ALREADY_EXISTS:
            raise


async def ensure_candidate_search_attributes(client: Client) -> None:
    """Backwards-compatible wrapper for earlier callers and tests."""
    await ensure_story_search_attributes(client)
