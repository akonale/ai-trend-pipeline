"""Start HelloWorkflow: `python -m ai_trend_pipeline.start_hello`."""

import asyncio

from ai_trend_pipeline.config import load_settings
from ai_trend_pipeline.hello import TASK_QUEUE, HelloInput, HelloWorkflow
from ai_trend_pipeline.temporal_client import connect


async def main() -> None:
    client = await connect(load_settings())
    result = await client.execute_workflow(
        HelloWorkflow.run,
        HelloInput(name="World"),
        id="hello-workflow",
        task_queue=TASK_QUEUE,
    )
    print(result.greeting)


if __name__ == "__main__":
    asyncio.run(main())
