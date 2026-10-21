"""Activity that asks Claude for a Vega-Lite spec and caption, then injects table data."""

import anthropic
from anthropic.types import ToolParam, ToolUseBlock
from pydantic import BaseModel, Field, JsonValue, ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.owid import OwidRow, OwidTable
from ai_trend_pipeline.topic import DEFAULT_MODEL

MAX_TOKENS = 4096
TOOL_NAME = "write_chart"
INVALID_DRAFT_ERROR_TYPE = "InvalidChartDraft"
SAMPLE_ROWS = 20
MAX_ENTITIES = 8
MAX_CAPTION_CHARS = 280
SYSTEM_PROMPT = (
    "You are given a topic and a summary of an Our World in Data table. Pick 1 to 8 entities "
    "from it and write a Vega-Lite v5 spec that uses only the fields entity (nominal), year "
    "and value. Do not include data in the spec; it is added for you. Also write a factual "
    f"caption of at most {MAX_CAPTION_CHARS} characters."
)


class WriteChartSpecInput(BaseModel):
    topic: str = Field(min_length=1)
    table: OwidTable


class ChartDraft(BaseModel):
    entities: list[str] = Field(min_length=1, max_length=MAX_ENTITIES)
    vega_lite_spec: dict[str, JsonValue]
    caption: str = Field(min_length=1, max_length=MAX_CAPTION_CHARS)


class ChartSpec(BaseModel):
    vega_lite_spec: dict[str, JsonValue]
    caption: str
    entities: list[str]


def _format_request(topic: str, table: OwidTable) -> str:
    entities = list(dict.fromkeys(r.entity for r in table.rows))
    years = [r.year for r in table.rows]
    sample = "\n".join(f"{r.entity} | {r.year} | {r.value}" for r in table.rows[:SAMPLE_ROWS])
    return (
        f"Topic: {topic}\n"
        f"Table title: {table.title}\n"
        f"Value column: {table.value_column}\n"
        f"Entities: {', '.join(entities)}\n"
        f"Years: {min(years)} to {max(years)}\n"
        f"Row count: {len(table.rows)}\n"
        f"First {min(SAMPLE_ROWS, len(table.rows))} rows (entity | year | value):\n{sample}"
    )


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_DRAFT_ERROR_TYPE, non_retryable=True)


def _row_values(rows: list[OwidRow]) -> list[JsonValue]:
    return [{"entity": r.entity, "year": r.year, "value": r.value} for r in rows]


class ChartSpecActivities:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @activity.defn
    async def write_chart_spec(self, data: WriteChartSpecInput) -> ChartSpec:
        table = data.table
        if not table.rows:
            raise _invalid("OWID table has no rows")
        tool: ToolParam = {
            "name": TOOL_NAME,
            "description": "Report the chosen entities, the Vega-Lite spec and a caption.",
            "input_schema": ChartDraft.model_json_schema(),
        }
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": _format_request(data.topic, table)}],
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
            draft = ChartDraft.model_validate(block.input)
        except ValidationError as exc:
            raise _invalid(f"Invalid chart draft: {exc}") from exc
        known = {r.entity for r in table.rows}
        unknown = [e for e in draft.entities if e not in known]
        if unknown:
            raise _invalid(f"Chart draft names entities not in the table: {unknown}")
        chosen = set(draft.entities)
        spec = {k: v for k, v in draft.vega_lite_spec.items() if k not in ("data", "datasets")}
        values = _row_values([r for r in table.rows if r.entity in chosen])
        spec["data"] = {"values": values}
        return ChartSpec(vega_lite_spec=spec, caption=draft.caption, entities=draft.entities)
