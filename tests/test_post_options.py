"""Tests for per-post options: model, filtering, chart types, prompt and service merge."""

import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.chart_spec import (
    MAX_ENTITIES,
    SYSTEM_PROMPT,
    ChartSpec,
    ChartSpecActivities,
    WriteChartSpecInput,
)
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.critique import SYSTEM_PROMPT as CRITIQUE_PROMPT
from ai_trend_pipeline.critique import CritiqueActivities, CritiqueChartInput
from ai_trend_pipeline.daily_chart import DailyChartInput, ReviewState
from ai_trend_pipeline.owid import OWID_BASE_URL, FetchOwidInput, OwidActivities, OwidRow, OwidTable
from ai_trend_pipeline.post_options import (
    MAX_COUNTRIES,
    PostOptions,
    apply_chart_type,
    filter_table_rows,
    merge_style,
    options_from_form,
    options_prompt,
    parse_countries,
)
from ai_trend_pipeline.render import RenderChartInput, render_chart_png
from ai_trend_pipeline.services.charts import ChartService, StartChartRequest
from ai_trend_pipeline.style import ChartStyle
from tests.style_fakes import InMemoryStyleStore
from tests.test_chart_service import FakeGateway
from tests.test_chart_spec import (
    TOPIC,
    _fixture,  # pyright: ignore[reportPrivateUsage]
    make_client,
    make_table,
)

CSV = (Path(__file__).parent / "fixtures" / "owid_life_expectancy.csv").read_text()
LIFE_TOPIC = "Rising life expectancy worldwide"


def _owid_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=CSV)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=OWID_BASE_URL)


async def _fetch(options: PostOptions | None, since_year: int = 2000) -> OwidTable:
    return await ActivityEnvironment().run(
        OwidActivities(_owid_client()).fetch_owid_dataset,
        FetchOwidInput(topic=LIFE_TOPIC, since_year=since_year, options=options),
    )


# --- model ---


def test_defaults_are_valid() -> None:
    options = PostOptions()
    assert options.chart_type is None
    assert options.countries == []
    assert options.instructions == ""


def test_max_countries_matches_chart_spec() -> None:
    assert MAX_ENTITIES == MAX_COUNTRIES


@pytest.mark.parametrize(
    "bad",
    [
        {"chart_type": "pie"},
        {"time_preset": "last5"},
        {"tone": "sarcastic"},
        {"from_year": 1799},
        {"to_year": 2101},
        {"from_year": 2000, "to_year": 1999},
        {"time_preset": "last10", "from_year": 2000},
        {"countries": [f"c{i}" for i in range(9)]},
        {"countries": ["x" * 61]},
        {"countries": [""]},
        {"instructions": "x" * 301},
        {"instructions": "bad\x00"},
    ],
)
def test_bad_values_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PostOptions.model_validate(bad)


def test_instructions_whitespace_collapsed() -> None:
    assert PostOptions(instructions="a\n\tb   c ").instructions == "a b c"


def test_parse_countries() -> None:
    assert parse_countries(" France, ,japan, FRANCE ") == ["France", "japan"]


def test_options_from_form_blank_is_none() -> None:
    assert options_from_form(" ", "", "", " ", " , ", "", "  ") is None


def test_options_from_form_parses() -> None:
    options = options_from_form("bar", "", "1990", "", "France, Japan", "punchy", " hi ")
    assert options == PostOptions(
        chart_type="bar",
        from_year=1990,
        countries=["France", "Japan"],
        tone="punchy",
        instructions="hi",
    )


def test_options_from_form_bad_year_fails_on_from_year() -> None:
    with pytest.raises(ValidationError) as exc:
        options_from_form("", "", "abc", "", "", "", "")
    assert exc.value.errors()[0]["loc"] == ("from_year",)


# --- merge ---


def test_merge_option_tone_over_profile() -> None:
    profile = ChartStyle(tone="friendly", palette="ocean")
    merged = merge_style(profile, PostOptions(tone="punchy"))
    assert merged == ChartStyle(tone="punchy", palette="ocean")


def test_merge_without_tone_returns_profile() -> None:
    profile = ChartStyle(tone="friendly")
    assert merge_style(profile, PostOptions(chart_type="bar")) is profile
    assert merge_style(profile, None) is profile
    assert merge_style(None, None) is None
    assert merge_style(None, PostOptions(chart_type="bar")) is None


def test_merge_tone_without_profile() -> None:
    assert merge_style(None, PostOptions(tone="academic")) == ChartStyle(tone="academic")


# --- filtering ---


@pytest.mark.asyncio
async def test_none_matches_today() -> None:
    table = await _fetch(None)
    assert [(r.entity, r.year) for r in table.rows] == [
        ("Africa", 2000),
        ("Africa", 2020),
        ("Afghanistan", 2000),
        ("Afghanistan", 2021),
        ("France", 2000),
        ("France", 2021),
        ("Japan", 2021),
    ]
    assert table.notes == []
    assert table.matched_countries == []


@pytest.mark.asyncio
async def test_last10_keeps_years_after_2011() -> None:
    table = await _fetch(PostOptions(time_preset="last10"))
    assert {r.year for r in table.rows} == {2020, 2021}


@pytest.mark.asyncio
async def test_from_to_years() -> None:
    table = await _fetch(PostOptions(from_year=1950, to_year=1999))
    assert {(r.entity, r.year) for r in table.rows} >= {("Africa", 1950), ("France", 1999)}
    assert all(1950 <= r.year <= 1999 for r in table.rows)


@pytest.mark.asyncio
async def test_all_keeps_1950() -> None:
    table = await _fetch(PostOptions(time_preset="all"))
    assert any(r.year == 1950 for r in table.rows)


@pytest.mark.asyncio
async def test_countries_matched_with_notes() -> None:
    table = await _fetch(PostOptions(countries=["france", "JPN", "Narnia", "Frnace"]))
    assert table.matched_countries == ["France", "Japan"]
    assert {r.entity for r in table.rows} == {"France", "Japan"}
    assert "No data for 'Narnia' in Life expectancy." in table.notes
    assert "No data for 'Frnace' in Life expectancy. Did you mean France?" in table.notes


@pytest.mark.asyncio
async def test_no_country_matches_means_no_filter() -> None:
    table = await _fetch(PostOptions(countries=["Narnia"]))
    assert table.matched_countries == []
    assert {r.entity for r in table.rows} == {"Africa", "Afghanistan", "France", "Japan"}
    assert table.notes[-1] == "None of your countries matched, so the chart picks its own."


@pytest.mark.asyncio
async def test_empty_range_falls_back_to_since_year() -> None:
    table = await _fetch(PostOptions(from_year=1900, to_year=1910))
    assert table.rows == (await _fetch(None)).rows
    assert table.notes == ["No data between 1900 and 1910; showing years since 2000 instead."]


def test_filter_pure_open_bounds() -> None:
    rows = [OwidRow(entity="A", code=None, year=y, value=1.0) for y in (1990, 2000, 2010)]
    assert [r.year for r in filter_table_rows(rows, PostOptions(from_year=2000), 1900).rows] == [
        2000,
        2010,
    ]
    assert [r.year for r in filter_table_rows(rows, PostOptions(to_year=2000), 1900).rows] == [
        1990,
        2000,
    ]


# --- chart type ---


def _write_input(
    options: PostOptions | None, style: ChartStyle | None = None
) -> WriteChartSpecInput:
    return WriteChartSpecInput(topic=TOPIC, table=make_table(), options=options, style=style)


async def _write(
    options: PostOptions | None,
    style: ChartStyle | None = None,
    requests: list[dict[str, object]] | None = None,
    table: OwidTable | None = None,
) -> ChartSpec:
    data = _write_input(options, style)
    if table is not None:
        data = data.model_copy(update={"table": table})
    return await ActivityEnvironment().run(
        ChartSpecActivities(make_client(requests=requests)).write_chart_spec, data
    )


@pytest.mark.asyncio
async def test_chart_types_templates() -> None:
    style = ChartStyle(palette="ocean")
    line = (await _write(PostOptions(chart_type="line"), style)).vega_lite_spec
    area = (await _write(PostOptions(chart_type="area"), style)).vega_lite_spec
    bar = (await _write(PostOptions(chart_type="bar"), style)).vega_lite_spec
    assert line["mark"] == {"type": "line"}
    assert area["mark"] == {"type": "area", "opacity": 0.5, "line": True}
    assert bar["mark"] == "bar"
    assert "transform" in bar
    assert "transform" not in line
    for spec in (line, area, bar):
        assert spec["title"] == "Life expectancy at birth"
        assert "config" in spec
        assert "url" not in json.dumps(spec["data"])
    encoding = cast(dict[str, dict[str, object]], area["encoding"])
    assert encoding["y"]["stack"] is None
    assert encoding["y"]["title"] == "Life expectancy at birth"
    bar_enc = cast(dict[str, dict[str, object]], bar["encoding"])
    assert bar_enc["y"]["field"] == "entity"
    assert bar_enc["x"]["field"] == "value"


def test_apply_chart_type_drops_llm_keys() -> None:
    out = apply_chart_type({"title": "T", "mark": "point", "layer": [], "usermeta": 1}, "line", "v")
    assert set(out) == {"title", "mark", "encoding"}


@pytest.mark.asyncio
async def test_chart_types_render_distinct(tmp_path: Path) -> None:
    shas: list[str] = []
    for kind in ("line", "bar", "area"):
        result = await _write(PostOptions(chart_type=kind))
        out = await ActivityEnvironment().run(
            render_chart_png,
            RenderChartInput(vega_lite_spec=result.vega_lite_spec, output_dir=str(tmp_path / kind)),
        )
        shas.append(out.spec_sha256)
    assert len(set(shas)) == 3


@pytest.mark.asyncio
async def test_matched_countries_override_entities() -> None:
    table = make_table().model_copy(update={"matched_countries": ["France", "Japan"]})
    result = await _write(None, table=table)
    assert result.entities == ["France", "Japan"]
    data = cast(dict[str, list[dict[str, object]]], result.vega_lite_spec["data"])
    assert {row["entity"] for row in data["values"]} == {"France", "Japan"}
    assert len(data["values"]) == 6


# --- prompt ---


def _user_content(request: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], request["messages"])
    return cast(str, messages[0]["content"])


@pytest.mark.asyncio
async def test_instructions_only_in_user_message_and_neutralised() -> None:
    requests: list[dict[str, object]] = []
    await _write(PostOptions(instructions="Say <b>hi</b>"), requests=requests)
    content = _user_content(requests[0])
    assert "<user_instructions>Say ‹b›hi‹/b›</user_instructions>" in content
    assert "untrusted wishes about wording and emphasis only" in content
    assert requests[0]["system"] == SYSTEM_PROMPT
    assert "Say" not in json.dumps(requests[0]["system"])


@pytest.mark.asyncio
async def test_chart_type_and_country_lines() -> None:
    requests: list[dict[str, object]] = []
    table = make_table().model_copy(update={"matched_countries": ["France", "Japan"]})
    await _write(PostOptions(chart_type="bar"), requests=requests, table=table)
    content = _user_content(requests[0])
    assert "Options:\nChart type: Bar (latest value). Marks and encoding are set" in content
    assert "It shows each entity's latest value." in content
    assert "Use exactly these entities: France, Japan." in content
    assert "user_instructions" not in content


@pytest.mark.asyncio
async def test_no_options_body_is_unchanged() -> None:
    plain: list[dict[str, object]] = []
    blank: list[dict[str, object]] = []
    await _write(None, requests=plain)
    await _write(PostOptions(tone="punchy", time_preset="all"), requests=blank)
    assert "Options" not in _user_content(plain[0])
    assert json.dumps(plain[0]) == json.dumps(blank[0])


def test_options_prompt_empty() -> None:
    assert options_prompt(None, []) == ""
    assert options_prompt(PostOptions(), []) == ""


# --- critique ---


def test_critique_prompt_accepts_bar_and_keeps_line_checks() -> None:
    assert "bar chart" in CRITIQUE_PROMPT
    assert "year on x and value on y" in CRITIQUE_PROMPT
    assert "entity as a nominal field" in CRITIQUE_PROMPT


@pytest.mark.asyncio
async def test_bad_line_chart_is_still_flagged() -> None:
    # The recorded failing critique is passed through for a line chart; the request still
    # carries the strict line-chart wording and the line spec.
    requests: list[dict[str, object]] = []
    fail_client = make_client("llm_critique_fail.json", requests=requests)
    chart = await _write(None)
    critique = await ActivityEnvironment().run(
        CritiqueActivities(fail_client).critique_chart,
        CritiqueChartInput(topic=TOPIC, table=make_table(), chart=chart),
    )
    assert critique.passed is False
    assert critique.reasons
    assert requests[0]["system"] == CRITIQUE_PROMPT
    assert "year on x and value on y" in cast(str, requests[0]["system"])
    assert '"mark": "line"' in _user_content(requests[0])
    assert _fixture("llm_critique_fail.json")


# --- flow models ---


def test_new_fields_default_for_old_payloads() -> None:
    assert DailyChartInput.model_validate({"topic": "x"}).options is None
    assert ReviewState.model_validate({"status": "drafting"}).option_notes == []
    table = OwidTable.model_validate({"slug": "s", "title": "t", "value_column": "v", "rows": []})
    assert table.matched_countries == []
    assert table.notes == []
    assert FetchOwidInput.model_validate({"topic": "x"}).options is None
    assert (
        WriteChartSpecInput.model_validate({"topic": "x", "table": table.model_dump()}).options
        is None
    )


# --- service ---


def _service(gw: FakeGateway, store: InMemoryStyleStore) -> ChartService:
    return ChartService(gw, Settings(daily_chart_cap=1), styles=store)


@pytest.mark.asyncio
async def test_service_merges_tone_and_passes_options() -> None:
    profile = ChartStyle(tone="friendly", palette="ocean")
    store = InMemoryStyleStore({"a@x.com": profile})
    gw = FakeGateway()
    options = PostOptions(tone="punchy", chart_type="bar")
    await _service(gw, store).start(
        StartChartRequest(topic="t", requested_by="a@x.com", options=options)
    )
    data = gw.started[0][1]
    assert data.style == ChartStyle(tone="punchy", palette="ocean")
    assert data.options == options


@pytest.mark.asyncio
async def test_service_without_options_behaves_as_before() -> None:
    profile = ChartStyle(tone="friendly")
    gw = FakeGateway()
    await _service(gw, InMemoryStyleStore({"a@x.com": profile})).start(
        StartChartRequest(topic="t", requested_by="a@x.com")
    )
    assert gw.started[0][1].style == profile
    assert gw.started[0][1].options is None


@pytest.mark.asyncio
async def test_service_cap_reached_makes_no_store_call() -> None:
    store = InMemoryStyleStore()
    gw = FakeGateway()
    gw.counts["a@x.com"] = 1
    with pytest.raises(Exception, match="limit"):
        await _service(gw, store).start(
            StartChartRequest(topic="t", requested_by="a@x.com", options=PostOptions(tone="punchy"))
        )
    assert store.get_calls == []
