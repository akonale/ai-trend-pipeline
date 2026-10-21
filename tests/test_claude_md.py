"""Guard tests: CLAUDE.md keeps its required structure."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
REQUIRED_HEADINGS = [
    "Stack",
    "Commands",
    "Code rules",
    "Temporal rules",
    "Testing rules",
    "Task workflow",
    "Review checklist",
]


def test_claude_md_has_required_sections() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+?)\s*$", text, flags=re.MULTILINE)
    positions: list[int] = []
    for name in REQUIRED_HEADINGS:
        assert name in headings, f"missing heading: {name}"
        positions.append(headings.index(name))
    assert positions == sorted(positions), "required headings out of order"


def test_claude_md_is_short() -> None:
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 150


def test_claude_md_commands_match_justfile() -> None:
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    recipes = re.findall(r"^([A-Za-z_][\w-]*)\s*:", justfile, flags=re.MULTILINE)
    recipes = [r for r in recipes if r != "default"]
    assert recipes, "no recipes found in justfile"
    text = CLAUDE_MD.read_text(encoding="utf-8")
    for name in recipes:
        assert f"just {name}" in text, f"CLAUDE.md missing `just {name}`"
