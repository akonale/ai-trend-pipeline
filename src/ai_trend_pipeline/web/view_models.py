"""Pydantic models shaped for templates."""

import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

from pydantic import BaseModel, Field, ValidationError

from ai_trend_pipeline.daily_chart import APPROVAL_TIMEOUT, ReviewState
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
from ai_trend_pipeline.services.candidates import CandidatePage, CandidateSummary
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import CandidateReviewState, candidate_id_for
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
    palette: str = ""
    font: str = ""
    hashtags: str = ""
    signature: str = ""
    hashtags_none: str = ""
    signature_none: str = ""
    save_default: str = ""

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
SAVE_DEFAULT_UNAVAILABLE = (
    "Saving a default is off right now because your saved style could not be loaded."
)
SAVE_DEFAULT_SIGN_IN = "Sign in to save a default style."
STYLE_INTRO = (
    "Your saved style is filled in. Change any field to use something else on this chart only."
)
SAVED_UNKNOWN = "Saved style"
TOPIC_TOO_LONG_ERROR = f"Your topic is too long. Use {MAX_TOPIC_LENGTH} characters or fewer."


class PaletteChoice(BaseModel):
    key: str
    label: str
    colors: list[str]
    checked: bool


class StyleStepView(BaseModel):
    error: str = ""
    topic: str
    change_href: str
    stepper: StepperView
    options: OptionsFormValues
    chart_types: list[ChoiceOption]
    time_presets: list[ChoiceOption]
    tones: list[ChoiceOption]
    palettes: list[PaletteChoice]
    fonts: list[ChoiceOption]
    profile_known: bool
    hashtags_help: str
    signature_help: str
    max_signature: int = MAX_SIGNATURE_CHARS
    can_save_default: bool
    save_note: str = ""
    notice: str = ""
    min_year: int = MIN_YEAR
    max_year: int = MAX_YEAR
    max_countries: int = MAX_COUNTRIES
    max_countries_text: int = MAX_COUNTRIES * (MAX_COUNTRY_CHARS + 2)
    max_instructions: int = MAX_INSTRUCTIONS_CHARS


HASHTAGS_RULE = f"Up to {MAX_HASHTAGS}, separated by spaces or commas."


def _saved(value: str | None) -> str:
    return SAVED_UNKNOWN if value is None else f"Saved: {value.capitalize()}"


def build_style_step_view(
    topic: str,
    options: OptionsFormValues,
    style: ChartStyle | None = None,
    notice: str = "",
    error: str = "",
    save_note: str = "",
) -> StyleStepView:
    topic = topic.strip()
    change_href = f"/?{flow_query(topic, options)}"
    palettes = [
        PaletteChoice(
            key="",
            label=_saved(style.palette if style else None),
            colors=list(PALETTES[style.palette]) if style else [],
            checked=options.palette == "",
        )
    ]
    palettes += [
        PaletteChoice(key=k, label=k.capitalize(), colors=list(v), checked=k == options.palette)
        for k, v in PALETTES.items()
    ]
    if style is None:
        hashtags_help = f"Leave blank to use your saved hashtags. {HASHTAGS_RULE}"
        signature_help = "Leave blank to use your saved signature."
    else:
        tags = " ".join(f"#{t}" for t in style.hashtags) or "None"
        hashtags_help = f"Saved: {tags}. Leave blank to use it. {HASHTAGS_RULE}"
        signature_help = f"Saved: {style.signature or 'None'}. Leave blank to use it."
    return StyleStepView(
        error=error,
        topic=topic,
        change_href=change_href,
        stepper=build_stepper(2, change_href),
        options=options,
        chart_types=_choices(CHART_TYPES, options.chart_type, "Default"),
        time_presets=_choices(TIME_PRESETS, options.time_preset, "Default"),
        tones=_choices(
            {k: k for k in TONES},
            options.tone,
            _saved(style.tone if style else None),
            capitalise=True,
        ),
        palettes=palettes,
        fonts=_choices(
            {k: k for k in FONTS},
            options.font,
            _saved(style.font if style else None),
            capitalise=True,
        ),
        profile_known=style is not None,
        hashtags_help=hashtags_help,
        signature_help=signature_help,
        can_save_default=not save_note,
        save_note=save_note,
        notice=STYLE_INTRO if style is not None else notice,
    )


STYLE_ERRORS = {
    "palette": "Palette: pick one of the listed palettes.",
    "font": "Font: pick one of the listed fonts.",
    "tone": "Tone: pick one of the listed tones.",
    "hashtags": (
        f"Hashtags: use at most {MAX_HASHTAGS}, each 1 to 24 letters, digits or underscores."
    ),
    "signature": (
        f"Signature: use at most {MAX_SIGNATURE_CHARS} printable characters on a single line."
    ),
}

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
    "palette": STYLE_ERRORS["palette"],
    "font": STYLE_ERRORS["font"],
    "hashtags": STYLE_ERRORS["hashtags"],
    "signature": STYLE_ERRORS["signature"],
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
    if options.palette is not None:
        lines.append(OptionLine(label="Palette", value=options.palette.capitalize()))
    if options.font is not None:
        lines.append(OptionLine(label="Font", value=options.font.capitalize()))
    if options.hashtags is not None:
        tags = " ".join(f"#{t}" for t in options.hashtags)
        lines.append(OptionLine(label="Hashtags", value=tags or "None"))
    if options.signature is not None:
        lines.append(OptionLine(label="Signature", value=options.signature or "None"))
    if options.instructions:
        lines.append(OptionLine(label="Instructions", value=options.instructions))
    return lines


BSKY_POST_URI = re.compile(
    r"at://(did:plc:[a-z2-7]{24})/app\.bsky\.feed\.post/([a-z2-7]{13})", re.ASCII
)


def bluesky_post_url(uri: str | None) -> str | None:
    """Public bsky.app URL for a post URI of the exact shape our activity makes, else None."""
    if uri is None:
        return None
    match = BSKY_POST_URI.fullmatch(uri)
    if match is None:
        return None
    return f"https://bsky.app/profile/{match.group(1)}/post/{match.group(2)}"


APPROVAL_HOURS = int(APPROVAL_TIMEOUT.total_seconds() // 3600)
HEADINGS = {
    "drafting": "Drafting your chart",
    "awaiting_decision": "Review your chart",
    "posting": "Posting to Bluesky",
    "approved": "Posted to Bluesky",
    "rejected": "Rejected",
    "expired": "Expired: nobody decided in time",
}
HEADING_SENT = "Sending your decision"
STAGE_MESSAGES = {
    "drafting": "Drafting your chart. This usually takes a minute or two.",
    "posting": "Posting your chart to Bluesky.",
}
STAGE_SENT = "Decision sent, waiting for the workflow."
END_MESSAGES = {
    "approved": "Your chart is live on Bluesky.",
    "rejected": "Nothing was posted.",
    "expired": (
        f"Nobody approved or rejected this chart within {APPROVAL_HOURS} hours, "
        "so nothing was posted."
    ),
}
OUTCOME_TONES: dict[str, Literal["success", "danger", "muted"]] = {
    "approved": "success",
    "rejected": "danger",
    "expired": "muted",
}
POST_LINK_MISSING = "The link to the post is not available for this run."


def critique_summary(passed: bool, attempts: int) -> str:
    noun = "draft" if attempts == 1 else "drafts"
    return f"{'Passed' if passed else 'Failed'} after {attempts} {noun}."


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
    heading: str
    progress_message: str = ""
    is_end: bool = False
    outcome_tone: Literal["success", "danger", "muted", ""] = ""
    end_message: str = ""
    post_url: str | None = None
    post_link_note: str = ""
    another_href: str = "/"
    critique_summary: str = ""


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
    approved = state.status == "approved"
    post_url = bluesky_post_url(state.post_uri) if approved else None
    sending = awaiting and sent
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
        heading=HEADING_SENT if sending else HEADINGS[state.status],
        progress_message=STAGE_SENT if sending else STAGE_MESSAGES.get(state.status, ""),
        is_end=state.status in OUTCOME_TONES,
        outcome_tone=OUTCOME_TONES.get(state.status, ""),
        end_message=END_MESSAGES.get(state.status, ""),
        post_url=post_url,
        post_link_note=POST_LINK_MISSING if approved and post_url is None else "",
        critique_summary=(
            critique_summary(review.critique.passed, review.attempts) if review else ""
        ),
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
    parts = [msg for key, msg in STYLE_ERRORS.items() if key in fields]
    return " ".join(parts) or "Check your style and try again."


INBOX_HEADING = "Story candidates"
INBOX_HREF = "/candidates"
EMPTY_HEADING = "Nothing waiting for review"
EMPTY_BODY = "New candidates show up here when a source finds a story. Check back soon."
EMPTY_ACTION_LABEL = "Check again"
UNAVAILABLE_HEADING = "The inbox is unavailable"
UNAVAILABLE_BODY = (
    "We couldn't reach the candidate service. Stories that were already waiting are not lost."
)
RETRY_LABEL = "Try again"
OLDER_LABEL = "Older stories"
NEWEST_LABEL = "Back to newest"
INVALID_PAGE_NOTICE = "That page link is out of date. Start from the newest stories."
TITLE_FALLBACK = "Untitled story"
ANGLE_FALLBACK = "No angle proposed."
SOURCE_LABELS: dict[str, str] = {
    SourceName.HN.value: "Hacker News",
    SourceName.FAKE.value: "Test source",
}


def source_label(source: str | SourceName) -> str:
    value = source.value if isinstance(source, SourceName) else source
    return SOURCE_LABELS.get(value, value)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def relative_time(observed_at: datetime | None, now: datetime) -> str:
    """Human gap between detection and now; naive timestamps are treated as UTC."""
    if observed_at is None:
        return ""
    moment = _as_utc(observed_at)
    delta = _as_utc(now) - moment
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    if seconds < 604800:
        days = delta.days
        return "1 day ago" if days == 1 else f"{days} days ago"
    return f"on {moment.strftime('%d %b %Y')}"


class CandidateRow(BaseModel):
    href: str
    title: str
    angle: str
    source_label: str
    observed_iso: str | None
    observed_label: str


class CandidateInboxView(BaseModel):
    heading: str
    count_text: str
    rows: list[CandidateRow]
    empty: bool
    empty_heading: str
    empty_body: str
    empty_action_label: str
    empty_href: str
    unavailable: bool
    unavailable_heading: str
    unavailable_body: str
    retry_href: str
    retry_label: str
    notice: str
    older_href: str | None
    older_label: str
    newest_href: str | None
    newest_label: str


def _build_row(summary: CandidateSummary, now: datetime) -> CandidateRow:
    observed = summary.observed_at
    return CandidateRow(
        href=f"/candidates/{quote(summary.candidate_id, safe='')}",
        title=summary.title.strip() or TITLE_FALLBACK,
        angle=summary.proposed_angle.strip() or ANGLE_FALLBACK,
        source_label=source_label(summary.source),
        observed_iso=_as_utc(observed).isoformat() if observed is not None else None,
        observed_label=relative_time(observed, now),
    )


def build_candidate_inbox_view(
    page: CandidatePage,
    *,
    now: datetime,
    page_token: str | None = None,
    notice: str = "",
) -> CandidateInboxView:
    """All inbox labels, fallbacks, relative times and links. Pure: no I/O."""
    rows = [_build_row(summary, now) for summary in page.candidates]
    count = len(rows)
    count_text = (
        f"{count} {'story' if count == 1 else 'stories'} waiting for review." if count else ""
    )
    return CandidateInboxView(
        heading=INBOX_HEADING,
        count_text=count_text,
        rows=rows,
        empty=not rows and not notice,
        empty_heading=EMPTY_HEADING,
        empty_body=EMPTY_BODY,
        empty_action_label=EMPTY_ACTION_LABEL,
        empty_href=INBOX_HREF,
        unavailable=False,
        unavailable_heading=UNAVAILABLE_HEADING,
        unavailable_body=UNAVAILABLE_BODY,
        retry_href=INBOX_HREF,
        retry_label=RETRY_LABEL,
        notice=notice,
        older_href=(
            f"{INBOX_HREF}?page={quote(page.next_token, safe='')}"
            if page.next_token is not None
            else None
        ),
        older_label=OLDER_LABEL,
        newest_href=INBOX_HREF if page_token is not None else None,
        newest_label=NEWEST_LABEL,
    )


def build_candidate_unavailable_view(now: datetime) -> CandidateInboxView:
    return CandidateInboxView(
        heading=INBOX_HEADING,
        count_text="",
        rows=[],
        empty=False,
        empty_heading=EMPTY_HEADING,
        empty_body=EMPTY_BODY,
        empty_action_label=EMPTY_ACTION_LABEL,
        empty_href=INBOX_HREF,
        unavailable=True,
        unavailable_heading=UNAVAILABLE_HEADING,
        unavailable_body=UNAVAILABLE_BODY,
        retry_href=INBOX_HREF,
        retry_label=RETRY_LABEL,
        notice="",
        older_href=None,
        older_label=OLDER_LABEL,
        newest_href=None,
        newest_label=NEWEST_LABEL,
    )


DECISION_HEADING = "Decide this story"
DECISION_NOTE = "Approving only records the story for later. Nothing is published."
APPROVE_LABEL = "Approve story"
REJECT_LABEL = "Reject story"
BACK_LABEL = "Back to candidates"
SOURCE_LINK_LABEL = "Read the original story"
SOURCE_LINK_HINT = "(opens in a new tab)"
SOURCE_MISSING_LABEL = "No source link for this story."
SUMMARY_LABEL = "Summary"
SUMMARY_FALLBACK = "No summary available."
ANGLE_LABEL = "Proposed angle"
REASON_LABEL = "Why this story"
REASON_FALLBACK = "No reason recorded."
METRICS_LABEL = "Source metrics"
METRICS_FALLBACK = "No source metrics for this story."
DETECTED_PREFIX = "Detected"
INVALID_DECISION_ERROR = "Choose Approve or Reject."
METRIC_LABELS: dict[str, str] = {"score": "Points", "comments": "Comments"}
DECISION_NOTICES: dict[str, str] = {
    "approved": "Approved: the story is recorded. Nothing was published.",
    "rejected": "Rejected: the story is out of the queue. Nothing was published.",
    "closed": "That story was already decided or has expired. Nothing changed.",
}
NOT_AVAILABLE_MISSING = (
    "This story is not available",
    "It may have been removed, or the link may be wrong. "
    "The candidate inbox is the best place to start.",
)
NOT_AVAILABLE_CLOSED: dict[str, tuple[str, str]] = {
    "approved": (
        "This story was already approved",
        "It is no longer waiting for review. Approving only records a story; "
        "nothing was published.",
    ),
    "rejected": (
        "This story was already rejected",
        "It is no longer waiting for review. Rejecting only removes a story; "
        "nothing was published.",
    ),
    "expired": (
        "This story expired without a decision",
        "The review window closed before anyone decided. Nothing was published.",
    ),
}
NOT_AVAILABLE_UNKNOWN = (
    "This story is no longer available",
    "It is no longer waiting for review.",
)


class MetricChip(BaseModel):
    label: str
    value: str


def metric_chips(metrics: dict[str, int]) -> list[MetricChip]:
    """Metric labels and values in input order; unknown keys are sentence-cased."""
    return [
        MetricChip(
            label=METRIC_LABELS.get(key) or key.replace("_", " ").capitalize(),
            value=f"{value:,}",
        )
        for key, value in metrics.items()
    ]


def safe_source_url(url: object) -> str | None:
    """Only an absolute http(s) URL with a host is safe to link; anything else is dropped."""
    if url is None:
        return None
    text = str(url)
    try:
        parsed = urlsplit(text)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host:
        return None
    return text


def decision_notice(token: str) -> str:
    """Allow-listed inbox notice text; unknown tokens render nothing."""
    return DECISION_NOTICES.get(token.strip(), "")


class CandidateDetailView(BaseModel):
    page_title: str
    title: str
    source_label: str
    observed_iso: str | None
    observed_text: str
    source_url: str | None
    source_link_label: str
    source_link_hint: str
    source_missing_label: str
    error: str
    summary_label: str
    summary: str
    angle_label: str
    angle: str
    reason_label: str
    selection_reason: str
    metrics_label: str
    metrics: list[MetricChip]
    metrics_note: str
    decision_heading: str
    decision_note: str
    decision_action: str
    approve_label: str
    reject_label: str
    back_href: str
    back_label: str


def build_candidate_detail_view(
    state: CandidateReviewState, *, now: datetime, error: str = ""
) -> CandidateDetailView:
    """Every detail label, fallback, metric, URL and action. Pure: no I/O."""
    candidate = state.candidate
    title = candidate.title.strip() or TITLE_FALLBACK
    observed_iso = _as_utc(candidate.observed_at).isoformat()
    relative = relative_time(candidate.observed_at, now)
    metrics = metric_chips(candidate.metrics)
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    return CandidateDetailView(
        page_title=title,
        title=title,
        source_label=source_label(candidate.source),
        observed_iso=observed_iso,
        observed_text=f"{DETECTED_PREFIX} {relative}" if relative else "",
        source_url=safe_source_url(candidate.url),
        source_link_label=SOURCE_LINK_LABEL,
        source_link_hint=SOURCE_LINK_HINT,
        source_missing_label=SOURCE_MISSING_LABEL,
        error=error,
        summary_label=SUMMARY_LABEL,
        summary=candidate.summary.strip() or SUMMARY_FALLBACK,
        angle_label=ANGLE_LABEL,
        angle=candidate.proposed_angle.strip() or ANGLE_FALLBACK,
        reason_label=REASON_LABEL,
        selection_reason=candidate.selection_reason.strip() or REASON_FALLBACK,
        metrics_label=METRICS_LABEL,
        metrics=metrics,
        metrics_note="" if metrics else METRICS_FALLBACK,
        decision_heading=DECISION_HEADING,
        decision_note=DECISION_NOTE,
        decision_action=f"{INBOX_HREF}/{quote(candidate_id, safe='')}/decide",
        approve_label=APPROVE_LABEL,
        reject_label=REJECT_LABEL,
        back_href=INBOX_HREF,
        back_label=BACK_LABEL,
    )


class CandidateNotAvailableView(BaseModel):
    page_title: str
    heading: str
    body: str
    back_href: str
    back_label: str


def build_candidate_not_available_view(status: str | None) -> CandidateNotAvailableView:
    """Neutral not-available copy: None is a missing candidate, else the closed status."""
    if status is None:
        heading, body = NOT_AVAILABLE_MISSING
    else:
        heading, body = NOT_AVAILABLE_CLOSED.get(str(status), NOT_AVAILABLE_UNKNOWN)
    return CandidateNotAvailableView(
        page_title=heading,
        heading=heading,
        body=body,
        back_href=INBOX_HREF,
        back_label=BACK_LABEL,
    )
