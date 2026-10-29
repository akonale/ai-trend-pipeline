"""Model, helper and ID tests for the continuous source ingestion workflow."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from temporalio.contrib.pydantic import pydantic_data_converter

from ai_trend_pipeline.source_ingestion import (
    MAX_CURSOR_LENGTH,
    MAX_PROCESSED_ID_LENGTH,
    MAX_PROCESSED_IDS,
    SourceIngestionInput,
    SourceIngestionResult,
    SourceIngestionState,
    SourceIngestionWorkflow,
    advance_cursor,
    append_processed_ids,
    filter_new_events,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceEvent, SourceName
from ai_trend_pipeline.story_candidate import DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S
from ai_trend_pipeline.story_selection import (
    DEFAULT_MAX_CANDIDATES,
    MAX_BATCH_EVENTS,
    MAX_CANDIDATES,
)

OBSERVED = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def event(external_id: str, title: str | None = None) -> SourceEvent:
    return SourceEvent(
        source=SourceName.HN,
        external_id=external_id,
        title=title or f"Story {external_id}",
        observed_at=OBSERVED,
    )


def test_input_defaults_and_bounds() -> None:
    data = SourceIngestionInput(source=SourceName.HN)
    assert data.source is SourceName.HN
    assert data.poll_limit == 30
    assert data.poll_interval_seconds == 300
    assert data.max_candidates == DEFAULT_MAX_CANDIDATES == 5
    assert data.max_polls_per_run == 50
    assert data.review_timeout_seconds == DEFAULT_CANDIDATE_REVIEW_TIMEOUT_S == 172800
    assert data.cursor is None
    assert data.processed_ids == ()
    assert data.polls_in_run == 0
    assert data.total_polls == 0

    assert SourceIngestionInput(source=SourceName.HN, poll_limit=100).poll_limit == 100
    assert SourceIngestionInput(source=SourceName.HN, poll_limit=1).poll_limit == 1
    assert (
        SourceIngestionInput(source=SourceName.HN, poll_interval_seconds=3600).poll_interval_seconds
        == 3600
    )
    assert (
        SourceIngestionInput(source=SourceName.HN, poll_interval_seconds=30).poll_interval_seconds
        == 30
    )
    assert (
        SourceIngestionInput(source=SourceName.HN, max_candidates=MAX_CANDIDATES).max_candidates
        == 20
    )
    assert (
        SourceIngestionInput(source=SourceName.HN, max_polls_per_run=500).max_polls_per_run == 500
    )
    assert (
        SourceIngestionInput(
            source=SourceName.HN, review_timeout_seconds=604800
        ).review_timeout_seconds
        == 604800
    )

    for kwargs in (
        {"poll_limit": 0},
        {"poll_limit": 101},
        {"poll_interval_seconds": 29},
        {"poll_interval_seconds": 3601},
        {"max_candidates": 0},
        {"max_candidates": 21},
        {"max_polls_per_run": 0},
        {"max_polls_per_run": 501},
        {"review_timeout_seconds": 0},
        {"review_timeout_seconds": 604801},
        {"polls_in_run": -1},
        {"total_polls": -1},
    ):
        with pytest.raises(ValidationError):
            SourceIngestionInput.model_validate({"source": "hn", **kwargs})

    with pytest.raises(ValidationError):
        SourceIngestionInput.model_validate({"source": "hn", "extra": "x"})
    with pytest.raises(ValidationError):
        SourceIngestionInput.model_validate({"source": "unknown"})
    with pytest.raises(ValidationError):
        data.cursor = "changed"


def test_processed_ids_must_be_unique_and_bounded() -> None:
    with pytest.raises(ValidationError):
        SourceIngestionInput(source=SourceName.HN, processed_ids=("a", "a"))
    many = tuple(str(index) for index in range(MAX_PROCESSED_IDS + 1))
    with pytest.raises(ValidationError):
        SourceIngestionInput(source=SourceName.HN, processed_ids=many)
    maximum = tuple(str(index) for index in range(MAX_PROCESSED_IDS))
    parsed = SourceIngestionInput(source=SourceName.HN, processed_ids=maximum)
    assert parsed.processed_ids == maximum

    for bad in ("", "   ", "x" * (MAX_PROCESSED_ID_LENGTH + 1)):
        with pytest.raises(ValidationError):
            SourceIngestionInput(source=SourceName.HN, processed_ids=(bad,))
    assert SourceIngestionInput(
        source=SourceName.HN, processed_ids=("  trimmed  ",)
    ).processed_ids == ("trimmed",)

    with pytest.raises(ValidationError):
        SourceIngestionInput(source=SourceName.HN, cursor="x" * (MAX_CURSOR_LENGTH + 1))
    assert SourceIngestionInput(source=SourceName.HN, cursor="  watermark  ").cursor == "watermark"
    assert SourceIngestionInput(source=SourceName.HN, cursor="   ").cursor is None


def test_input_round_trips_through_pydantic_data_converter_under_256kb() -> None:
    processed = tuple(f"{index:04d}" + "x" * (MAX_PROCESSED_ID_LENGTH - 4) for index in range(1000))
    data = SourceIngestionInput(
        source=SourceName.HN,
        poll_limit=100,
        poll_interval_seconds=3600,
        max_candidates=MAX_CANDIDATES,
        max_polls_per_run=500,
        review_timeout_seconds=604800,
        cursor="c" * MAX_CURSOR_LENGTH,
        processed_ids=processed,
        polls_in_run=400,
        total_polls=10_000_000,
    )
    converter = pydantic_data_converter.payload_converter
    payload = converter.to_payload(data)
    assert len(payload.data) < 256 * 1024
    assert converter.from_payload(payload, SourceIngestionInput) == data


def test_filter_new_events_skips_processed_and_preserves_order() -> None:
    first = event("a", "First")
    result = filter_new_events([first, event("b"), event("a", "Duplicate"), event("c")], ("b",))
    assert [item.external_id for item in result] == ["a", "c"]
    assert result[0] is first
    assert filter_new_events([], ()) == []


def test_filter_new_events_caps_the_batch() -> None:
    events = [event(str(index)) for index in range(MAX_BATCH_EVENTS + 5)]
    result = filter_new_events(events, ())
    assert len(result) == MAX_BATCH_EVENTS == 100
    assert result[0].external_id == "0"
    assert result[-1].external_id == str(MAX_BATCH_EVENTS - 1)


def test_advance_cursor_takes_max_numeric_id() -> None:
    assert advance_cursor(None, [event("3"), event("9"), event("5")]) == "9"
    assert advance_cursor("9", [event("3"), event("7")]) == "9"
    assert advance_cursor("4", [event("3"), event("7")]) == "7"
    assert advance_cursor("4", []) == "4"
    assert advance_cursor(None, [event("abc"), event("def")]) is None
    assert advance_cursor("abc", [event("3"), event("9")]) == "abc"
    assert advance_cursor("12", [event("abc"), event("def")]) == "12"


def test_append_processed_ids_keeps_fifo_bound() -> None:
    assert append_processed_ids(["a", "b"], ["b", "c", "d"], limit=3) == ("b", "c", "d")
    assert append_processed_ids(["a"], ["a"], limit=3) == ("a",)
    assert append_processed_ids([], ["a", "b"], limit=5) == ("a", "b")
    assert append_processed_ids(["a", "b", "c"], [], limit=2) == ("b", "c")
    with pytest.raises(ValueError):
        append_processed_ids(["a"], ["b"], limit=0)
    assert MAX_PROCESSED_IDS == 1000


def test_workflow_id_is_versioned_and_source_specific() -> None:
    hn = source_ingestion_workflow_id(SourceName.HN)
    fake = source_ingestion_workflow_id(SourceName.FAKE)
    assert hn == "source-ingestion-v1-hn"
    assert hn.startswith("source-ingestion-v1-")
    assert hn.endswith(SourceName.HN.value)
    assert fake == "source-ingestion-v1-fake"
    assert hn != fake
    assert SourceIngestionWorkflow.__name__ == "SourceIngestionWorkflow"


def test_state_and_result_models_are_frozen_bounded_and_forbid_extra_fields() -> None:
    state = SourceIngestionState()
    assert state.source is None
    assert state.cursor is None
    assert state.processed_ids == ()
    assert state.polls_in_run == 0
    assert state.total_polls == 0
    assert state.last_poll_at is None
    assert state.last_event_count == 0
    assert state.last_new_event_count == 0
    assert state.last_selected_count == 0

    populated = SourceIngestionState(
        source=SourceName.HN,
        cursor="2001",
        processed_ids=("2001",),
        polls_in_run=2,
        total_polls=12,
        last_poll_at=OBSERVED,
        last_event_count=5,
        last_new_event_count=2,
        last_selected_count=1,
    )
    assert populated.source is SourceName.HN
    with pytest.raises(ValidationError):
        populated.total_polls = 3

    with pytest.raises(ValidationError):
        SourceIngestionState.model_validate({"extra": "x"})
    with pytest.raises(ValidationError):
        SourceIngestionState(polls_in_run=-1)
    with pytest.raises(ValidationError):
        SourceIngestionState(total_polls=-1)
    with pytest.raises(ValidationError):
        SourceIngestionState(last_event_count=-1)
    with pytest.raises(ValidationError):
        SourceIngestionState(processed_ids=tuple(str(index) for index in range(1001)))
    with pytest.raises(ValidationError):
        SourceIngestionState(last_poll_at=datetime(2026, 10, 9, 12, 0))

    result = SourceIngestionResult(
        source=SourceName.HN, reason="stopped", total_polls=4, cursor="2001"
    )
    assert result.reason == "stopped"
    assert result.total_polls == 4
    assert result.cursor == "2001"
    with pytest.raises(ValidationError):
        result.total_polls = 5
    with pytest.raises(ValidationError):
        SourceIngestionResult.model_validate(
            {"source": "hn", "reason": "stopped", "total_polls": 0, "extra": "x"}
        )
    with pytest.raises(ValidationError):
        SourceIngestionResult.model_validate({"source": "hn", "reason": "done", "total_polls": 0})
    with pytest.raises(ValidationError):
        SourceIngestionResult(source=SourceName.HN, reason="stopped", total_polls=-1)
    with pytest.raises(ValidationError):
        SourceIngestionResult(
            source=SourceName.HN,
            reason="stopped",
            total_polls=0,
            cursor="c" * (MAX_CURSOR_LENGTH + 1),
        )
