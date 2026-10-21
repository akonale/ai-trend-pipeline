"""Hello workflow and activity: the smallest end-to-end Temporal example."""

from datetime import timedelta

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

TASK_QUEUE = "hello-task-queue"


class HelloInput(BaseModel):
    name: str


class HelloOutput(BaseModel):
    greeting: str


@activity.defn
async def say_hello(data: HelloInput) -> HelloOutput:
    return HelloOutput(greeting=f"Hello, {data.name}!")


@workflow.defn
class HelloWorkflow:
    @workflow.run
    async def run(self, data: HelloInput) -> HelloOutput:
        return await workflow.execute_activity(
            say_hello,
            data,
            start_to_close_timeout=timedelta(seconds=10),
        )
