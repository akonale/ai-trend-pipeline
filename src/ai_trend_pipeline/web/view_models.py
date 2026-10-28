"""Pydantic models shaped for templates."""

from urllib.parse import quote

from pydantic import BaseModel

from ai_trend_pipeline.daily_chart import ReviewState


class HealthView(BaseModel):
    status: str
    temporal_address: str


class HomeView(BaseModel):
    error: str = ""
    topic: str = ""


STATUS_LABELS = {
    "drafting": "Drafting the chart",
    "awaiting_decision": "Waiting for your decision",
    "posting": "Posting to Bluesky",
    "approved": "Approved and posted",
    "rejected": "Rejected",
    "expired": "Expired without a decision",
}


DECIDED_LABELS = {"posting": "Approved", "approved": "Approved", "rejected": "Rejected"}


class RunView(BaseModel):
    wf_id: str
    status: str
    status_label: str
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
    return RunView(
        wf_id=wf_id,
        status=state.status,
        status_label=STATUS_LABELS[state.status],
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
