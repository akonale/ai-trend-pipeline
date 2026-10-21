from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_workflow_triggers() -> None:
    text = _text()
    assert "pull_request:" in text
    assert "push:" in text
    assert "branches: [main]" in text or "- main" in text


def test_ci_workflow_runs_just_recipes() -> None:
    text = _text()
    assert "uv sync --locked" in text
    assert "just lint" in text
    assert "just test" in text
    assert text.index("just lint") < text.index("just test")


def test_ci_workflow_has_no_secrets() -> None:
    assert "secrets." not in _text()
