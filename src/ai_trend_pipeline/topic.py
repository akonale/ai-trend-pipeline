"""Activity that asks Claude to pick a chartable topic from HN stories."""

import os

import anthropic
from anthropic.types import ToolParam, ToolUseBlock
from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.hn import HNStory

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 1024
TOOL_NAME = "pick_topic"
SYSTEM_PROMPT = (
    "You are given current Hacker News stories. Pick one topic from them that could be "
    "charted with public country or time-series data (for example from Our World in Data). "
    "Explain why in one or two sentences."
    f" Respond only by calling the {TOOL_NAME} tool."
)


class PickTopicInput(BaseModel):
    stories: list[HNStory] = Field(min_length=1)


class PickTopicOutput(BaseModel):
    topic: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def make_anthropic_client() -> anthropic.AsyncAnthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0)


def _format_stories(stories: list[HNStory]) -> str:
    return "\n".join(
        f"id={s.id} | title={s.title} | url={s.url or 'none'} | "
        f"score={s.score} | comments={s.comments}"
        for s in stories
    )


class TopicActivities:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @activity.defn
    async def pick_topic(self, data: PickTopicInput) -> PickTopicOutput:
        tool: ToolParam = {
            "name": TOOL_NAME,
            "description": "Report the chosen topic and why it can be charted.",
            "input_schema": PickTopicOutput.model_json_schema(),
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": _format_stories(data.stories)}],
        )
        for block in response.content:
            if isinstance(block, ToolUseBlock) and block.name == TOOL_NAME:
                return PickTopicOutput.model_validate(block.input)
        raise ApplicationError(
            f"Model response contained no {TOOL_NAME} tool call", non_retryable=True
        )
