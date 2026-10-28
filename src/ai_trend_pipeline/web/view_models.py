"""Pydantic models shaped for templates."""

from urllib.parse import quote

from pydantic import BaseModel, Field

from ai_trend_pipeline.daily_chart import ReviewState


class HealthView(BaseModel):
    status: str
    temporal_address: str


MAX_TOPIC_LENGTH = 200

EXAMPLE_TOPICS = ["Life expectancy", "CO2 emissions", "Internet users", "Renewable energy"]


class TopicChip(BaseModel):
    label: str
    href: str


def _chips() -> list[TopicChip]:
    return [TopicChip(label=t, href=f"/?topic={quote(t)}") for t in EXAMPLE_TOPICS]


class HomeView(BaseModel):
    error: str = ""
    topic: str = ""
    examples: list[TopicChip] = Field(default_factory=_chips)


def build_home_view(topic: str = "") -> HomeView:
    return HomeView(topic=topic.strip()[:MAX_TOPIC_LENGTH])


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


def build_run_view(
    wf_id: str, state: ReviewState, temporal_ui_url: str, sent: bool = False, error: str = ""
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
    )
