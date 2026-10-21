"""Temporal client helpers."""

import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter


def temporal_address() -> str:
    return os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


async def connect() -> Client:
    return await Client.connect(temporal_address(), data_converter=pydantic_data_converter)
