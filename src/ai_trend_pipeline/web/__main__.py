"""Run the web app on localhost."""

import uvicorn

from ai_trend_pipeline.config import load_settings
from ai_trend_pipeline.web.app import create_app

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    uvicorn.run(create_app(load_settings()), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
