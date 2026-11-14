"""Settings from environment variables, with a git-ignored .env file at the repo root for local runs."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = os.getenv("MODEL", "claude-opus-5-5")
# Claude Opus 5.5 list price per million tokens, used to report the cost of a live run.
PRICE_IN_PER_MTOK = 4.0
PRICE_OUT_PER_MTOK = 20.0
DAILY_LIMIT_PER_IP = int(os.getenv("DAILY_LIMIT_PER_IP", "10"))
DATA_DIR = ROOT / "data"


def live_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))
