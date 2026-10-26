"""Temporal client helpers."""

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from ai_trend_pipeline.config import Settings


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, data_converter=pydantic_data_converter)
