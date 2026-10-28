"""Run the web app on localhost."""

import uvicorn

from ai_trend_pipeline.config import load_settings
from ai_trend_pipeline.web.app import create_app

PORT = 8000


def main() -> None:
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.web_host, port=PORT)


if __name__ == "__main__":
    main()
