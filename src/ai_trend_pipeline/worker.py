"""Run the hello worker: `python -m ai_trend_pipeline.worker`."""

import asyncio
import time

from temporalio.client import Client
from temporalio.worker import Worker

from ai_trend_pipeline.hello import TASK_QUEUE, HelloWorkflow, say_hello
from ai_trend_pipeline.temporal_client import connect

CONNECT_TIMEOUT_S = 30.0
RETRY_DELAY_S = 1.0


async def connect_with_retry() -> Client:
    deadline = time.monotonic() + CONNECT_TIMEOUT_S
    while True:
        try:
            return await connect()
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(RETRY_DELAY_S)


async def main() -> None:
    client = await connect_with_retry()
    worker = Worker(
        client, task_queue=TASK_QUEUE, workflows=[HelloWorkflow], activities=[say_hello]
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
