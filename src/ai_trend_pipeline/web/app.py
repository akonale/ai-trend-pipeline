"""Application factory."""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.web.routes import health, home, runs
from ai_trend_pipeline.web.routes import settings as settings_routes

WEB_DIR = Path(__file__).parent


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Topic Studio")
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.state.style_store_lock = asyncio.Lock()
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(runs.router)
    app.include_router(settings_routes.router)
    return app
