"""Activity that asks Claude to critique a chart spec against a summary of its data."""

import json

import anthropic
from anthropic.types import ToolParam, ToolUseBlock
from pydantic import BaseModel, Field, ValidationError, model_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.chart_spec import ChartSpec
from ai_trend_pipeline.owid import OwidTable
from ai_trend_pipeline.topic import DEFAULT_MODEL

MAX_TOKENS = 1024
TOOL_NAME = "critique_chart"
INVALID_CRITIQUE_ERROR_TYPE = "InvalidChartCritique"
MAX_REASONS = 5
SYSTEM_PROMPT = (
    "You review a Vega-Lite chart made from an Our World in Data table. Check that the caption "
    "is factually supported by the data summary, that the encoding (entity as nominal colour, "
    "year on x, value on y) and the title fit the topic, and that the chart is not misleading. "
    "Fail the chart only for concrete problems, and give one short reason per problem."
    f" Respond only by calling the {TOOL_NAME} tool."
)


class CritiqueChartInput(BaseModel):
    topic: str = Field(min_length=1)
    table: OwidTable
    chart: ChartSpec


class ChartCritique(BaseModel):
    passed: bool
    reasons: list[str] = Field(default_factory=list, max_length=MAX_REASONS)

    @model_validator(mode="after")
    def _failure_needs_reasons(self) -> "ChartCritique":
        if not self.passed and not self.reasons:
            raise ValueError("a failed critique must give at least one reason")
        return self


def _summary_lines(table: OwidTable, entities: list[str]) -> list[str]:
    lines: list[str] = []
    for entity in entities:
        rows = sorted((r for r in table.rows if r.entity == entity), key=lambda r: r.year)
        if not rows:
            continue
        values = [r.value for r in rows]
        lines.append(
            f"{entity}: first {rows[0].year} = {rows[0].value}, "
            f"last {rows[-1].year} = {rows[-1].value}, "
            f"min {min(values)}, max {max(values)}, {len(rows)} rows"
        )
    return lines


def _format_request(data: CritiqueChartInput) -> str:
    chart = data.chart
    spec = {k: v for k, v in chart.vega_lite_spec.items() if k not in ("data", "datasets")}
    return (
        f"Topic: {data.topic}\n"
        f"Table title: {data.table.title}\n"
        f"Value column: {data.table.value_column}\n"
        f"Caption: {chart.caption}\n"
        f"Entities: {', '.join(chart.entities)}\n"
        f"Vega-Lite spec (data omitted): {json.dumps(spec)}\n"
        "Data summary per entity:\n" + "\n".join(_summary_lines(data.table, chart.entities))
    )


class CritiqueActivities:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @activity.defn
    async def critique_chart(self, data: CritiqueChartInput) -> ChartCritique:
        tool: ToolParam = {
            "name": TOOL_NAME,
            "description": "Report whether the chart passes review, with reasons if not.",
            "input_schema": ChartCritique.model_json_schema(),
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": _format_request(data)}],
        )
        block = next(
            (b for b in response.content if isinstance(b, ToolUseBlock) and b.name == TOOL_NAME),
            None,
        )
        if block is None:
            raise ApplicationError(
                f"Model response contained no {TOOL_NAME} tool call", non_retryable=True
            )
        try:
            return ChartCritique.model_validate(block.input)
        except ValidationError as exc:
            raise ApplicationError(
                f"Invalid chart critique: {exc}",
                type=INVALID_CRITIQUE_ERROR_TYPE,
                non_retryable=True,
            ) from exc
