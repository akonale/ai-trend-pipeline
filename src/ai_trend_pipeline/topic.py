"""Activity that asks Claude to pick a chartable topic from HN stories."""

import os

import anthropic
from anthropic.types import ToolParam, ToolUseBlock
from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.hn import HNStory
from ai_trend_pipeline.owid import CATALOG

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 1024
TOOL_NAME = "pick_topic"
NOT_IN_CATALOG_ERROR_TYPE = "TopicNotInCatalog"
CATALOG_TITLES = [d.title for d in CATALOG]
SYSTEM_PROMPT = (
    "You are given current Hacker News stories. Choose exactly one of the following chartable "
    "topics, the one that best fits the stories:\n"
    + "\n".join(f"- {title}" for title in CATALOG_TITLES)
    + "\nCopy the chosen title verbatim into `topic`. In `reason`, say in one or two sentences "
    "how it links to the news."
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
        schema = PickTopicOutput.model_json_schema()
        schema["properties"]["topic"]["enum"] = list(CATALOG_TITLES)
        tool: ToolParam = {
            "name": TOOL_NAME,
            "description": "Report the chosen topic and why it can be charted.",
            "input_schema": schema,
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
                picked = PickTopicOutput.model_validate(block.input)
                wanted = picked.topic.strip().casefold()
                for dataset in CATALOG:
                    if dataset.title.casefold() == wanted:
                        return PickTopicOutput(topic=dataset.title, reason=picked.reason)
                raise ApplicationError(
                    f"Topic {picked.topic!r} is not in the OWID catalog",
                    type=NOT_IN_CATALOG_ERROR_TYPE,
                    non_retryable=False,
                )
        raise ApplicationError(
            f"Model response contained no {TOOL_NAME} tool call", non_retryable=True
        )
