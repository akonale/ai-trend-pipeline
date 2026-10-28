"""Pure style-profile model and helpers. No I/O, so workflows and activities may import it."""

import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

MAX_HASHTAGS = 3
MAX_SIGNATURE_CHARS = 40
HASHTAG_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,24}$")

PALETTES: dict[str, tuple[str, ...]] = {
    "classic": (
        "#4c78a8",
        "#f58518",
        "#e45756",
        "#72b7b2",
        "#54a24b",
        "#eeca3b",
        "#b279a2",
        "#ff9da6",
        "#9d755d",
        "#bab0ac",
    ),
    "ocean": (
        "#03396c",
        "#0077b6",
        "#00a8cc",
        "#48cae4",
        "#2a9d8f",
        "#52b788",
        "#90e0ef",
        "#6c8ead",
    ),
    "sunset": (
        "#7f1d1d",
        "#c2410c",
        "#ea580c",
        "#f59e0b",
        "#fbbf24",
        "#be185d",
        "#9d174d",
        "#6b21a8",
    ),
    "okabe-ito": (
        "#e69f00",
        "#56b4e9",
        "#009e73",
        "#f0e442",
        "#0072b2",
        "#d55e00",
        "#cc79a7",
        "#000000",
    ),
    "ink": (
        "#d9480f",
        "#212529",
        "#495057",
        "#6c757d",
        "#868e96",
        "#adb5bd",
        "#343a40",
        "#ced4da",
    ),
}

# vl-convert bundles only Liberation Sans and the slim image has no system fonts. A stack with no
# installed family renders no text at all, so serif and mono end with Liberation Sans.
FONTS: dict[str, str] = {
    "sans": "Liberation Sans, Arial, Helvetica, sans-serif",
    "serif": "Liberation Serif, Georgia, Times New Roman, serif, Liberation Sans",
    "mono": "Liberation Mono, Courier New, monospace, Liberation Sans",
}

TONES: dict[str, str] = {
    "neutral": "Write the caption in a neutral, plain tone. Keep it factual.",
    "friendly": "Write the caption in a warm, friendly tone. Keep it factual.",
    "punchy": "Write the caption in a short, punchy tone. Keep it factual.",
    "academic": "Write the caption in a precise, academic tone. Keep it factual.",
}


class ChartStyle(BaseModel):
    model_config = ConfigDict(frozen=True)

    palette: str = "classic"
    font: str = "sans"
    tone: str = "neutral"
    hashtags: list[str] = Field(default_factory=list)
    signature: str = ""

    @field_validator("palette")
    @classmethod
    def _palette(cls, value: str) -> str:
        if value not in PALETTES:
            raise ValueError(f"unknown palette {value!r}; choose one of {', '.join(PALETTES)}")
        return value

    @field_validator("font")
    @classmethod
    def _font(cls, value: str) -> str:
        if value not in FONTS:
            raise ValueError(f"unknown font {value!r}; choose one of {', '.join(FONTS)}")
        return value

    @field_validator("tone")
    @classmethod
    def _tone(cls, value: str) -> str:
        if value not in TONES:
            raise ValueError(f"unknown tone {value!r}; choose one of {', '.join(TONES)}")
        return value

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_HASHTAGS:
            raise ValueError(f"use at most {MAX_HASHTAGS} hashtags")
        for tag in value:
            if not HASHTAG_PATTERN.fullmatch(tag):
                raise ValueError(f"hashtag {tag!r} must be 1 to 24 letters, digits or underscores")
        return value

    @field_validator("signature")
    @classmethod
    def _signature(cls, value: str) -> str:
        value = value.strip()
        if "\n" in value or "\r" in value:
            raise ValueError("signature must be a single line")
        if len(value) > MAX_SIGNATURE_CHARS:
            raise ValueError(f"signature must be at most {MAX_SIGNATURE_CHARS} characters")
        return value


def parse_hashtags(text: str) -> list[str]:
    tags = [t.lstrip("#") for t in re.split(r"[,\s]+", text)]
    return list(dict.fromkeys(t for t in tags if t))


def caption_suffix(style: ChartStyle) -> str:
    parts = [" ".join(f"#{t}" for t in style.hashtags), style.signature]
    return "\n\n".join(p for p in parts if p)


def style_prompt(style: ChartStyle, caption_budget: int) -> str:
    return (
        "Style:\n"
        f"- {TONES[style.tone]}\n"
        f"- Keep the caption to at most {caption_budget} characters; "
        "hashtags and a signature are added after it.\n"
        "- Do not set colours, fonts or a config block; they are applied for you."
    )


def apply_style_to_spec(spec: dict[str, JsonValue], style: ChartStyle) -> dict[str, JsonValue]:
    palette = list(PALETTES[style.palette])
    existing = spec.get("config")
    config: dict[str, JsonValue] = dict(existing) if isinstance(existing, dict) else {}
    range_ = config.get("range")
    new_range: dict[str, JsonValue] = dict(range_) if isinstance(range_, dict) else {}
    new_range["category"] = list[JsonValue](palette)
    mark = config.get("mark")
    new_mark: dict[str, JsonValue] = dict(mark) if isinstance(mark, dict) else {}
    new_mark["color"] = palette[0]
    config["range"] = new_range
    config["mark"] = new_mark
    config["font"] = FONTS[style.font]
    out = dict(spec)
    out["config"] = config
    return out
