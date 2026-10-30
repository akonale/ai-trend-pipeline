"""Guard the committed story-generation documentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCH = (ROOT / "docs" / "architecture" / "README.md").read_text(encoding="utf-8")
TECH = (ROOT / "docs" / "architecture" / "tech.md").read_text(encoding="utf-8")
BUSINESS = (ROOT / "docs" / "architecture" / "business.md").read_text(encoding="utf-8")
STORIES = (ROOT / "docs" / "runbooks" / "stories.md").read_text(encoding="utf-8")
SEARCH = (ROOT / "docs" / "runbooks" / "search-attributes.md").read_text(encoding="utf-8")
INGESTION = (ROOT / "docs" / "runbooks" / "ingestion.md").read_text(encoding="utf-8")
DEPLOY = (ROOT / "docs" / "runbooks" / "deploy.md").read_text(encoding="utf-8")

FIVE_STATUSES = ("Pending", "Writing", "Review", "Ready", "Closed")
RESEARCH_KEYS = (
    "RESEARCH_FETCH_TIMEOUT_S",
    "RESEARCH_MAX_RESPONSE_BYTES",
    "RESEARCH_MAX_REDIRECTS",
    "RESEARCH_USER_AGENT",
    "RESEARCH_EXTRA_CONTEXT_HOSTS",
)


def test_readme_documents_the_two_decisions_statuses_retention_and_proof() -> None:
    assert "Story generation after approval" in README
    assert "Candidate approval starts generation" in README
    assert "Final story approval marks the package `ready` and never publishes" in README
    for status in FIVE_STATUSES:
        assert status in README, status
    assert "48-hour" in README
    assert "presentation only" in README
    assert "SOURCE_CANDIDATE_BUDGET" in README
    assert "RESEARCH_FETCH_TIMEOUT_S" in README
    assert "story_generation.*" in README
    assert "grep story_generation" in README
    assert "docs/runbooks/stories.md" in README
    assert "docs/runbooks/search-attributes.md" in README
    assert "uv run pytest tests/test_story_generation_e2e.py -q" in README


def test_architecture_readme_has_budgets_logs_runbooks_and_the_e2e_proof() -> None:
    assert "Story generation after candidate approval" in ARCH
    assert "SOURCE_CANDIDATE_BUDGET" in ARCH
    assert "story_generation.*" in ARCH
    assert "story_logging.py" in ARCH
    assert "operational_logging.py" in ARCH
    assert "tests/test_story_generation_e2e.py" in ARCH
    assert "runbooks/stories.md" in ARCH
    assert "start_local" in ARCH
    for status in FIVE_STATUSES:
        assert status.lower() in ARCH.lower(), status


def test_tech_documents_the_budget_and_event_tables() -> None:
    assert "Story generation budgets and operational logs" in TECH
    for setting in (
        "SOURCE_CANDIDATE_BUDGET",
        "RESEARCH_FETCH_TIMEOUT_S",
        "RESEARCH_MAX_RESPONSE_BYTES",
        "RESEARCH_MAX_REDIRECTS",
        "ACTIVITY_MAX_ATTEMPTS",
        "STORY_DRAFT_MAX_FACTS",
        "STORY_UI_RETENTION",
    ):
        assert setting in TECH, setting
    for event in (
        "story_generation.started",
        "story_generation.status",
        "story_generation.research_completed",
        "story_generation.facts_completed",
        "story_generation.draft_completed",
        "story_generation.check_completed",
        "story_generation.revision",
        "story_generation.activity_failed",
        "story_generation.review_waiting",
        "story_generation.decision",
        "story_generation.ignored_signal",
        "story_generation.finished",
    ):
        assert event in TECH, event
    assert "FORBIDDEN_STORY_LOG_FIELDS" in TECH
    assert "runbooks/stories.md" in TECH
    assert "test_story_generation_e2e.py" in TECH


def test_business_documents_the_product_rules() -> None:
    assert "### Story generation" in BUSINESS
    assert "Candidate approval starts generation" in BUSINESS
    assert "never publishes" in BUSINESS
    for status in FIVE_STATUSES:
        assert status in BUSINESS, status
    assert "48 hours" in BUSINESS
    assert "presentation retention, not deletion" in BUSINESS
    assert "no global or per-day story quota" in BUSINESS


def test_stories_runbook_has_the_required_sections_and_commands() -> None:
    for heading in (
        "## Two human decisions",
        "## Local manual walkthrough",
        "## Search attributes",
        "## Bounded budgets",
        "## Failure recovery",
        "## Retention and replay",
        "## Structured logs",
    ):
        assert heading in STORIES, heading
    for command in (
        "just dev",
        "just ingest",
        "just web",
        "workflow query --workflow-id <story-id> --type story_state",
        'WorkflowType = "StoryGenerationWorkflow"',
        "StoryCandidateId",
        "operator search-attribute list",
        "grep story_generation",
        "SOURCE_ENABLED=hn,fake",
        "story-v2-",
        "uv run pytest tests/test_story_generation_e2e.py -q",
    ):
        assert command in STORIES, command
    assert "dev-temporal()" in STORIES
    assert "prod-temporal()" in STORIES
    assert "Candidate approval starts generation" in STORIES
    assert "never publishes" in STORIES
    assert "story_generation.activity_failed" in STORIES


def test_search_attributes_runbook_has_story_checks() -> None:
    assert "Story generation checks" in SEARCH
    assert 'WorkflowType = "StoryGenerationWorkflow" AND StoryStatus = "review"' in SEARCH
    assert 'StoryCandidateId = "candidate-v1-' in SEARCH
    assert "never the five UI badges" in SEARCH
    assert "StoryCreatedAt" in SEARCH
    assert "StoryUpdatedAt" in SEARCH
    assert "sorting only" in SEARCH


def test_ingestion_and_deploy_link_the_stories_runbook() -> None:
    assert "Approved candidates start exactly one story workflow" in INGESTION
    assert "stories.md" in INGESTION
    assert "stories.md" in DEPLOY
    for key in RESEARCH_KEYS:
        assert key in DEPLOY, key
