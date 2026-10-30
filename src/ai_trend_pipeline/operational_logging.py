"""Shared primitives for fixed, replay-safe operational log events.

Pure stdlib module: workflow code may import it inside `workflow.unsafe.imports_passed_through()`.
The guard, not reviewer discipline, keeps source text and secrets out of the operational log.
"""

from collections.abc import Mapping
from typing import Protocol

MAX_LOG_VALUE_LENGTH = 256

FORBIDDEN_LOG_FIELDS = frozenset(
    {
        "title",
        "url",
        "summary",
        "proposed_angle",
        "selection_reason",
        "instructions",
        "prompt",
        "response",
        "payload",
        "body",
        "text",
        "api_key",
        "password",
        "secret",
    }
)

LogValue = str | int | float | bool | None


class StructuredLogger(Protocol):
    """The subset of the SDK workflow/activity logger that operational events need."""

    def info(self, msg: str, *, extra: Mapping[str, object]) -> None: ...

    def warning(self, msg: str, *, extra: Mapping[str, object]) -> None: ...

    def error(self, msg: str, *, extra: Mapping[str, object]) -> None: ...


def check_log_field(name: str, value: object, *, forbidden: frozenset[str]) -> object:
    """Reject forbidden names case-insensitively and non-primitives; truncate strings."""
    if name.casefold() in forbidden:
        raise ValueError(f"forbidden log field: {name}")
    if isinstance(value, str):
        return value[:MAX_LOG_VALUE_LENGTH]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError(f"log field {name!r} must be a primitive, got {type(value).__name__}")
