"""Deterministic workflow helper: draft a chart, critique it, regenerate at most once."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, Field

    from ai_trend_pipeline.chart_spec import (
        INVALID_DRAFT_ERROR_TYPE,
        ChartSpec,
        ChartSpecActivities,
        WriteChartSpecInput,
    )
    from ai_trend_pipeline.critique import ChartCritique, CritiqueActivities, CritiqueChartInput

LLM_ACTIVITY_TIMEOUT = timedelta(seconds=120)
LLM_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
MAX_DRAFTS = 2


class CritiquedChart(BaseModel):
    chart: ChartSpec
    critique: ChartCritique
    attempts: int = Field(ge=1, le=MAX_DRAFTS)


async def _write(data: WriteChartSpecInput) -> ChartSpec:
    return await workflow.execute_activity_method(
        ChartSpecActivities.write_chart_spec,
        data,
        start_to_close_timeout=LLM_ACTIVITY_TIMEOUT,
        retry_policy=LLM_RETRY_POLICY,
    )


async def _critique(data: WriteChartSpecInput, chart: ChartSpec) -> ChartCritique:
    return await workflow.execute_activity_method(
        CritiqueActivities.critique_chart,
        CritiqueChartInput(topic=data.topic, table=data.table, chart=chart),
        start_to_close_timeout=LLM_ACTIVITY_TIMEOUT,
        retry_policy=LLM_RETRY_POLICY,
    )


async def draft_chart_with_critique(data: WriteChartSpecInput) -> CritiquedChart:
    """Draft and critique a chart; regenerate once with feedback if the first draft fails.

    Workflow code only: it makes activity calls and pure branching decisions.
    """
    feedback: list[str]
    try:
        chart = await _write(data)
    except ActivityError as exc:
        cause = exc.cause
        if not (isinstance(cause, ApplicationError) and cause.type == INVALID_DRAFT_ERROR_TYPE):
            raise
        feedback = [cause.message]
    else:
        critique = await _critique(data, chart)
        if critique.passed:
            return CritiquedChart(chart=chart, critique=critique, attempts=1)
        feedback = critique.reasons
    chart = await _write(data.model_copy(update={"feedback": feedback}))
    critique = await _critique(data, chart)
    return CritiquedChart(chart=chart, critique=critique, attempts=MAX_DRAFTS)


@workflow.defn
class DraftChartWorkflow:
    @workflow.run
    async def run(self, data: WriteChartSpecInput) -> CritiquedChart:
        return await draft_chart_with_critique(data)
