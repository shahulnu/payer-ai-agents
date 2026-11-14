"""The synthetic faxes used by the demo and the eval."""

import json
from functools import lru_cache

from app import settings


@lru_cache(maxsize=1)
def load_faxes() -> list[dict]:
    return json.loads((settings.DATA_DIR / "fax-intake" / "faxes.json").read_text(encoding="utf-8"))


def find_fax(fax_id: str) -> dict | None:
    return next((f for f in load_faxes() if f["id"] == fax_id), None)
