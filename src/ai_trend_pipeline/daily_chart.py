"""DailyChartWorkflow: HN stories -> topic -> OWID data -> chart -> PNG -> human approval."""

from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, Field

    from ai_trend_pipeline.bluesky import BlueskyActivities, PostChartInput, PostChartOutput
    from ai_trend_pipeline.chart_loop import (
        LLM_ACTIVITY_TIMEOUT,
        LLM_RETRY_POLICY,
        draft_chart_with_critique,
    )
    from ai_trend_pipeline.chart_spec import WriteChartSpecInput
    from ai_trend_pipeline.critique import ChartCritique
    from ai_trend_pipeline.hn import HNActivities, HNTopStoriesInput
    from ai_trend_pipeline.owid import FetchOwidInput, OwidActivities
    from ai_trend_pipeline.render import RenderChartInput, render_chart_png
    from ai_trend_pipeline.topic import PickTopicInput, TopicActivities

DAILY_CHART_TASK_QUEUE = "daily-chart-task-queue"
APPROVAL_TIMEOUT = timedelta(hours=20)
IO_ACTIVITY_TIMEOUT = timedelta(seconds=60)
IO_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
MANUAL_TOPIC_REASON = "Manually chosen"


class DailyChartInput(BaseModel):
    story_limit: int = Field(default=30, ge=1, le=100)
    since_year: int = Field(default=2000, ge=1800, le=2100)
    output_dir: str = Field(default="charts", min_length=1)
    topic: str | None = Field(default=None, min_length=1)


class ReviewRequest(BaseModel):
    topic: str
    topic_reason: str
    caption: str
    png_path: str
    spec_sha256: str
    critique: ChartCritique
    attempts: int


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=500)


class ReviewState(BaseModel):
    status: Literal["drafting", "awaiting_decision", "approved", "rejected", "expired"]
    review: ReviewRequest | None = None
    note: str = ""


class DailyChartResult(BaseModel):
    status: Literal["approved", "rejected", "expired"]
    review: ReviewRequest
    note: str = ""
    post: PostChartOutput | None = None


@workflow.defn
class DailyChartWorkflow:
    def __init__(self) -> None:
        self._state = ReviewState(status="drafting")
        self._decision: ApprovalDecision | None = None

    @workflow.run
    async def run(self, data: DailyChartInput) -> DailyChartResult:
        if data.topic is not None:
            topic, reason = data.topic, MANUAL_TOPIC_REASON
        else:
            stories = await workflow.execute_activity_method(
                HNActivities.fetch_hn_top_stories,
                HNTopStoriesInput(limit=data.story_limit),
                start_to_close_timeout=IO_ACTIVITY_TIMEOUT,
                retry_policy=IO_RETRY_POLICY,
            )
            picked = await workflow.execute_activity_method(
                TopicActivities.pick_topic,
                PickTopicInput(stories=stories.stories),
                start_to_close_timeout=LLM_ACTIVITY_TIMEOUT,
                retry_policy=LLM_RETRY_POLICY,
            )
            topic, reason = picked.topic, picked.reason
        table = await workflow.execute_activity_method(
            OwidActivities.fetch_owid_dataset,
            FetchOwidInput(topic=topic, since_year=data.since_year),
            start_to_close_timeout=IO_ACTIVITY_TIMEOUT,
            retry_policy=IO_RETRY_POLICY,
        )
        chart = await draft_chart_with_critique(WriteChartSpecInput(topic=topic, table=table))
        rendered = await workflow.execute_activity(
            render_chart_png,
            RenderChartInput(vega_lite_spec=chart.chart.vega_lite_spec, output_dir=data.output_dir),
            start_to_close_timeout=IO_ACTIVITY_TIMEOUT,
            retry_policy=IO_RETRY_POLICY,
        )
        review = ReviewRequest(
            topic=topic,
            topic_reason=reason,
            caption=chart.chart.caption,
            png_path=rendered.path,
            spec_sha256=rendered.spec_sha256,
            critique=chart.critique,
            attempts=chart.attempts,
        )
        self._state = ReviewState(status="awaiting_decision", review=review)
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None, timeout=APPROVAL_TIMEOUT
            )
        except TimeoutError:
            self._state = ReviewState(status="expired", review=review)
            return DailyChartResult(status="expired", review=review)
        decision = self._decision
        assert decision is not None
        status: Literal["approved", "rejected"] = "approved" if decision.approved else "rejected"
        post: PostChartOutput | None = None
        if decision.approved:
            post = await workflow.execute_activity_method(
                BlueskyActivities.post_chart_to_bluesky,
                PostChartInput(
                    png_path=review.png_path,
                    text=review.caption,
                    alt_text=f"Chart: {review.topic}. {review.caption}"[:1000],
                    idempotency_key=workflow.info().workflow_id,
                    created_at=workflow.now(),
                ),
                start_to_close_timeout=IO_ACTIVITY_TIMEOUT,
                retry_policy=IO_RETRY_POLICY,
            )
        self._state = ReviewState(status=status, review=review, note=decision.note)
        return DailyChartResult(status=status, review=review, note=decision.note, post=post)

    @workflow.signal
    def decide(self, decision: ApprovalDecision) -> None:
        if self._state.status != "awaiting_decision" or self._decision is not None:
            workflow.logger.warning("Ignoring decision; state is %s", self._state.status)
            return
        self._decision = decision

    @workflow.query
    def review_state(self) -> ReviewState:
        return self._state
