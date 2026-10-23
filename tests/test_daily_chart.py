"""Tests for DailyChartWorkflow in the time-skipping environment with fake activities."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.bluesky import PostChartInput, PostChartOutput
from ai_trend_pipeline.chart_spec import ChartSpec, WriteChartSpecInput
from ai_trend_pipeline.critique import ChartCritique, CritiqueChartInput
from ai_trend_pipeline.daily_chart import (
    DAILY_CHART_TASK_QUEUE,
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    DailyChartWorkflow,
    ReviewState,
)
from ai_trend_pipeline.hn import HNStory, HNTopStoriesInput, HNTopStoriesOutput
from ai_trend_pipeline.owid import NO_MATCH_ERROR_TYPE, FetchOwidInput, OwidRow, OwidTable
from ai_trend_pipeline.render import RenderChartInput, RenderChartOutput
from ai_trend_pipeline.topic import PickTopicInput, PickTopicOutput

PNG_PATH = "/tmp/fake/chart.png"
SPEC_SHA = "a" * 64
POST_URI = "at://did:plc:fake/app.bsky.feed.post/fakerkey"
POST_OUT = PostChartOutput(uri=POST_URI, cid="bafyfake", rkey="fakerkey", already_posted=False)
TOPIC = "Life expectancy trends"
PASS = ChartCritique(passed=True, reasons=[])
FAIL = ChartCritique(passed=False, reasons=["caption contradicts data", "axis unlabeled"])
STORY = HNStory(
    id=1,
    title="Story",
    url=None,
    score=10,
    by="a",
    time=datetime(2026, 1, 1, tzinfo=UTC),
    comments=2,
)
TABLE = OwidTable(
    slug="life-expectancy",
    title="Life expectancy",
    value_column="v",
    rows=[OwidRow(entity="France", code=None, year=2000, value=1.0)],
)


class FakeDailyActivities:
    """Stateful fakes that record inputs and return scripted outputs."""

    def __init__(
        self,
        critiques: list[ChartCritique] | None = None,
        owid_fails: bool = False,
        post_fails: bool = False,
    ):
        self._critiques = list(critiques or [PASS])
        self._owid_fails = owid_fails
        self._post_fails = post_fails
        self.calls: list[str] = []
        self.hn_inputs: list[HNTopStoriesInput] = []
        self.topic_inputs: list[PickTopicInput] = []
        self.owid_inputs: list[FetchOwidInput] = []
        self.write_inputs: list[WriteChartSpecInput] = []
        self.critique_inputs: list[CritiqueChartInput] = []
        self.render_inputs: list[RenderChartInput] = []
        self.post_inputs: list[PostChartInput] = []

    @activity.defn(name="fetch_hn_top_stories")
    async def fetch_hn_top_stories(self, data: HNTopStoriesInput) -> HNTopStoriesOutput:
        self.calls.append("fetch_hn_top_stories")
        self.hn_inputs.append(data)
        return HNTopStoriesOutput(stories=[STORY])

    @activity.defn(name="pick_topic")
    async def pick_topic(self, data: PickTopicInput) -> PickTopicOutput:
        self.calls.append("pick_topic")
        self.topic_inputs.append(data)
        return PickTopicOutput(topic=TOPIC, reason="because")

    @activity.defn(name="fetch_owid_dataset")
    async def fetch_owid_dataset(self, data: FetchOwidInput) -> OwidTable:
        self.calls.append("fetch_owid_dataset")
        self.owid_inputs.append(data)
        if self._owid_fails:
            raise ApplicationError("no match", type=NO_MATCH_ERROR_TYPE, non_retryable=True)
        return TABLE

    @activity.defn(name="write_chart_spec")
    async def write_chart_spec(self, data: WriteChartSpecInput) -> ChartSpec:
        self.calls.append("write_chart_spec")
        self.write_inputs.append(data)
        return ChartSpec(
            vega_lite_spec={"mark": "line", "n": len(self.write_inputs)},
            caption=f"caption {len(self.write_inputs)}",
            entities=["France"],
        )

    @activity.defn(name="critique_chart")
    async def critique_chart(self, data: CritiqueChartInput) -> ChartCritique:
        self.calls.append("critique_chart")
        self.critique_inputs.append(data)
        return self._critiques[min(len(self.critique_inputs), len(self._critiques)) - 1]

    @activity.defn(name="render_chart_png")
    async def render_chart_png(self, data: RenderChartInput) -> RenderChartOutput:
        self.calls.append("render_chart_png")
        self.render_inputs.append(data)
        return RenderChartOutput(path=PNG_PATH, spec_sha256=SPEC_SHA, size_bytes=10)

    @activity.defn(name="post_chart_to_bluesky")
    async def post_chart_to_bluesky(self, data: PostChartInput) -> PostChartOutput:
        self.calls.append("post_chart_to_bluesky")
        self.post_inputs.append(data)
        if self._post_fails:
            raise ApplicationError("auth", type="BlueskyAuthFailed", non_retryable=True)
        return POST_OUT

    def as_list(self) -> list[Callable[..., object]]:
        return [
            self.fetch_hn_top_stories,
            self.pick_topic,
            self.fetch_owid_dataset,
            self.write_chart_spec,
            self.critique_chart,
            self.render_chart_png,
            self.post_chart_to_bluesky,
        ]


async def wait_for_state(
    handle: WorkflowHandle[DailyChartWorkflow, DailyChartResult], status: str
) -> ReviewState:
    """Poll review_state with real sleeps (at most 10 s) until it reaches `status`."""
    state: ReviewState | None = None
    for _ in range(200):
        state = await handle.query(DailyChartWorkflow.review_state)
        if state.status == status:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"state never reached {status}; last was {state}")


async def start_env() -> WorkflowEnvironment:
    return await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)


def make_worker(env: WorkflowEnvironment, fakes: FakeDailyActivities) -> Worker:
    return Worker(
        env.client,
        task_queue=DAILY_CHART_TASK_QUEUE,
        workflows=[DailyChartWorkflow],
        activities=fakes.as_list(),
    )


async def start_workflow(
    env: WorkflowEnvironment, data: DailyChartInput | None = None
) -> WorkflowHandle[DailyChartWorkflow, DailyChartResult]:
    return await env.client.start_workflow(
        DailyChartWorkflow.run,
        data or DailyChartInput(),
        id=f"wf-{uuid.uuid4()}",
        task_queue=DAILY_CHART_TASK_QUEUE,
    )


@pytest.mark.asyncio
async def test_activities_chained_in_order() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(
            env, DailyChartInput(story_limit=5, since_year=1990, output_dir="out")
        )
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=True))
        await handle.result()
    assert fakes.calls == [
        "fetch_hn_top_stories",
        "pick_topic",
        "fetch_owid_dataset",
        "write_chart_spec",
        "critique_chart",
        "render_chart_png",
        "post_chart_to_bluesky",
    ]
    assert fakes.hn_inputs[0].limit == 5
    assert fakes.topic_inputs[0].stories == [STORY]
    assert fakes.owid_inputs[0] == FetchOwidInput(topic=TOPIC, since_year=1990)
    assert fakes.write_inputs[0].topic == TOPIC
    assert fakes.write_inputs[0].table == TABLE
    assert fakes.render_inputs[0].output_dir == "out"
    assert fakes.render_inputs[0].vega_lite_spec == {"mark": "line", "n": 1}


@pytest.mark.asyncio
async def test_pauses_until_decision() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        state = await wait_for_state(handle, "awaiting_decision")
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
    assert state.review is not None
    assert state.review.topic == TOPIC
    assert state.review.topic_reason == "because"
    assert state.review.caption == "caption 1"
    assert state.review.png_path == PNG_PATH
    assert state.review.spec_sha256 == SPEC_SHA
    assert state.review.critique == PASS
    assert state.review.attempts == 1


@pytest.mark.asyncio
async def test_approve_signal_completes_approved() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=True, note="ok"))
        result = await handle.result()
    assert result.status == "approved"
    assert result.note == "ok"
    assert result.review.png_path == PNG_PATH


@pytest.mark.asyncio
async def test_reject_signal_completes_rejected() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=False, note="no"))
        result = await handle.result()
    assert result.status == "rejected"
    assert result.note == "no"


@pytest.mark.asyncio
async def test_second_decision_is_ignored() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=True, note="a"))
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=False, note="b"))
        result = await handle.result()
    assert result.status == "approved"
    assert result.note == "a"


@pytest.mark.asyncio
async def test_no_decision_expires() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        result = await handle.result()
    assert result.status == "expired"
    assert result.review.png_path == PNG_PATH


@pytest.mark.asyncio
async def test_failed_critique_is_shown_in_review() -> None:
    fakes = FakeDailyActivities(critiques=[FAIL, FAIL])
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        state = await wait_for_state(handle, "awaiting_decision")
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
    assert state.review is not None
    assert state.review.attempts == 2
    assert state.review.critique.passed is False
    assert state.review.critique.reasons == FAIL.reasons
    assert state.review.caption == "caption 2"


@pytest.mark.asyncio
async def test_non_retryable_activity_error_fails_workflow() -> None:
    fakes = FakeDailyActivities(owid_fails=True)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert fakes.render_inputs == []
    assert "render_chart_png" not in fakes.calls


@pytest.mark.asyncio
async def test_approve_posts_once_with_review_data() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=True))
        result = await handle.result()
    assert len(fakes.post_inputs) == 1
    sent = fakes.post_inputs[0]
    assert sent.png_path == PNG_PATH
    assert sent.text == "caption 1"
    assert sent.alt_text == f"Chart: {TOPIC}. caption 1"
    assert sent.idempotency_key == handle.id
    assert sent.created_at.tzinfo is not None
    assert result.post == POST_OUT


@pytest.mark.asyncio
async def test_reject_and_expire_do_not_post() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=False))
        rejected = await handle.result()
        expired = await (await start_workflow(env)).result()
    assert rejected.status == "rejected"
    assert expired.status == "expired"
    assert rejected.post is None
    assert expired.post is None
    assert fakes.post_inputs == []


@pytest.mark.asyncio
async def test_post_failure_fails_workflow() -> None:
    fakes = FakeDailyActivities(post_fails=True)
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=True))
        with pytest.raises(WorkflowFailureError):
            await handle.result()
