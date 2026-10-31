"""Critique activity tests; LLM calls use recorded Messages API fixtures only."""

import ast
import json
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

import ai_trend_pipeline.story_drafting.critique as critique
from ai_trend_pipeline.story_drafting import (
    MAX_CRITIQUE_TOKENS,
    STORY_CRITIQUE_INVALID_ERROR_TYPE,
    STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE,
    TOOL_NAME_CRITIQUE,
    CritiqueCategory,
    CritiqueInput,
    CritiqueOutput,
    CritiqueSeverity,
)
from ai_trend_pipeline.topic import DEFAULT_MODEL
from tests.drafting_fixtures import (
    CANDIDATE_ID,
    CONTEXT_TEXT,
    DEFAULT_FACT_ID,
    REVISION_GUIDANCE,
    make_clean_package,
    make_critique_input,
    make_draft_input,
    make_revision,
)

FIXTURES = Path(__file__).parent / "fixtures"
PASS = "llm_critique_story_pass.json"
HEADLINE = "llm_critique_story_headline.json"
UNSUPPORTED_CLAIM = "llm_critique_story_unsupported_claim.json"
MISSING_CAVEAT = "llm_critique_story_missing_caveat.json"
SOURCE_DATE = "llm_critique_story_source_date.json"
INVENTED_FACT_ID = "llm_critique_story_invented_fact_id.json"
MALFORMED = "llm_critique_story_malformed.json"
NO_TOOL = "llm_critique_story_no_tool.json"


def _fixture(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / name).read_text()))


def _client(
    fixture: str = PASS,
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
                json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
            )
        return httpx2.Response(200, json=_fixture(fixture))

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def _run(
    data: CritiqueInput,
    *,
    fixture: str = PASS,
    requests: list[dict[str, object]] | None = None,
    status: int = 200,
) -> CritiqueOutput:
    activities = critique.StoryCritiqueActivities(_client(fixture, requests, status))
    return await ActivityEnvironment().run(activities.critique_story, data)


def _user_text(request: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], request["messages"])
    assert len(messages) == 1
    return cast(str, messages[0]["content"])


def _blocking_categories(result: CritiqueOutput) -> set[CritiqueCategory]:
    return {
        finding.category
        for finding in result.findings
        if finding.severity is CritiqueSeverity.BLOCKING
    }


@pytest.mark.asyncio
async def test_clean_draft_passes() -> None:
    data = make_critique_input()
    result = await _run(data, fixture=PASS)
    assert result.story_id == CANDIDATE_ID
    assert result.schema_version == 1
    assert result.passed is True
    assert result.findings == []
    assert result.revision_guidance is None
    assert _blocking_categories(result) == set()


@pytest.mark.asyncio
async def test_one_useful_critique_returns_blocking_findings_and_guidance() -> None:
    result = await _run(make_critique_input(), fixture=HEADLINE)
    assert result.passed is False
    assert _blocking_categories(result) == {CritiqueCategory.HEADLINE_OVERSTATEMENT}
    assert len(result.findings) == 1
    assert result.findings[0].location is not None
    assert result.findings[0].location.value == "headline"
    assert result.revision_guidance
    assert "Softening" not in result.revision_guidance


@pytest.mark.asyncio
async def test_unsupported_numbers_are_caught_without_an_llm_call() -> None:
    package = make_clean_package(body="The latest figure was 80.0 years.")
    requests: list[dict[str, object]] = []
    result = await _run(make_critique_input(draft=package), fixture=PASS, requests=requests)
    assert requests == []
    assert result.passed is False
    assert CritiqueCategory.NUMERICAL_FIDELITY in _blocking_categories(result)
    assert result.revision_guidance
    assert "numerical_fidelity" in result.revision_guidance


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture", "category"),
    [
        (UNSUPPORTED_CLAIM, CritiqueCategory.UNSUPPORTED_CLAIM),
        (MISSING_CAVEAT, CritiqueCategory.MISSING_CAVEAT),
    ],
)
async def test_unsupported_claim_and_missing_caveat_report_their_category(
    fixture: str, category: CritiqueCategory
) -> None:
    result = await _run(make_critique_input(), fixture=fixture)
    assert result.passed is False
    assert category in _blocking_categories(result)


@pytest.mark.asyncio
async def test_source_and_date_fidelity_report_their_categories() -> None:
    result = await _run(make_critique_input(), fixture=SOURCE_DATE)
    assert result.passed is False
    assert {
        CritiqueCategory.SOURCE_FIDELITY,
        CritiqueCategory.DATE_FIDELITY,
    } <= _blocking_categories(result)


@pytest.mark.asyncio
async def test_invented_fact_ids_in_checker_output_fail_closed() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_critique_input(), fixture=INVENTED_FACT_ID)
    assert exc.value.type == STORY_CRITIQUE_INVALID_ERROR_TYPE == "StoryCritiqueInvalid"
    assert exc.value.non_retryable is True
    assert "fact-v1-0123456789abcdef" not in str(exc.value)


@pytest.mark.asyncio
async def test_malformed_and_missing_tool_critique_outputs_are_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc:
        await _run(make_critique_input(), fixture=MALFORMED)
    assert exc.value.type == STORY_CRITIQUE_INVALID_ERROR_TYPE
    assert exc.value.non_retryable is True

    with pytest.raises(ApplicationError) as exc:
        await _run(make_critique_input(), fixture=NO_TOOL)
    assert exc.value.type == STORY_CRITIQUE_TOOL_MISSING_ERROR_TYPE == "StoryCritiqueToolMissing"
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_api_errors_propagate_for_temporal_retry() -> None:
    with pytest.raises(anthropic.APIStatusError):
        await _run(make_critique_input(), status=500)


@pytest.mark.asyncio
async def test_critique_prompt_includes_draft_facts_evidence_and_round() -> None:
    requests: list[dict[str, object]] = []
    await _run(make_critique_input(round=0), requests=requests)
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == MAX_CRITIQUE_TOKENS == 2048
    assert body["system"] == critique.SYSTEM_PROMPT
    assert body["tool_choice"] == {"type": "auto"}
    tools = cast(list[dict[str, object]], body["tools"])
    assert [tool["name"] for tool in tools] == [TOOL_NAME_CRITIQUE] == ["critique_story"]
    schema = cast(dict[str, object], tools[0]["input_schema"])
    props = cast(dict[str, object], schema["properties"])
    assert set(props) == {"passed", "findings", "revision_guidance"}

    user = _user_text(body)
    for header in (
        critique.CANDIDATE_HEADER,
        critique.CONTEXTS_HEADER,
        critique.EVIDENCE_HEADER,
        critique.FACTS_HEADER,
        critique.DRAFT_HEADER,
    ):
        assert header in user
    assert "Round: 0" in user
    assert CONTEXT_TEXT in user
    assert DEFAULT_FACT_ID in user
    assert '"rows"' not in user
    assert "lineage" not in user
    assert "test-key" not in user
    assert CONTEXT_TEXT not in cast(str, body["system"])
    assert "secret" not in user.lower()

    start = user.index(critique.CONTEXTS_HEADER) + len(critique.CONTEXTS_HEADER)
    end = user.index(critique.EVIDENCE_HEADER, start)
    contexts = cast(list[dict[str, object]], json.loads(user[start:end].strip()))
    assert contexts[0]["text"] == CONTEXT_TEXT
    draft_start = user.index(critique.DRAFT_HEADER) + len(critique.DRAFT_HEADER)
    draft_end = user.index("\n\nRound:", draft_start)
    draft = cast(dict[str, object], json.loads(user[draft_start:draft_end].strip()))
    assert draft["story_id"] == CANDIDATE_ID


@pytest.mark.asyncio
async def test_critique_prompt_uses_round_one_after_a_revision() -> None:
    revision = make_revision()
    data = make_critique_input(
        draft_input=make_draft_input(revision=revision),
        draft=revision.prior_draft,
        round=1,
    )
    requests: list[dict[str, object]] = []
    result = await _run(data, fixture=PASS, requests=requests)
    assert result.passed is True
    user = _user_text(requests[0])
    assert "Round: 1" in user
    assert REVISION_GUIDANCE in user
    assert revision.prior_draft.headline in user


def test_critique_activity_starts_no_workflow_or_temporal_client() -> None:
    module_file = critique.__file__
    assert module_file is not None
    source = Path(module_file).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = {
        "temporalio.client",
        "temporalio.workflow",
        "ai_trend_pipeline.services",
        "ai_trend_pipeline.web",
        "ai_trend_pipeline.bluesky",
        "ai_trend_pipeline.worker",
        "ai_trend_pipeline.daily_chart",
        "ai_trend_pipeline.chart_loop",
        "ai_trend_pipeline.story_candidate",
        "ai_trend_pipeline.source_ingestion",
    }
    for name in imported:
        for banned in forbidden:
            assert name != banned and not name.startswith(f"{banned}."), (name, banned)
    assert "start_workflow" not in source
    assert "start_child_workflow" not in source
    assert "Client.connect" not in source


def test_prompt_does_not_include_media() -> None:
    from tests.media_fixtures import make_manifest

    def prompt(media: object) -> str:
        revision = make_revision(prior_draft=make_clean_package(media=media))
        data = make_critique_input(
            draft_input=make_draft_input(revision=revision),
            draft=make_clean_package(media=media),
        )
        return critique.build_user_prompt(data)

    with_media = prompt(make_manifest())
    assert with_media == prompt(None)
    assert "static_artifact" not in with_media
