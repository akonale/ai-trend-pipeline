"""Pure helper tests for the Temporal gateway."""

from datetime import UTC, datetime

from ai_trend_pipeline.services.temporal_gateway import started_since_query


def test_started_since_query() -> None:
    assert started_since_query(datetime(2026, 5, 1, tzinfo=UTC)) == (
        "WorkflowType = 'DailyChartWorkflow' AND StartTime >= '2026-05-01T00:00:00Z'"
    )
