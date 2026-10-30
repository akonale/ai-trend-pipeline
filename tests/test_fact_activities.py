"""Activity boundary tests for deterministic fact calculation."""

import ast
import inspect
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.analysis.activities import FactActivities
from ai_trend_pipeline.analysis.calculations import build_fact_set
from ai_trend_pipeline.analysis.models import FactInput, FactSetStatus
from ai_trend_pipeline.research import ResearchStatus
from tests.fact_fixtures import make_dataset_evidence, make_fact_input, make_rows

ANALYSIS_DIR = Path(__file__).parents[1] / "src" / "ai_trend_pipeline" / "analysis"
WORKER_FILE = Path(__file__).parents[1] / "src" / "ai_trend_pipeline" / "worker.py"


def _input() -> FactInput:
    evidence = make_dataset_evidence(
        make_rows(
            {
                "Alpha": {2000: 10.0, 2005: 30.0, 2010: 5.0, 2015: 20.0, 2020: 15.0},
                "Beta": {2000: 9.0, 2005: 8.0, 2010: 1.0},
                "Gamma": {2000: 9.0, 2005: 8.0, 2010: 2.0},
                "Delta": {2000: 9.0, 2005: 8.0, 2010: 3.0},
                "Epsilon": {2000: 9.0, 2005: 8.0, 2010: 4.0},
                "Zeta": {2000: 9.0, 2005: 8.0, 2010: 1000.0},
            }
        )
    )
    return make_fact_input([evidence])


def test_calculate_facts_activity_matches_pure_builder() -> None:
    data = _input()
    activity_result = ActivityEnvironment().run(FactActivities().calculate_facts, data)
    assert activity_result == build_fact_set(data)


def test_calculate_facts_is_idempotent_across_calls() -> None:
    data = _input()
    activities = FactActivities()
    first = ActivityEnvironment().run(activities.calculate_facts, data)
    second = ActivityEnvironment().run(activities.calculate_facts, data)
    assert first.model_dump_json() == second.model_dump_json()
    assert [fact.fact_id for fact in first.facts] == [fact.fact_id for fact in second.facts]


def test_unsupported_story_activity_returns_no_fallback_facts() -> None:
    evidence = make_dataset_evidence(make_rows({"World": {2000: 1.0, 2010: 2.0}}))
    data = make_fact_input([evidence], research_status=ResearchStatus.UNSUPPORTED)
    result = ActivityEnvironment().run(FactActivities().calculate_facts, data)
    assert result.status is FactSetStatus.UNSUPPORTED
    assert result.facts == []
    assert result.evidence_ids == []


def _forbidden_import(name: str) -> bool:
    forbidden = (
        "anthropic",
        "httpx",
        "temporalio.client",
        "temporalio.workflow",
        "ai_trend_pipeline.config",
        "ai_trend_pipeline.worker",
        "ai_trend_pipeline.daily_chart",
        "ai_trend_pipeline.chart_loop",
        "ai_trend_pipeline.story_candidate",
        "ai_trend_pipeline.source_ingestion",
        "ai_trend_pipeline.story_selection",
        "ai_trend_pipeline.services",
        "ai_trend_pipeline.web",
    )
    return any(name == item or name.startswith(f"{item}.") for item in forbidden)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_analysis_modules_are_llm_io_free_and_not_registered() -> None:
    bad = [
        (path.name, name)
        for path in sorted(ANALYSIS_DIR.glob("*.py"))
        for name in _imports(path)
        if _forbidden_import(name)
    ]
    assert bad == []

    package_imports = _imports(ANALYSIS_DIR / "__init__.py")
    assert not [
        name
        for name in package_imports
        if name.endswith(".calculations") or name.endswith(".activities")
    ]

    assert "FactActivities" not in WORKER_FILE.read_text()
    assert not inspect.iscoroutinefunction(FactActivities.calculate_facts)
