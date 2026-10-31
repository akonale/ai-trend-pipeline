"""Guard the committed publishing documentation."""

import re
from pathlib import Path

from ai_trend_pipeline.publishing.models import PublicationErrorCode
from ai_trend_pipeline.story_publishing import FAILURE_DETAILS

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "runbooks" / "publishing.md").read_text(encoding="utf-8")
STORIES = (ROOT / "docs" / "runbooks" / "stories.md").read_text(encoding="utf-8")
SEARCH = (ROOT / "docs" / "runbooks" / "search-attributes.md").read_text(encoding="utf-8")
DEPLOY = (ROOT / "docs" / "runbooks" / "deploy.md").read_text(encoding="utf-8")
TECH = (ROOT / "docs" / "architecture" / "tech.md").read_text(encoding="utf-8")
BUSINESS = (ROOT / "docs" / "architecture" / "business.md").read_text(encoding="utf-8")
ARCH = (ROOT / "docs" / "architecture" / "README.md").read_text(encoding="utf-8")


def test_readme_has_publishing_section_bluesky_and_media_keys_and_e2e_command() -> None:
    assert "## Publishing a story" in README
    section = README[README.index("## Publishing a story") :]
    section = section.split("\n## ", 2)[1] if section.count("\n## ") else section
    for needle in (
        "Published",
        "BLUESKY_HANDLE",
        "BLUESKY_APP_PASSWORD",
        "BLUESKY_SERVICE",
        "MEDIA_DIR",
        "MEDIA_PROVIDER",
        "story-card",
        "app password",
        "still card",
        "48-hour",
        "docs/runbooks/publishing.md",
        "uv run pytest tests/test_story_publishing_e2e.py -q",
    ):
        assert needle in README, needle
    for status in ("Pending", "Writing", "Review", "Ready", "Published", "Closed"):
        assert status in README, status
    assert "Candidate approval" in README and "never publishes" in README


def test_publishing_runbook_sections_commands_and_every_error_code() -> None:
    for heading in (
        "## Configure Bluesky",
        "## Media and providers",
        "## Publish a story",
        "## Recover a failed publication",
        "## Retention",
        "## Visual check (manual)",
    ):
        assert heading in RUNBOOK, heading
    for command in (
        "workflow describe --workflow-id publish-v1-",
        "workflow query --workflow-id publish-v1-",
        "--type publication_state",
        'WorkflowType = "StoryPublishingWorkflow" AND StoryStatus = "publish_failed"',
        "dev-temporal()",
        "prod-temporal()",
        "MEDIA_DIR",
        "MEDIA_PROVIDER",
        "<sha256>.<ext>",
        "workflow terminate",
    ):
        assert command in RUNBOOK, command
    for code in PublicationErrorCode:
        assert f"`{code.value}`" in RUNBOOK, code
        assert FAILURE_DETAILS[code] in RUNBOOK, code
    assert "Never create the post by hand" in RUNBOOK
    assert "Try again" in RUNBOOK
    assert "app password" in RUNBOOK
    assert not re.search(r"[\w.-]+@[\w-]+\.", RUNBOOK)  # no real handle or email in examples


def test_publishing_runbook_has_manual_visual_checklist() -> None:
    checklist = RUNBOOK[RUNBOOK.index("## Visual check (manual)") :]
    for item in (
        "horizontal scroll at 360px",
        "below 48rem",
        "long URL host",
        "dashed",
        "focus ring",
        "ink-on-violet",
        "Reduced motion",
    ):
        assert item in checklist, item
    assert checklist.count("- [ ]") >= 7
    assert "screenshot" in checklist.lower()


def test_tech_lists_publish_route_media_provider_and_no_for_064_note() -> None:
    assert "/stories/{id}/publish" in TECH
    assert "?publish=" in TECH
    assert "MEDIA_PROVIDER" in TECH and "`media_provider`" in TECH
    assert "For 064" not in TECH
    assert "PublicationLookup" in TECH
    assert "preview_post" in TECH
    assert "--color-published" in TECH
    assert "test_story_publishing_e2e.py" in TECH
    assert "post proof" in TECH


def test_other_docs_link_publishing_and_describe_the_explicit_publish_rule() -> None:
    assert "publishing.md" in STORIES and "never publishes" in STORIES
    assert "publishing.md" in DEPLOY
    for status in ("publishing", "published", "publish_failed"):
        assert f"`{status}`" in SEARCH, status
    assert "no new" in SEARCH.lower()
    assert "Published" in BUSINESS and "Publish" in BUSINESS
    assert "Published" in ARCH and "ready" in ARCH.lower()
    assert "never the six UI badges" in SEARCH
