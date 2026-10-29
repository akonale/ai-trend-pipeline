"""Model and ID tests for the story candidate workflow."""

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from temporalio.contrib.pydantic import pydantic_data_converter

from ai_trend_pipeline.services.candidates import (
    InvalidPageToken,
    decode_page_token,
    encode_page_token,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S,
    CandidateDecision,
    StoryCandidate,
    StoryCandidateInput,
    candidate_id_for,
)

OBSERVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_candidate(**overrides: object) -> StoryCandidate:
    payload: dict[str, object] = {
        "source": SourceName.HN,
        "external_id": "123",
        "title": "A story",
        "url": "https://example.com/story",
        "published_at": datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        "observed_at": OBSERVED,
        "summary": "A short summary",
        "proposed_angle": "Why it matters",
        "selection_reason": "Novel and useful",
        "metrics": {"score": 10, "comments": 2},
    }
    payload.update(overrides)
    return StoryCandidate.model_validate(payload)


def test_candidate_id_is_deterministic_and_versioned() -> None:
    candidate_id = candidate_id_for(SourceName.HN, "123")
    expected = hashlib.sha256(b"hn\x1f123").hexdigest()[:16]
    assert candidate_id == f"candidate-v1-{expected}"
    assert candidate_id.startswith("candidate-v1-")
    suffix = candidate_id.removeprefix("candidate-v1-")
    assert len(suffix) == 16
    int(suffix, 16)
    assert candidate_id_for(SourceName.HN, "123") == candidate_id


def test_candidate_id_changes_with_source_or_external_id() -> None:
    base = candidate_id_for(SourceName.HN, "123")
    assert candidate_id_for(SourceName.HN, "124") != base
    assert candidate_id_for(SourceName.FAKE, "123") != base


def test_story_candidate_input_round_trips_through_pydantic_data_converter_under_4kb() -> None:
    data = StoryCandidateInput(
        candidate=make_candidate(
            external_id="e" * 128,
            title="t" * 200,
            url="https://example.com/" + "a" * 1000,
            summary="s" * 500,
            proposed_angle="p" * 500,
            selection_reason="r" * 500,
            metrics={"one": 1, "two": 2, "three": 3, "four": 4},
        ),
        review_timeout_seconds=604800,
    )
    converter = pydantic_data_converter.payload_converter
    payload = converter.to_payload(data)
    assert len(payload.data) < 4096
    assert converter.from_payload(payload, StoryCandidateInput) == data


def test_story_candidate_rejects_extra_fields_overlong_text_bad_urls_and_too_many_metrics() -> None:
    with pytest.raises(ValidationError):
        make_candidate(extra="nope")
    for external_id in ("", "   ", "e" * 129):
        with pytest.raises(ValidationError):
            make_candidate(external_id=external_id)
    for title in ("", "   ", "t" * 201):
        with pytest.raises(ValidationError):
            make_candidate(title=title)
    for field in ("summary", "proposed_angle", "selection_reason"):
        for value in ("", "   ", "x" * 501):
            with pytest.raises(ValidationError):
                make_candidate(**{field: value})
    for url in ("ftp://example.com/x", "javascript:alert(1)", "http://"):
        with pytest.raises(ValidationError):
            make_candidate(url=url)
    with pytest.raises(ValidationError):
        make_candidate(metrics={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
    with pytest.raises(ValidationError):
        make_candidate(observed_at=datetime(2026, 10, 1, 12, 0))
    shifted = make_candidate(
        published_at=datetime(2026, 10, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    )
    assert shifted.published_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_story_candidate_input_bounds_timeout_and_forbids_extra_fields() -> None:
    assert StoryCandidateInput(candidate=make_candidate()).review_timeout_seconds == 172800
    assert DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S == 172800
    for bad in (0, 604801):
        with pytest.raises(ValidationError):
            StoryCandidateInput(candidate=make_candidate(), review_timeout_seconds=bad)
    with pytest.raises(ValidationError):
        StoryCandidateInput.model_validate(
            {"candidate": make_candidate().model_dump(), "extra": "x"}
        )


def test_candidate_decision_strips_decided_by_and_bounds_note() -> None:
    decision = CandidateDecision(note="  please approve  ", decided_by="  alice  ")
    assert decision.note == "please approve"
    assert decision.decided_by == "alice"
    assert CandidateDecision(decided_by="   ").decided_by is None
    assert CandidateDecision().note == ""
    assert CandidateDecision(note="n" * 500).note == "n" * 500
    with pytest.raises(ValidationError):
        CandidateDecision(note="n" * 501)
    with pytest.raises(ValidationError):
        CandidateDecision(decided_by="d" * 321)
    with pytest.raises(ValidationError):
        CandidateDecision.model_validate({"extra": "x"})
    with pytest.raises(ValidationError):
        decision.note = "changed"


def test_page_token_round_trips_and_invalid_token_raises() -> None:
    raw = b"\x00\xffserver-page-token\x01"
    token = encode_page_token(raw)
    assert "=" not in token
    assert decode_page_token(token) == raw
    for bad in ("!!!", "not base64!", "a", ""):
        with pytest.raises(InvalidPageToken):
            decode_page_token(bad)
