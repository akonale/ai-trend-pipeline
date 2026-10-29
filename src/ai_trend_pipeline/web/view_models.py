"""Pydantic models shaped for templates."""

from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field, ValidationError

from ai_trend_pipeline.daily_chart import ReviewState
from ai_trend_pipeline.post_options import (
    CHART_TYPES,
    MAX_COUNTRIES,
    MAX_COUNTRY_CHARS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_YEAR,
    MIN_YEAR,
    TIME_PRESETS,
    PostOptions,
)
from ai_trend_pipeline.style import (
    FONTS,
    MAX_HASHTAGS,
    MAX_SIGNATURE_CHARS,
    PALETTES,
    TONES,
    ChartStyle,
)

SHELL_TAGLINE = "Charts from Our World in Data, posted to Bluesky once you approve them."


class NavItem(BaseModel):
    label: str
    href: str
    current: bool = False


class ShellView(BaseModel):
    brand: str = "Topic Studio"
    brand_href: str = "/"
    nav: list[NavItem]
    user_email: str | None = None
    env_tag: str = ""
    tagline: str = SHELL_TAGLINE


def build_shell(
    path: str, user_email: str | None, auth_mode: Literal["local", "cloudflare"]
) -> ShellView:
    """Header, nav, chip and footer data for one page. Pure: no I/O."""
    on_new = path in ("/", "/runs") or path.startswith(("/runs/", "/new/"))
    return ShellView(
        nav=[
            NavItem(label="New chart", href="/", current=on_new),
            NavItem(label="Your style", href="/settings", current=path == "/settings"),
        ],
        user_email=user_email,
        env_tag="dev" if auth_mode == "local" else "",
    )


class StepView(BaseModel):
    number: int
    label: str
    state: Literal["done", "current", "upcoming"]
    href: str | None = None
    aria_current: bool = False


class StepperView(BaseModel):
    steps: list[StepView]
    oob: bool = False


STEP_LABELS = ["Topic", "Style", "Review", "Done"]
RUN_STEP = {
    "drafting": 3,
    "awaiting_decision": 3,
    "posting": 3,
    "approved": 4,
    "rejected": 4,
    "expired": 4,
}


def build_stepper(current: int, topic_href: str | None = None, oob: bool = False) -> StepperView:
    """Four-step progress. Only a done step 1 can link (back to the topic box)."""
    steps: list[StepView] = []
    for number, label in enumerate(STEP_LABELS, start=1):
        state: Literal["done", "current", "upcoming"] = (
            "done" if number < current else "current" if number == current else "upcoming"
        )
        steps.append(
            StepView(
                number=number,
                label=label,
                state=state,
                href=topic_href if number == 1 and state == "done" else None,
                aria_current=state == "current",
            )
        )
    return StepperView(steps=steps, oob=oob)


class HealthView(BaseModel):
    status: str
    temporal_address: str


MAX_TOPIC_LENGTH = 200
STYLE_UNAVAILABLE_ERROR = (
    "We couldn't load your style settings right now. Please try again in a moment."
)

EXAMPLE_TOPICS = ["Life expectancy", "CO2 emissions", "Internet users", "Renewable energy"]


class TopicChip(BaseModel):
    label: str
    href: str


def _chips() -> list[TopicChip]:
    return [TopicChip(label=t, href=f"/?topic={quote(t)}") for t in EXAMPLE_TOPICS]


class ChoiceOption(BaseModel):
    key: str
    label: str
    selected: bool


class OptionsFormValues(BaseModel):
    chart_type: str = ""
    time_preset: str = ""
    from_year: str = ""
    to_year: str = ""
    countries: str = ""
    tone: str = ""
    instructions: str = ""

    def any_set(self) -> bool:
        return any(v.strip() for v in self.model_dump().values())


def _choices(
    table: dict[str, str], selected: str, default_label: str, capitalise: bool = False
) -> list[ChoiceOption]:
    items = [ChoiceOption(key="", label=default_label, selected=selected == "")]
    items += [
        ChoiceOption(key=k, label=v.capitalize() if capitalise else v, selected=k == selected)
        for k, v in table.items()
    ]
    return items


class OptionLine(BaseModel):
    label: str
    value: str
    colors: list[str] = []


class HiddenField(BaseModel):
    name: str
    value: str


def _carry(options: OptionsFormValues) -> list[HiddenField]:
    return [HiddenField(name=k, value=v) for k, v in options.model_dump().items() if v.strip()]


def flow_query(topic: str, options: OptionsFormValues) -> str:
    """Query string that carries the topic and any typed options between steps."""
    pairs = [("topic", topic)] + [(f.name, f.value) for f in _carry(options)]
    return urlencode(pairs, quote_via=quote)


class HomeView(BaseModel):
    error: str = ""
    topic: str = ""
    examples: list[TopicChip] = Field(default_factory=_chips)
    carry: list[HiddenField] = []
    stepper: StepperView = Field(default_factory=lambda: build_stepper(1))
    max_topic: int = MAX_TOPIC_LENGTH


def build_home_view(
    topic: str = "", options: OptionsFormValues | None = None, error: str = ""
) -> HomeView:
    return HomeView(
        topic=topic.strip()[:MAX_TOPIC_LENGTH],
        error=error,
        carry=_carry(options or OptionsFormValues()),
    )


STYLE_NOTICE_UNAVAILABLE = "Your saved style could not be loaded right now. Generate still works."
STYLE_NOTICE_SIGN_IN = "Sign in to see your saved style."
STYLE_NOTICE_APPLIED = "Your saved style is applied when you generate."
TOPIC_TOO_LONG_ERROR = f"Your topic is too long. Use {MAX_TOPIC_LENGTH} characters or fewer."


class StyleStepView(BaseModel):
    error: str = ""
    topic: str
    change_href: str
    stepper: StepperView
    options: OptionsFormValues
    chart_types: list[ChoiceOption]
    time_presets: list[ChoiceOption]
    tones: list[ChoiceOption]
    summary: list[OptionLine] = []
    notice: str = ""
    min_year: int = MIN_YEAR
    max_year: int = MAX_YEAR
    max_countries: int = MAX_COUNTRIES
    max_countries_text: int = MAX_COUNTRIES * (MAX_COUNTRY_CHARS + 2)
    max_instructions: int = MAX_INSTRUCTIONS_CHARS


def style_summary_lines(style: ChartStyle) -> list[OptionLine]:
    return [
        OptionLine(
            label="Palette", value=style.palette.capitalize(), colors=list(PALETTES[style.palette])
        ),
        OptionLine(label="Font", value=style.font.capitalize()),
        OptionLine(label="Caption tone", value=style.tone.capitalize()),
        OptionLine(label="Hashtags", value=" ".join(f"#{t}" for t in style.hashtags) or "None"),
        OptionLine(label="Signature", value=style.signature or "None"),
    ]


def build_style_step_view(
    topic: str,
    options: OptionsFormValues,
    style: ChartStyle | None = None,
    notice: str = "",
    error: str = "",
) -> StyleStepView:
    topic = topic.strip()
    change_href = f"/?{flow_query(topic, options)}"
    return StyleStepView(
        error=error,
        topic=topic,
        change_href=change_href,
        stepper=build_stepper(2, change_href),
        options=options,
        chart_types=_choices(CHART_TYPES, options.chart_type, "Default"),
        time_presets=_choices(TIME_PRESETS, options.time_preset, "Default"),
        tones=_choices({k: k for k in TONES}, options.tone, "Your style", capitalise=True),
        summary=style_summary_lines(style) if style is not None else [],
        notice=notice,
    )


OPTIONS_ERRORS = {
    "chart_type": "Chart type: pick line, bar or area.",
    "time_preset": (
        f"Time range: pick a preset or From/To years ({MIN_YEAR} to {MAX_YEAR}, "
        "From not after To), not both."
    ),
    "countries": (
        f"Countries: up to {MAX_COUNTRIES} names, each at most {MAX_COUNTRY_CHARS} characters, "
        "separated by commas."
    ),
    "tone": "Caption tone: pick one of the listed tones.",
    "instructions": f"Instructions: at most {MAX_INSTRUCTIONS_CHARS} characters.",
}


def options_error_message(exc: ValidationError) -> str:
    fields: set[str] = set()
    for e in exc.errors():
        loc = str(e["loc"][0]) if e["loc"] else "time_preset"
        fields.add("time_preset" if loc in ("from_year", "to_year") else loc)
    parts = [msg for key, msg in OPTIONS_ERRORS.items() if key in fields]
    return " ".join(parts) or "Check your options and try again."


STATUS_LABELS = {
    "drafting": "Drafting the chart",
    "awaiting_decision": "Waiting for your decision",
    "posting": "Posting to Bluesky",
    "approved": "Approved and posted",
    "rejected": "Rejected",
    "expired": "Expired without a decision",
}


DECIDED_LABELS = {"posting": "Approved", "approved": "Approved", "rejected": "Rejected"}


def badge_for(status: str, sent: bool) -> tuple[str, str]:
    if status == "awaiting_decision":
        return (
            ("Decision sent", "badge--progress") if sent else ("Needs review", "badge--attention")
        )
    return BADGES[status]


BADGES = {
    "drafting": ("Drafting", "badge--progress"),
    "posting": ("Posting", "badge--progress"),
    "approved": ("Posted", "badge--success"),
    "rejected": ("Rejected", "badge--danger"),
    "expired": ("Expired", "badge--muted"),
}


def _time_text(options: PostOptions) -> str:
    if options.time_preset is not None:
        return TIME_PRESETS[options.time_preset]
    if options.from_year is not None and options.to_year is not None:
        return f"{options.from_year} to {options.to_year}"
    if options.from_year is not None:
        return f"From {options.from_year}"
    return f"Up to {options.to_year}"


def option_lines(options: PostOptions | None) -> list[OptionLine]:
    if options is None:
        return []
    lines: list[OptionLine] = []
    if options.chart_type is not None:
        lines.append(OptionLine(label="Chart type", value=CHART_TYPES[options.chart_type]))
    if (
        options.time_preset is not None
        or options.from_year is not None
        or options.to_year is not None
    ):
        lines.append(OptionLine(label="Time range", value=_time_text(options)))
    if options.countries:
        lines.append(OptionLine(label="Countries", value=", ".join(options.countries)))
    if options.tone is not None:
        lines.append(OptionLine(label="Caption tone", value=options.tone.capitalize()))
    if options.instructions:
        lines.append(OptionLine(label="Instructions", value=options.instructions))
    return lines


class RunView(BaseModel):
    wf_id: str
    status: str
    status_label: str
    badge_label: str
    badge_class: str
    topic: str | None = None
    topic_reason: str | None = None
    caption: str | None = None
    critique_passed: bool | None = None
    critique_reasons: list[str] = []
    attempts: int | None = None
    note: str = ""
    image_url: str | None = None
    can_decide: bool
    polling: bool
    sent: bool = False
    temporal_url: str
    error: str = ""
    requested_by: str | None = None
    decided_by: str | None = None
    decided_label: str = ""
    options: list[OptionLine] = []
    option_notes: list[str] = []
    stepper: StepperView


def build_run_view(
    wf_id: str,
    state: ReviewState,
    temporal_ui_url: str,
    sent: bool = False,
    error: str = "",
    fragment: bool = False,
) -> RunView:
    review = state.review
    awaiting = state.status == "awaiting_decision"
    badge_label, badge_class = badge_for(state.status, sent)
    return RunView(
        wf_id=wf_id,
        status=state.status,
        status_label=STATUS_LABELS[state.status],
        badge_label=badge_label,
        badge_class=badge_class,
        topic=review.topic if review else None,
        topic_reason=review.topic_reason if review else None,
        caption=review.caption if review else None,
        critique_passed=review.critique.passed if review else None,
        critique_reasons=list(review.critique.reasons) if review else [],
        attempts=review.attempts if review else None,
        note=state.note,
        image_url=f"/runs/{wf_id}/chart.png" if review else None,
        can_decide=awaiting and not sent,
        polling=state.status in ("drafting", "posting") or (awaiting and sent),
        sent=sent,
        temporal_url=f"{temporal_ui_url}/namespaces/default/workflows/{quote(wf_id, safe='')}",
        error=error,
        requested_by=state.requested_by,
        decided_by=state.decided_by,
        decided_label=DECIDED_LABELS.get(state.status, ""),
        options=option_lines(state.options),
        option_notes=list(state.option_notes),
        stepper=build_stepper(RUN_STEP[state.status], oob=fragment),
    )


class SwatchOption(BaseModel):
    key: str
    colors: list[str]
    checked: bool


class SettingsView(BaseModel):
    email: str
    palettes: list[SwatchOption]
    fonts: list[ChoiceOption]
    tones: list[ChoiceOption]
    hashtags: str
    signature: str
    max_signature: int = MAX_SIGNATURE_CHARS
    saved: bool = False
    error: str = ""


def build_settings_view(
    email: str,
    style: ChartStyle,
    saved: bool = False,
    error: str = "",
    hashtags_text: str | None = None,
) -> SettingsView:
    return SettingsView(
        email=email,
        palettes=[
            SwatchOption(key=k, colors=list(v), checked=k == style.palette)
            for k, v in PALETTES.items()
        ],
        fonts=[ChoiceOption(key=k, label=k.capitalize(), selected=k == style.font) for k in FONTS],
        tones=[ChoiceOption(key=k, label=k.capitalize(), selected=k == style.tone) for k in TONES],
        hashtags=hashtags_text
        if hashtags_text is not None
        else " ".join(f"#{t}" for t in style.hashtags),
        signature=style.signature,
        saved=saved,
        error=error,
    )


def style_error_message(exc: ValidationError) -> str:
    fields = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
    parts: list[str] = []
    if "palette" in fields:
        parts.append("Palette: pick one of the listed palettes.")
    if "font" in fields:
        parts.append("Font: pick one of the listed fonts.")
    if "tone" in fields:
        parts.append("Tone: pick one of the listed tones.")
    if "hashtags" in fields:
        parts.append(
            f"Hashtags: use at most {MAX_HASHTAGS}, each 1 to 24 letters, digits or underscores."
        )
    if "signature" in fields:
        parts.append(
            f"Signature: use at most {MAX_SIGNATURE_CHARS} printable characters on a single line."
        )
    return " ".join(parts) or "Check your style and try again."
