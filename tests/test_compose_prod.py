"""Static checks on the production compose file, Dockerfile and .dockerignore."""

import re
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {"postgres", "temporal", "temporal-ui", "worker", "web"}
SECRET_WORDS = ("PASSWORD", "KEY", "TOKEN", "SECRET", "HANDLE")

Service = dict[str, object]


def _load() -> dict[str, Service]:
    raw = yaml.safe_load((ROOT / "infra" / "compose.prod.yaml").read_text())  # pyright: ignore[reportAny]
    doc = cast(dict[str, object], raw)
    return cast(dict[str, Service], doc["services"])


def _env(service: Service) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, list):
        items = cast(list[str], raw)
        return dict(i.split("=", 1) for i in items)
    return {k: str(v) for k, v in cast(dict[str, object], raw).items()}


def test_has_exactly_five_services() -> None:
    assert set(_load()) == SERVICES


def test_every_service_restarts() -> None:
    for name, svc in _load().items():
        assert svc.get("restart") == "unless-stopped", name


def test_published_ports() -> None:
    published = {name: svc["ports"] for name, svc in _load().items() if "ports" in svc}
    assert published == {
        "web": ["127.0.0.1:8000:8000"],
        "temporal-ui": ["127.0.0.1:8233:8080"],
    }


def test_worker_and_web_share_build_and_volume() -> None:
    services = _load()
    worker, web = services["worker"], services["web"]
    assert worker["build"] == web["build"]
    assert worker["command"] != web["command"]
    w_env, web_env = _env(worker), _env(web)
    for env in (w_env, web_env):
        assert env["TEMPORAL_ADDRESS"] == "temporal:7233"
    assert w_env["CHART_OUTPUT_DIR"] == web_env["CHART_OUTPUT_DIR"]
    chart_dir = w_env["CHART_OUTPUT_DIR"]
    for svc in (worker, web):
        assert f"charts:{chart_dir}" in cast(list[str], svc["volumes"])
    assert web_env["WEB_HOST"] == "0.0.0.0"


def test_secrets_are_interpolations_without_defaults() -> None:
    checked = 0
    for name, svc in _load().items():
        for key, value in _env(svc).items():
            if any(w in key for w in SECRET_WORDS):
                checked += 1
                assert re.fullmatch(r"\$\{[A-Z_]+(:\?[^}]*)?\}", value), f"{name}.{key}"
    assert checked >= 3
    assert _env(_load()["postgres"])["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")


def test_dockerfile_and_dockerignore() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in dockerfile
    assert re.search(r"^USER (?!root)\S+", dockerfile, re.MULTILINE)
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignored
