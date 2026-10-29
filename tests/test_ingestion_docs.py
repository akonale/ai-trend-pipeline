"""Guard tests for the committed runbooks and the updated architecture docs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNBOOKS = ROOT / "docs" / "runbooks"


def test_ingestion_runbook_covers_the_five_procedures() -> None:
    text = (RUNBOOKS / "ingestion.md").read_text(encoding="utf-8")
    for heading in (
        "Ensure the workflows",
        "Enable or disable a source",
        "Recover a failed or silent source",
        "Replay and continue-as-new upgrades",
        "Two-source local demo",
    ):
        assert heading in text, heading
    for command in (
        "just ingest",
        "dev-temporal workflow signal --workflow-id source-ingestion-v1-fake --name stop",
        "dev-temporal workflow terminate --workflow-id source-ingestion-v1-fake "
        '--reason "source disabled"',
        "dev-temporal workflow describe --workflow-id source-ingestion-v1-hn",
        "dev-temporal workflow list --query 'WorkflowType = \"SourceIngestionWorkflow\"'",
        "dev-temporal workflow query --workflow-id source-ingestion-v1-hn --type ingestion_state",
        "dev-temporal workflow show --workflow-id source-ingestion-v1-hn",
        "source-ingestion-task-queue",
        "SOURCE_ENABLED=hn,fake",
        "SOURCE_CANDIDATE_BUDGET",
        "SOURCE_POLL_LIMIT",
        "SOURCE_POLL_INTERVAL_S",
        "CANDIDATE_REVIEW_TIMEOUT_S",
        "source-ingestion-v2-",
        "max_polls_per_run",
        "ParentClosePolicy.ABANDON",
        "ALLOW_DUPLICATE",
    ):
        assert command in text, command
    assert "dev-temporal()" in text
    assert "prod-temporal()" in text
    assert "--address" in text


def test_search_attribute_runbook_has_both_attributes_and_types() -> None:
    text = (RUNBOOKS / "search-attributes.md").read_text(encoding="utf-8")
    assert "operator search-attribute create --name CandidateStatus --type Keyword" in text
    assert "operator search-attribute create --name CandidateSource --type Keyword" in text
    assert "operator search-attribute list" in text
    assert "ALREADY_EXISTS" in text
    assert "ensure_candidate_search_attributes" in text
    assert "CandidateStatus" in text
    assert "CandidateSource" in text
    assert "Keyword" in text
    assert "start_local" in text
    assert "dev-temporal()" in text
    assert "prod-temporal()" in text


def test_architecture_docs_qualify_the_scheduler_claim_and_link_runbooks() -> None:
    architecture = (ROOT / "docs" / "architecture" / "README.md").read_text(encoding="utf-8")
    lowered = architecture.lower()
    assert "nothing starts a run on its own" not in lowered
    assert "../runbooks/ingestion.md" in architecture
    assert "../runbooks/search-attributes.md" in architecture
    assert "poll continuously" in lowered

    deploy = (RUNBOOKS / "deploy.md").read_text(encoding="utf-8")
    assert "ingestion.md" in deploy
    assert "search-attributes.md" in deploy
    assert "SOURCE_CANDIDATE_BUDGET" in deploy
    assert "ingestion_starter" in deploy

    history = (ROOT / "docs" / "history.md").read_text(encoding="utf-8")
    assert "Phase 9" in history
    assert "042" in history and "049" in history
    for task in ("042", "043", "044", "045", "046", "047", "048"):
        assert task in history

    tech = (ROOT / "docs" / "architecture" / "tech.md").read_text(encoding="utf-8")
    assert "SOURCE_CANDIDATE_BUDGET" in tech
    assert "poll_fake_source" in tech
    assert "candidate_ingestion." in tech
    assert "ingestion.md" in tech or "../runbooks/ingestion.md" in tech

    business = (ROOT / "docs" / "architecture" / "business.md").read_text(encoding="utf-8")
    assert "Story candidates" in business
    assert "budget" in business.lower()
    assert "Google Trends" in business
    assert "nothing starts a run on its own" not in business.lower()

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "SOURCE_CANDIDATE_BUDGET" in readme
    assert "Test source" in readme
    assert "candidate_ingestion" in readme
    assert "docs/runbooks/ingestion.md" in readme
    assert "docs/runbooks/search-attributes.md" in readme
    assert "Google Trends" in readme
