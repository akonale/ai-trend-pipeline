"""Tests for per-chart style overrides (task 039): model, merge, service, routes, view, replay."""

import html
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.daily_chart import DAILY_CHART_TASK_QUEUE, DailyChartInput, DailyChartResult
from ai_trend_pipeline.post_options import (
    STYLE_OVERRIDE_FIELDS,
    PostOptions,
    merge_style,
    options_from_form,
)
from ai_trend_pipeline.services.charts import (
    ChartService,
    DailyCapReached,
    StartChartRequest,
)
from ai_trend_pipeline.services.styles import StyleService, StyleStoreUnavailable
from ai_trend_pipeline.style import (
    FONTS,
    PALETTES,
    ChartStyle,
    check_font,
    check_hashtags,
    check_palette,
    check_signature,
    check_tone,
)
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import (
    DEV_USER_EMAIL,
    get_chart_service,
    get_style_service,
)
from ai_trend_pipeline.web.view_models import (
    SAVE_DEFAULT_SIGN_IN,
    SAVE_DEFAULT_UNAVAILABLE,
    STYLE_UNAVAILABLE_ERROR,
    OptionsFormValues,
    flow_query,
    option_lines,
)
from tests.style_fakes import InMemoryStyleStore
from tests.test_chart_service import FakeGateway
from tests.test_chart_spec import _run_styled  # pyright: ignore[reportPrivateUsage]
from tests.test_daily_chart import (
    TOPIC,
    FakeDailyActivities,
    make_worker,
    start_env,
    wait_for_state,
)
from tests.test_web import RecGateway
from tests.test_web_settings import BrokenStore

CSS = (Path(__file__).parent.parent / "src/ai_trend_pipeline/web/static/app.css").read_text()
PROFILE = ChartStyle(
    palette="ocean", font="serif", tone="punchy", hashtags=["ai", "data"], signature="By Ash"
)
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


class SaveFailsStore:
    """get works; save raises StyleStoreUnavailable."""

    def __init__(self, profile: ChartStyle | None = None) -> None:
        self.profile = profile
        self.saves = 0

    async def get(self, email: str) -> ChartStyle | None:
        return self.profile

    async def save(self, email: str, style: ChartStyle) -> None:
        self.saves += 1
        raise StyleStoreUnavailable


class OrderStore(InMemoryStyleStore):
    def __init__(self, log: list[str], styles: dict[str, ChartStyle] | None = None) -> None:
        super().__init__(styles)
        self.log = log

    async def save(self, email: str, style: ChartStyle) -> None:
        self.log.append("save")
        await super().save(email, style)


class OrderGateway(FakeGateway):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    async def start(self, wf_id: str, data: DailyChartInput) -> None:
        self.log.append("start")
        await super().start(wf_id, data)


def web_client(
    store: object,
    gateway: RecGateway | None = None,
    mode: Literal["local", "cloudflare"] = "local",
) -> TestClient:
    settings = Settings(auth_mode=mode)
    app = create_app(settings)
    chart_service = ChartService(gateway or RecGateway(), settings, styles=store)  # pyright: ignore[reportArgumentType]  # structural fake
    style_service = StyleService(store)  # pyright: ignore[reportArgumentType]  # structural fake

    async def fake_chart() -> ChartService:
        return chart_service

    async def fake_style() -> StyleService:
        return style_service

    app.dependency_overrides[get_chart_service] = fake_chart
    app.dependency_overrides[get_style_service] = fake_style
    return TestClient(app)


def stored(profile: ChartStyle | None = PROFILE) -> InMemoryStyleStore:
    return InMemoryStyleStore({DEV_USER_EMAIL: profile} if profile else {})


# ---- check_* functions ----


@pytest.mark.parametrize(
    ("check", "valid", "invalid"),
    [
        (check_palette, "ocean", "neon"),
        (check_font, "mono", "comic"),
        (check_tone, "punchy", "angry"),
    ],
)
def test_check_functions_match_chart_style(
    check: Callable[[str], str], valid: str, invalid: str
) -> None:
    assert check(valid) == valid
    with pytest.raises(ValueError, match=f"unknown .* {invalid!r}"):
        check(invalid)


def test_check_hashtags_and_signature() -> None:
    assert check_hashtags(["a", "b_1"]) == ["a", "b_1"]
    with pytest.raises(ValueError, match="at most 3 hashtags"):
        check_hashtags(["a", "b", "c", "d"])
    with pytest.raises(ValueError, match="must be 1 to 24 letters"):
        check_hashtags(["bad-tag"])
    assert check_signature("  By Ash  ") == "By Ash"
    with pytest.raises(ValueError, match="printable"):
        check_signature("a\x07b")
    with pytest.raises(ValueError, match="at most 40"):
        check_signature("x" * 41)
    with pytest.raises(ValidationError) as exc:
        ChartStyle(signature="x" * 41)
    assert "signature must be at most 40 characters" in str(exc.value)


# ---- PostOptions fields ----


def test_post_options_accepts_none_empty_and_values() -> None:
    assert PostOptions().palette is None
    assert PostOptions().hashtags is None
    options = PostOptions(palette="ink", font="mono", hashtags=[], signature="")
    assert options.hashtags == [] and options.signature == ""
    assert PostOptions(signature="  hi ").signature == "hi"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("palette", "neon"),
        ("font", "comic"),
        ("hashtags", ["bad-tag"]),
        ("hashtags", ["a", "b", "c", "d"]),
        ("signature", "x" * 41),
    ],
)
def test_post_options_rejects_bad_values_with_field_loc(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        PostOptions.model_validate({field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


def test_old_payload_without_new_fields_validates() -> None:
    options = PostOptions.model_validate({"chart_type": "bar", "tone": "punchy"})
    assert [getattr(options, f) for f in ("palette", "font", "hashtags", "signature")] == [None] * 4
    assert STYLE_OVERRIDE_FIELDS == ("palette", "font", "tone", "hashtags", "signature")


# ---- merge_style ----

OVERRIDES: list[tuple[str, object, object]] = [
    ("palette", "sunset", "ocean"),
    ("font", "mono", "serif"),
    ("tone", "academic", "punchy"),
    ("hashtags", ["x"], ["ai", "data"]),
    ("signature", "Me", "By Ash"),
]


@pytest.mark.parametrize(("field", "override", "from_profile"), OVERRIDES)
def test_merge_precedence_per_field(field: str, override: object, from_profile: object) -> None:
    merged = merge_style(PROFILE, PostOptions.model_validate({field: override}))
    assert merged is not None
    assert getattr(merged, field) == override
    for other, _, expected in OVERRIDES:
        if other != field:
            assert getattr(merged, other) == expected
    # None inherits the profile; no profile falls back to the default
    assert merge_style(PROFILE, PostOptions(chart_type="bar")) is PROFILE
    no_profile = merge_style(None, PostOptions.model_validate({field: override}))
    assert no_profile == ChartStyle.model_validate({field: override})


def test_merge_without_style_overrides_returns_same_object() -> None:
    assert merge_style(PROFILE, PostOptions(chart_type="bar")) is PROFILE
    assert merge_style(PROFILE, None) is PROFILE
    assert merge_style(None, PostOptions(chart_type="bar")) is None


def test_merge_clear_semantics() -> None:
    merged = merge_style(PROFILE, PostOptions(hashtags=[], signature=""))
    assert merged == PROFILE.model_copy(update={"hashtags": [], "signature": ""})


# ---- options_from_form ----


def test_options_from_form_new_keywords() -> None:
    assert options_from_form("", "", "", "", "", "", "", palette=" ", font="", hashtags="") is None
    options = options_from_form(
        "", "", "", "", "", "", "", palette="ink", font="mono", hashtags="#ai, data"
    )
    assert options is not None
    assert (options.palette, options.font, options.hashtags) == ("ink", "mono", ["ai", "data"])
    assert options.signature is None
    options = options_from_form("", "", "", "", "", "", "", signature="  Me ")
    assert options is not None and options.signature == "Me"
    assert options_from_form("", "", "", "", "", "", "", hashtags="#  ,") is None


def test_options_from_form_none_boxes_win_over_text() -> None:
    options = options_from_form(
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        hashtags="#ai",
        signature="Me",
        hashtags_none=True,
        signature_none=True,
    )
    assert options is not None
    assert options.hashtags == [] and options.signature == ""


# ---- ChartService ----


def styled_service(gw: FakeGateway, store: InMemoryStyleStore) -> ChartService:
    return ChartService(gw, Settings(), now=lambda: NOW, styles=store)


@pytest.mark.asyncio
async def test_start_passes_merged_style_and_options_without_saving() -> None:
    gw, store = FakeGateway(), InMemoryStyleStore({"a@x.com": PROFILE})
    options = PostOptions(palette="sunset", signature="")
    await styled_service(gw, store).start(
        StartChartRequest(topic="t", requested_by="a@x.com", options=options)
    )
    data = gw.started[0][1]
    assert data.options == options
    assert data.style == PROFILE.model_copy(update={"palette": "sunset", "signature": ""})
    assert store.styles == {"a@x.com": PROFILE}


@pytest.mark.asyncio
async def test_start_save_as_default_saves_effective_style_before_start() -> None:
    log: list[str] = []
    gw, store = OrderGateway(log), OrderStore(log, {"a@x.com": PROFILE})
    await ChartService(gw, Settings(), now=lambda: NOW, styles=store).start(
        StartChartRequest(
            topic="t",
            requested_by="a@x.com",
            options=PostOptions(palette="ink"),
            save_as_default=True,
        )
    )
    assert log == ["save", "start"]
    assert store.styles["a@x.com"] == PROFILE.model_copy(update={"palette": "ink"})


@pytest.mark.asyncio
async def test_start_save_as_default_without_profile_uses_defaults() -> None:
    gw, store = FakeGateway(), InMemoryStyleStore()
    await styled_service(gw, store).start(
        StartChartRequest(
            topic="t",
            requested_by="a@x.com",
            options=PostOptions(font="mono"),
            save_as_default=True,
        )
    )
    assert store.styles["a@x.com"] == ChartStyle(font="mono")


@pytest.mark.asyncio
async def test_start_save_as_default_ignored_without_requester() -> None:
    gw, store = FakeGateway(), InMemoryStyleStore()
    await styled_service(gw, store).start(StartChartRequest(topic="t", save_as_default=True))
    assert store.styles == {} and store.get_calls == [] and len(gw.started) == 1


@pytest.mark.asyncio
async def test_start_save_failure_propagates_and_starts_nothing() -> None:
    gw, store = FakeGateway(), SaveFailsStore(PROFILE)
    service = ChartService(gw, Settings(), now=lambda: NOW, styles=store)  # pyright: ignore[reportArgumentType]  # structural fake
    with pytest.raises(StyleStoreUnavailable):
        await service.start(
            StartChartRequest(topic="t", requested_by="a@x.com", save_as_default=True)
        )
    assert gw.started == [] and store.saves == 1


@pytest.mark.asyncio
async def test_start_cap_reached_touches_no_store() -> None:
    gw, store = FakeGateway(), InMemoryStyleStore({"a@x.com": PROFILE})
    gw.counts["a@x.com"] = 10
    with pytest.raises(DailyCapReached):
        await styled_service(gw, store).start(
            StartChartRequest(
                topic="t",
                requested_by="a@x.com",
                options=PostOptions(palette="ink"),
                save_as_default=True,
            )
        )
    assert store.get_calls == [] and store.styles == {"a@x.com": PROFILE} and gw.started == []


# ---- POST /runs ----


def started(gateway: RecGateway) -> DailyChartInput:
    return gateway.started[0][1]


def test_post_palette_and_font_override_leave_profile_alone() -> None:
    gateway, store = RecGateway(), stored()
    res = web_client(store, gateway).post(
        "/runs", data={"topic": "abc", "palette": "sunset", "font": "mono"}, follow_redirects=False
    )
    assert res.status_code == 303
    data = started(gateway)
    assert data.style == PROFILE.model_copy(update={"palette": "sunset", "font": "mono"})
    assert data.options is not None and data.options.palette == "sunset"
    assert store.styles[DEV_USER_EMAIL] == PROFILE
    web_client(store, gateway).post("/runs", data={"topic": "abc"}, follow_redirects=False)
    assert gateway.started[1][1].style == PROFILE


def test_post_save_default_saves_effective_style() -> None:
    gateway, store = RecGateway(), stored()
    res = web_client(store, gateway).post(
        "/runs",
        data={"topic": "abc", "palette": "ink", "signature": "Me", "save_default": "1"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    expected = PROFILE.model_copy(update={"palette": "ink", "signature": "Me"})
    assert store.styles[DEV_USER_EMAIL] == expected
    assert started(gateway).style == expected


def test_post_save_failure_is_503_and_keeps_values() -> None:
    gateway = RecGateway()
    res = web_client(SaveFailsStore(PROFILE), gateway).post(
        "/runs",
        data={"topic": "abc", "hashtags": "#ai #x", "save_default": "1", "signature": "Me"},
    )
    assert res.status_code == 503
    assert STYLE_UNAVAILABLE_ERROR in html.unescape(res.text)
    assert 'value="#ai #x"' in res.text and 'value="Me"' in res.text
    assert re.search(r'name="save_default"[^>]*checked', res.text)
    assert gateway.started == []


def test_post_at_cap_with_save_default_is_429_and_store_unchanged() -> None:
    gateway, store = RecGateway(), stored()
    gateway.count = 10
    res = web_client(store, gateway).post(
        "/runs", data={"topic": "abc", "palette": "ink", "save_default": "1"}
    )
    assert res.status_code == 429
    assert store.styles == {DEV_USER_EMAIL: PROFILE} and store.get_calls == []
    assert gateway.started == []


@pytest.mark.parametrize(
    ("data", "message", "kept"),
    [
        ({"palette": "neon"}, "Palette: pick one of the listed palettes.", ""),
        ({"font": "comic"}, "Font: pick one of the listed fonts.", ""),
        (
            {"hashtags": "a b c d"},
            "Hashtags: use at most 3, each 1 to 24 letters, digits or underscores.",
            'value="a b c d"',
        ),
        (
            {"hashtags": "bad-tag"},
            "Hashtags: use at most 3, each 1 to 24 letters, digits or underscores.",
            'value="bad-tag"',
        ),
        (
            {"signature": "x" * 41},
            "Signature: use at most 40 printable characters on a single line.",
            "",
        ),
    ],
)
def test_post_invalid_style_values_are_400(data: dict[str, str], message: str, kept: str) -> None:
    gateway, store = RecGateway(), stored()
    res = web_client(store, gateway).post("/runs", data={"topic": "abc", **data})
    assert res.status_code == 400
    assert message in res.text and 'role="alert"' in res.text
    assert 'class="stepper"' in res.text and "Set up this chart" in res.text
    assert kept in res.text
    assert gateway.started == [] and store.get_calls == []


def test_post_hashtags_none_clears_for_this_run() -> None:
    gateway = RecGateway()
    web_client(stored(), gateway).post(
        "/runs",
        data={"topic": "abc", "hashtags": "#ai", "hashtags_none": "1"},
        follow_redirects=False,
    )
    data = started(gateway)
    assert data.options is not None and data.options.hashtags == []
    assert data.style is not None and data.style.hashtags == []
    assert data.style.signature == "By Ash"


# ---- GET /new/style and GET / ----


def test_get_style_shows_prefilled_panel() -> None:
    store = stored()
    text = web_client(store).get("/new/style?topic=a").text
    assert 'aria-labelledby="style-panel-h"' in text and "Style for this chart" in text
    assert "Saved: Ocean" in text and "Saved: Serif" in text and "Saved: Punchy" in text
    assert re.search(r'<input type="radio" name="palette" value="" checked>', text)
    assert 'class="swatch-colors"' in text
    assert (
        "Saved: #ai #data. Leave blank to use it. Up to 3, separated by spaces or commas." in text
    )
    assert "Saved: By Ash. Leave blank to use it." in text
    assert 'name="save_default"' in text and "Save as my default" in text
    assert "Your saved style is filled in." in text
    assert 'href="/settings"' in text
    assert store.get_calls == [DEV_USER_EMAIL]


def test_get_style_without_saved_hashtags_says_none() -> None:
    text = web_client(stored(None)).get("/new/style?topic=a").text
    assert "Saved: None. Leave blank to use it. Up to 3" in text
    assert "Saved: None. Leave blank to use it.</p>" in text
    assert "Saved: Classic" in text


def test_get_style_prefills_typed_overrides_and_carries_them() -> None:
    query = "topic=a&palette=ink&hashtags=%23x&hashtags_none=1&signature_none=1&save_default=1"
    text = web_client(stored()).get(f"/new/style?{query}").text
    assert re.search(r'value="ink"\s+checked', text)
    assert 'value="#x"' in text
    for name in ("hashtags_none", "signature_none", "save_default"):
        assert re.search(rf'name="{name}"[^>]*checked', text), name
    values = OptionsFormValues(
        palette="ink", hashtags="#x", hashtags_none="1", signature_none="1", save_default="1"
    )
    assert f'href="/new?{flow_query("a", values)}"' in html.unescape(text)


def test_get_style_store_down_is_200_without_checkbox() -> None:
    res = web_client(BrokenStore(StyleStoreUnavailable())).get("/new/style?topic=a")
    assert res.status_code == 200
    assert "could not be loaded right now. Generate still works." in res.text
    assert SAVE_DEFAULT_UNAVAILABLE in res.text
    assert 'name="save_default"' not in res.text
    assert "Saved style" in res.text and "Generate chart" in res.text


def test_get_style_cloudflare_without_header_asks_to_sign_in() -> None:
    res = web_client(BrokenStore(), mode="cloudflare").get("/new/style?topic=a")
    assert res.status_code == 200
    assert "Sign in to save a default style." in res.text and SAVE_DEFAULT_SIGN_IN in res.text
    assert 'name="save_default"' not in res.text


def test_home_reemits_new_fields_as_hidden_carry() -> None:
    text = web_client(stored()).get("/new?topic=a&palette=ink&save_default=1").text
    assert '<input type="hidden" name="palette" value="ink">' in text
    assert '<input type="hidden" name="save_default" value="1">' in text


# ---- view models ----


def test_option_lines_new_fields() -> None:
    lines = option_lines(PostOptions(tone="punchy", palette="okabe-ito", font="mono"))
    assert [(line.label, line.value) for line in lines] == [
        ("Caption tone", "Punchy"),
        ("Palette", "Okabe-ito"),
        ("Font", "Mono"),
    ]
    lines = option_lines(PostOptions(hashtags=[], signature="", instructions="x"))
    assert [(line.label, line.value) for line in lines] == [
        ("Hashtags", "None"),
        ("Signature", "None"),
        ("Instructions", "x"),
    ]
    lines = option_lines(PostOptions(hashtags=["a", "b"], signature="Me"))
    assert [(line.label, line.value) for line in lines] == [
        ("Hashtags", "#a #b"),
        ("Signature", "Me"),
    ]


def test_flow_query_unchanged_for_old_fields() -> None:
    assert flow_query("a", OptionsFormValues(tone="punchy")) == "topic=a&tone=punchy"


# ---- chart output and replay ----


@pytest.mark.asyncio
async def test_overrides_change_chart_spec() -> None:
    first = merge_style(PROFILE, PostOptions(palette="sunset"))
    second = merge_style(PROFILE, PostOptions(font="mono"))
    results = [await _run_styled(first), await _run_styled(second)]
    c1 = cast(dict[str, dict[str, object]], results[0].vega_lite_spec["config"])
    c2 = cast(dict[str, dict[str, object]], results[1].vega_lite_spec["config"])
    assert c1["range"]["category"] == list(PALETTES["sunset"])
    assert c2["range"]["category"] == list(PALETTES["ocean"])
    assert c1["font"] == FONTS["serif"] and c2["font"] == FONTS["mono"]
    assert c1["range"]["category"] != c2["range"]["category"] and c1["font"] != c2["font"]


@pytest.mark.asyncio
async def test_pre_039_payload_replays_to_awaiting_decision() -> None:
    raw: dict[str, object] = {
        "topic": TOPIC,
        "requested_by": "a@b.c",
        "options": {"chart_type": "bar", "tone": "punchy", "countries": [], "instructions": ""},
        "style": {
            "palette": "ocean",
            "font": "serif",
            "tone": "punchy",
            "hashtags": ["ai"],
            "signature": "Me",
        },
    }
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await env.client.start_workflow(
            "DailyChartWorkflow",
            raw,
            id=f"wf-{uuid.uuid4()}",
            task_queue=DAILY_CHART_TASK_QUEUE,
            result_type=DailyChartResult,
        )
        state = await wait_for_state(handle, "awaiting_decision")
    for options in (state.options, fakes.write_inputs[0].options):
        assert options is not None and options.chart_type == "bar"
        assert options.palette is None and options.font is None
        assert options.hashtags is None and options.signature is None
    assert fakes.write_inputs[0].style == ChartStyle(
        palette="ocean", font="serif", tone="punchy", hashtags=["ai"], signature="Me"
    )


def test_css_rules() -> None:
    assert ".style-panel > label" in CSS
    assert ".check-row" in CSS and ".save-default" in CSS
    assert ".style-summary" not in CSS
    assert ".chart-options label" in CSS
