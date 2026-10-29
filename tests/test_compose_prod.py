"""Static checks on the production compose file, Dockerfile and .dockerignore."""

import re
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {"postgres", "temporal", "temporal-ui", "worker", "web", "cloudflared"}
SECRET_WORDS = ("PASSWORD", "KEY", "TOKEN", "SECRET", "HANDLE")

Service = dict[str, object]


def _doc(name: str = "compose.prod.yaml") -> dict[str, object]:
    # yaml.safe_load returns Any by design; the result is cast to the known compose shape below.
    raw = yaml.safe_load((ROOT / "infra" / name).read_text())  # pyright: ignore[reportAny]
    return cast(dict[str, object], raw)


def _load() -> dict[str, Service]:
    return cast(dict[str, Service], _doc()["services"])


def _host_ports(services: dict[str, Service]) -> set[str]:
    out: set[str] = set()
    for svc in services.values():
        for port in cast(list[str], svc.get("ports", [])):
            out.add(port.split(":")[-2])
    return out


def _env(service: Service) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, list):
        items = cast(list[str], raw)
        return dict(i.split("=", 1) for i in items)
    return {k: str(v) for k, v in cast(dict[str, object], raw).items()}


def test_has_exactly_six_services() -> None:
    assert set(_load()) == SERVICES


def test_every_service_restarts() -> None:
    for name, svc in _load().items():
        assert svc.get("restart") == "unless-stopped", name


def test_published_ports() -> None:
    published = {name: svc["ports"] for name, svc in _load().items() if "ports" in svc}
    assert published == {
        "web": ["127.0.0.1:18000:8000"],
        "temporal-ui": ["127.0.0.1:18233:8080"],
    }


def test_project_name() -> None:
    assert _doc()["name"] == "ai-trend-prod"


def test_prod_ports_differ_from_dev() -> None:
    dev = cast(dict[str, Service], _doc("compose.dev.yaml")["services"])
    prod_ports = _host_ports(_load())
    assert prod_ports
    assert not prod_ports & _host_ports(dev)
    assert "8000" not in prod_ports


def test_cloudflared_service() -> None:
    svc = _load()["cloudflared"]
    image = cast(str, svc["image"])
    assert re.fullmatch(r"cloudflare/cloudflared:[\w.\-]+", image)
    assert not image.endswith(":latest")
    assert svc["command"] == ["tunnel", "--no-autoupdate", "run"]
    deps = cast(list[str] | dict[str, object], svc["depends_on"])
    assert {"web", "temporal-ui"} <= set(deps)
    assert "ports" not in svc
    assert "network_mode" not in svc


def test_tunnel_token_required_without_default() -> None:
    svc = _load()["cloudflared"]
    token = _env(svc)["TUNNEL_TOKEN"]
    assert re.fullmatch(r"\$\{CLOUDFLARE_TUNNEL_TOKEN:\?[^}]+\}", token)
    assert ":-" not in token
    assert "TOKEN" not in " ".join(cast(list[str], svc["command"]))


def test_web_temporal_ui_url_from_env() -> None:
    web = _env(_load()["web"])
    assert web["TEMPORAL_UI_URL"] == "${TEMPORAL_UI_URL:-http://localhost:18233}"


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


def test_worker_source_settings_from_env() -> None:
    worker = _env(_load()["worker"])
    assert worker["SOURCE_ENABLED"] == "${SOURCE_ENABLED:-hn}"
    assert worker["SOURCE_POLL_LIMIT"] == "${SOURCE_POLL_LIMIT:-30}"
    assert worker["SOURCE_POLL_INTERVAL_S"] == "${SOURCE_POLL_INTERVAL_S:-300}"
    assert worker["SOURCE_POLL_TIMEOUT_S"] == "${SOURCE_POLL_TIMEOUT_S:-15.0}"
    assert worker["CANDIDATE_REVIEW_TIMEOUT_S"] == "${CANDIDATE_REVIEW_TIMEOUT_S:-172800}"
    assert worker["SOURCE_CANDIDATE_BUDGET"] == "${SOURCE_CANDIDATE_BUDGET:-20}"


def test_secrets_are_interpolations_without_defaults() -> None:
    checked = 0
    for name, svc in _load().items():
        for key, value in _env(svc).items():
            if any(w in key for w in SECRET_WORDS):
                checked += 1
                assert re.fullmatch(r"\$\{[A-Z_]+(:\?[^}]*)?\}", value), f"{name}.{key}"
    assert checked >= 4
    assert _env(_load()["postgres"])["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")


def test_dockerfile_and_dockerignore() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in dockerfile
    assert re.search(r"^USER (?!root)\S+", dockerfile, re.MULTILINE)
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignored
    assert ".env.prod" in ignored


def test_env_prod_ignored() -> None:
    for name in (".gitignore", ".dockerignore"):
        assert ".env.prod" in (ROOT / name).read_text().splitlines(), name


def _deploy_recipe() -> str:
    text = (ROOT / "justfile").read_text()
    match = re.search(r"^deploy:\n((?:[ \t]+.*\n?)+)", text, re.MULTILINE)
    assert match
    return match.group(1)


def test_prod_uses_env_prod() -> None:
    deploy = _deploy_recipe()
    assert "--env-file .env.prod" in deploy
    assert "env -i" in deploy
    texts = [
        deploy,
        (ROOT / "infra" / "compose.prod.yaml").read_text(),
        (ROOT / "docs" / "runbooks" / "deploy.md").read_text(),
        (ROOT / "docs" / "runbooks" / "cloudflare.md").read_text(),
        (ROOT / "README.md").read_text(),
    ]
    for text in texts:
        assert not re.search(r"--env-file \.env(?!\.prod)", text)


def test_no_real_domain() -> None:
    paths = [
        ROOT / "infra" / "compose.prod.yaml",
        ROOT / "docs" / "runbooks" / "cloudflare.md",
        ROOT / "docs" / "runbooks" / "deploy.md",
        ROOT / "README.md",
    ]
    for path in paths:
        assert "trendrunner" not in path.read_text(), path.name


def test_web_auth_mode_is_literal_cloudflare() -> None:
    assert _env(_load()["web"])["AUTH_MODE"] == "cloudflare"


def test_web_daily_chart_cap_from_env() -> None:
    assert _env(_load()["web"])["DAILY_CHART_CAP"] == "${DAILY_CHART_CAP:-10}"


def test_web_database_url_and_dependencies() -> None:
    services = _load()
    url = _env(services["web"])["DATABASE_URL"]
    assert re.fullmatch(
        r"host=postgres port=5432 dbname=postgres user=postgres "
        r"password=\$\{POSTGRES_PASSWORD:\?[^}]+\}",
        url,
    )
    deps = cast(dict[str, dict[str, str]], services["web"]["depends_on"])
    assert deps["postgres"]["condition"] == "service_healthy"
    assert "temporal" in deps
    assert "DATABASE_URL" not in _env(services["worker"])
