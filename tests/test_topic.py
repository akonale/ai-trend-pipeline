"""Tests for the LLM topic-picking activity, using recorded Messages API fixtures."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.hn import HNStory
from ai_trend_pipeline.owid import CATALOG
from ai_trend_pipeline.topic import (
    DEFAULT_MODEL,
    MAX_TOKENS,
    NOT_IN_CATALOG_ERROR_TYPE,
    SYSTEM_PROMPT,
    TOOL_NAME,
    PickTopicInput,
    PickTopicOutput,
    TopicActivities,
    make_anthropic_client,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / name).read_text()))


def _stories() -> list[HNStory]:
    when = datetime(2025, 10, 9, tzinfo=UTC)
    return [
        HNStory(
            id=1,
            title="Story Alpha",
            url="https://a.example",
            score=10,
            by="a",
            time=when,
            comments=3,
        ),
        HNStory(id=2, title="Story Beta", url=None, score=5, by="b", time=when, comments=0),
    ]


def _client(
    fixture: str = "llm_pick_topic_response.json",
    requests: list[dict[str, object]] | None = None,
    status: int = 200,
) -> anthropic.AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or request.url.path != "/v1/messages":
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        if requests is not None:
            requests.append(cast(dict[str, object], json.loads(request.content)))
        if status != 200:
            return httpx2.Response(
                status,
                json={
                    "type": "error",
                    "error": {"type": "api_error", "message": "boom"},
                },
            )
        return httpx2.Response(200, json=_fixture(fixture))

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def _run(client: anthropic.AsyncAnthropic, model: str | None = None) -> PickTopicOutput:
    activities = TopicActivities(client) if model is None else TopicActivities(client, model=model)
    return await ActivityEnvironment().run(
        activities.pick_topic, PickTopicInput(stories=_stories())
    )


@pytest.mark.asyncio
async def test_pick_topic_returns_topic_and_reason() -> None:
    result = await _run(_client())
    expected = cast(
        dict[str, str],
        cast(list[dict[str, object]], _fixture("llm_pick_topic_response.json")["content"])[1][
            "input"
        ],
    )
    assert isinstance(result, PickTopicOutput)
    assert result.topic == expected["topic"]
    assert result.reason == expected["reason"]


@pytest.mark.asyncio
async def test_request_shape() -> None:
    requests: list[dict[str, object]] = []
    await _run(_client(requests=requests))
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == MAX_TOKENS
    tools = cast(list[dict[str, object]], body["tools"])
    assert [t["name"] for t in tools] == [TOOL_NAME] == ["pick_topic"]
    assert body["tool_choice"] == {"type": "auto"}
    messages = cast(list[dict[str, object]], body["messages"])
    assert len(messages) == 1
    text = json.dumps(messages[0]["content"])
    for story in _stories():
        assert story.title in text


@pytest.mark.asyncio
async def test_custom_model_is_sent() -> None:
    requests: list[dict[str, object]] = []
    await _run(_client(requests=requests), model="other-model")
    assert requests[0]["model"] == "other-model"


@pytest.mark.asyncio
async def test_no_tool_use_raises_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(_client("llm_pick_topic_no_tool.json"))
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_api_error_propagates() -> None:
    with pytest.raises(anthropic.APIStatusError):
        await _run(_client(status=500))


def test_input_requires_stories() -> None:
    with pytest.raises(ValidationError):
        PickTopicInput(stories=[])


def test_make_anthropic_client_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert make_anthropic_client().api_key == "test-key"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_anthropic_client()


@pytest.mark.asyncio
async def test_system_prompt_mentions_tool() -> None:
    requests: list[dict[str, object]] = []
    await _run(_client(requests=requests))
    system = str(requests[0]["system"])
    for text in (system, SYSTEM_PROMPT):
        assert TOOL_NAME in text
        assert "calling" in text


@pytest.mark.asyncio
async def test_system_prompt_lists_catalog() -> None:
    requests: list[dict[str, object]] = []
    await _run(_client(requests=requests))
    system = str(requests[0]["system"])
    for dataset in CATALOG:
        assert dataset.title in SYSTEM_PROMPT
        assert dataset.title in system
    assert SYSTEM_PROMPT.endswith(f"Respond only by calling the {TOOL_NAME} tool.")


@pytest.mark.asyncio
async def test_tool_schema_enum_is_catalog() -> None:
    requests: list[dict[str, object]] = []
    await _run(_client(requests=requests))
    tools = cast(list[dict[str, object]], requests[0]["tools"])
    schema = cast(dict[str, object], tools[0]["input_schema"])
    props = cast(dict[str, dict[str, object]], schema["properties"])
    assert props["topic"]["enum"] == [d.title for d in CATALOG]


@pytest.mark.asyncio
async def test_pick_outside_catalog_raises_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(_client("llm_pick_topic_off_catalog.json"))
    assert exc.value.type == NOT_IN_CATALOG_ERROR_TYPE == "TopicNotInCatalog"
    assert exc.value.non_retryable is False
    assert "Rail travel / passenger rail usage by country" in str(exc.value)


@pytest.mark.asyncio
async def test_pick_topic_canonicalises_title() -> None:
    body = _fixture("llm_pick_topic_response.json")
    content = cast(list[dict[str, object]], body["content"])
    cast(dict[str, str], content[1]["input"])["topic"] = "  SOLAR energy CONSUMPTION "

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=body)

    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    result = await _run(client)
    assert result.topic == "Solar energy consumption"
