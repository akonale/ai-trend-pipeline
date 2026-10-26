"""Tests for the hello workflow, activity and Temporal client helpers."""

import uuid

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.hello import HelloInput, HelloOutput, HelloWorkflow, say_hello


@pytest.mark.asyncio
async def test_say_hello_activity() -> None:
    result = await ActivityEnvironment().run(say_hello, HelloInput(name="World"))
    assert result == HelloOutput(greeting="Hello, World!")


@pytest.mark.asyncio
async def test_hello_workflow_returns_greeting() -> None:
    task_queue = f"test-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[HelloWorkflow],
            activities=[say_hello],
        ):
            result = await env.client.execute_workflow(
                HelloWorkflow.run,
                HelloInput(name="World"),
                id=f"test-{uuid.uuid4()}",
                task_queue=task_queue,
            )
    assert isinstance(result, HelloOutput)
    assert result.greeting == "Hello, World!"
