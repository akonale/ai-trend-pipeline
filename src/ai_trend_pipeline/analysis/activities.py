"""Standalone, pure fact-calculation activity.

It is read-only and idempotent: no clients, no logging, no workflow start and no registration
(053 owns wiring). Expected data conditions are returned as `FactSet` values, never exceptions.
"""

from temporalio import activity

from ai_trend_pipeline.analysis.calculations import build_fact_set
from ai_trend_pipeline.analysis.models import FactInput, FactSet


class FactActivities:
    """Calculate bounded, deterministic facts from 050 dataset evidence."""

    @activity.defn
    def calculate_facts(self, data: FactInput) -> FactSet:
        return build_fact_set(data)
