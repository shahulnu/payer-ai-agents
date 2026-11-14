"""HTTP API. The portfolio site forwards requests here when FAX_INTAKE_ENGINE_URL is set.

Run locally: uv run uvicorn app.main:app --port 8091

Paused runs live in memory (LangGraph's InMemorySaver), so run one worker; a production deployment
would use a persistent checkpointer such as Postgres."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import live_limit, settings
from app.fax_intake.faxes import find_fax, load_faxes
from app.fax_intake.graph import resume, start
from app.fax_intake.llm import ModelDeclinedError, ReplayMissingError

app = FastAPI(title="Payer AI agents")


class ProcessRequest(BaseModel):
    faxId: str
    mode: str = "replay"


class ApproveRequest(BaseModel):
    threadId: str
    approved: bool
    reviewer: str = "intake reviewer"


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/health")
def health():
    return {"status": "ok", "model": settings.MODEL}


@app.get("/api/fax-intake/faxes")
def faxes():
    return [{k: f[k] for k in ("id", "title", "teaser", "receivedAt", "sender")} for f in load_faxes()]


@app.get("/api/fax-intake/process")
def process_info():
    return {"liveAvailable": settings.live_available(), "engine": "python"}


@app.post("/api/fax-intake/process")
async def process(body: ProcessRequest, request: Request):
    fax = find_fax(body.faxId)
    if fax is None:
        return JSONResponse({"error": f'Unknown fax "{body.faxId}".'}, status_code=404)
    mode, notice = "replay", None
    if body.mode == "live":
        if not settings.live_available():
            notice = "Live runs are off on this deployment, so this is a recorded live run."
        elif not live_limit.take(client_ip(request)):
            notice = f"You've reached today's limit of {settings.DAILY_LIMIT_PER_IP} live runs, so this is a recorded live run."
        else:
            mode = "live"
    try:
        result = await start(fax, mode)
    except ReplayMissingError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except ModelDeclinedError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {**result, "notice": notice}


@app.post("/api/fax-intake/approve")
async def approve(body: ApproveRequest):
    try:
        return await resume(body.threadId, body.approved, body.reviewer)
    except KeyError as e:
        return JSONResponse({"error": str(e.args[0]) + " It may have expired; run the fax again."}, status_code=404)
