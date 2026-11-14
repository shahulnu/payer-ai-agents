"""Export recorded runs to the portfolio site, so its Replay mode works without this service running.

  uv run python scripts/export_site.py [--site ../ai-portfolio]

For each fax it writes the run paused for approval and the run after approval, plus the eval
summary, into the site's data/fax-intake folder. Fax page previews come from make_faxes.py --site."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.fax_intake.faxes import load_faxes  # noqa: E402
from app.fax_intake.graph import resume, start  # noqa: E402


def strip_timing(result: dict) -> dict:
    """Drop values that change on every run, so re-exports only change when results change."""
    for key in ("generatedAt", "threadId"):
        result.pop(key, None)
    for t in result["trace"]:
        t["ms"] = 0
    for item in result["lookups"] + result["results"]:
        item["ms"] = 0
    if result.get("decision"):
        result["decision"]["at"] = None
    return result


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=str(ROOT.parent / "ai-portfolio"))
    args = ap.parse_args()
    site = Path(args.site)
    if not (site / "package.json").exists():
        sys.exit(f"{site} doesn't look like the site project.")

    out = site / "data" / "fax-intake" / "results"
    out.mkdir(parents=True, exist_ok=True)
    for fax in load_faxes():
        paused = await start(fax, "replay")
        approved = await resume(paused["threadId"], approved=True, reviewer="demo reviewer")
        payload = {"paused": strip_timing(paused), "approved": strip_timing(approved)}
        (out / f"{fax['id']}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out / (fax['id'] + '.json')}")

    summary = ROOT / "evals" / "fax-intake-summary.json"
    if summary.exists():
        shutil.copy(summary, site / "data" / "fax-intake" / "eval-summary.json")
        print("Copied eval summary")
    else:
        print("No live eval summary yet; run the eval with --live first.")


if __name__ == "__main__":
    asyncio.run(main())
