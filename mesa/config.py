from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSITIONS_FILE = PROJECT_ROOT / "positions.json"
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Absolute path so cron/systemd work from any cwd; "ignore" so an unrelated key in .env
    # (pydantic-settings forbids extras by default) can't take the whole app down.
    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


def get_settings() -> Settings:
    return Settings()
