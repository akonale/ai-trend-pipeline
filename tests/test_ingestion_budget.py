"""Pure budget helpers and the budget fields on ingestion input/state."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.source_ingestion import (
    DEFAULT_SOURCE_CANDIDATE_BUDGET,
    MAX_SOURCE_CANDIDATE_BUDGET,
    MIN_SOURCE_CANDIDATE_BUDGET,
    SourceIngestionInput,
    SourceIngestionState,
    budget_day_for,
    budget_remaining,
)
from ai_trend_pipeline.sources import SourceName


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_budget_day_for_uses_utc() -> None:
    assert budget_day_for(_utc(2026, 10, 9, 23, 59)) == date(2026, 10, 9)
    assert budget_day_for(_utc(2026, 10, 10, 0, 0)) == date(2026, 10, 10)
    plus_two = timezone(timedelta(hours=2))
    assert budget_day_for(datetime(2026, 10, 10, 1, 0, tzinfo=plus_two)) == date(2026, 10, 9)


def test_budget_remaining_defaults_resets_on_new_day_and_clamps_at_zero() -> None:
    day = date(2026, 10, 9)
    assert budget_remaining(20, 5, day, _utc(2026, 10, 9)) == 15
    assert budget_remaining(20, 5, None, _utc(2026, 10, 9)) == 20
    assert budget_remaining(20, 5, day, _utc(2026, 10, 10)) == 20
    assert budget_remaining(20, 20, day, _utc(2026, 10, 9)) == 0
    assert budget_remaining(20, 25, day, _utc(2026, 10, 9)) == 0


def test_budget_input_fields_defaults_and_bounds() -> None:
    data = SourceIngestionInput(source=SourceName.HN)
    assert data.candidate_budget == DEFAULT_SOURCE_CANDIDATE_BUDGET == 20
    assert data.budget_day is None
    assert data.candidates_started == 0

    assert MIN_SOURCE_CANDIDATE_BUDGET == 1
    assert MAX_SOURCE_CANDIDATE_BUDGET == 500
    assert SourceIngestionInput(source=SourceName.HN, candidate_budget=1).candidate_budget == 1
    assert SourceIngestionInput(source=SourceName.HN, candidate_budget=500).candidate_budget == 500
    parsed = SourceIngestionInput.model_validate(
        {
            "source": "hn",
            "candidate_budget": 7,
            "budget_day": "2026-10-09",
            "candidates_started": 3,
        }
    )
    assert parsed.budget_day == date(2026, 10, 9)
    assert parsed.candidates_started == 3

    for bad in (
        {"candidate_budget": 0},
        {"candidate_budget": 501},
        {"candidates_started": -1},
        {"budget_day": "not-a-date"},
        {"budget_day": 12},
        {"unknown_budget": 1},
    ):
        with pytest.raises(ValidationError):
            SourceIngestionInput.model_validate({"source": "hn", **bad})


def test_state_reports_budget_and_remaining() -> None:
    state = SourceIngestionState(
        candidate_budget=5,
        budget_day=date(2026, 10, 9),
        candidates_started=3,
    )
    assert state.candidate_budget == 5
    assert state.budget_day == date(2026, 10, 9)
    assert state.candidates_started == 3
    assert state.budget_remaining == 2

    assert SourceIngestionState(candidate_budget=5, candidates_started=9).budget_remaining == 0
    assert (
        SourceIngestionState(
            candidate_budget=5, candidates_started=2, budget_remaining=99
        ).budget_remaining
        == 3
    )
    fresh = SourceIngestionState()
    assert fresh.candidate_budget == 20
    assert fresh.budget_day is None
    assert fresh.candidates_started == 0
    assert fresh.budget_remaining == 20

    with pytest.raises(ValidationError):
        SourceIngestionState(candidate_budget=0)
    with pytest.raises(ValidationError):
        SourceIngestionState(candidate_budget=501)
    with pytest.raises(ValidationError):
        SourceIngestionState(candidates_started=-1)
    with pytest.raises(ValidationError):
        SourceIngestionState.model_validate({"budget_day": "not-a-date"})
    with pytest.raises(ValidationError):
        SourceIngestionState(budget_remaining=-1)
