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

CANDIDATE_SEARCH_ATTRIBUTES: dict[str, IndexedValueType.ValueType] = {
    CANDIDATE_STATUS_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    CANDIDATE_SOURCE_ATTRIBUTE_NAME: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
}


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, data_converter=pydantic_data_converter)


async def ensure_candidate_search_attributes(client: Client) -> None:
    """Register the candidate keyword attributes; safe to call on every worker start."""
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=client.namespace,
                search_attributes=CANDIDATE_SEARCH_ATTRIBUTES,
            )
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.ALREADY_EXISTS:
            raise
