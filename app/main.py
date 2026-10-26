"""FastAPI app: serves the chat page and the report / chat / health API.

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import ROOT_DIR, ConfigError, settings
from app.core import chat, reports
from app.data import fantasy, nba
from app.data.nba import DataError
from app.llm.base import LLMError

app = FastAPI(title="NBA Fantasy Bot")
INDEX = ROOT_DIR / "static" / "index.html"


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


class PostgameIn(BaseModel):
    date: str | None = None


def _run(fn, *args):
    try:
        return fn(*args)
    except (ConfigError, LLMError, DataError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from None


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/api/chat")
def api_chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    return {"reply": _run(chat.chat, body.session_id, body.message)}


@app.post("/api/chat/reset")
def api_chat_reset(body: ResetIn):
    chat.reset(body.session_id)
    return {"ok": True}


@app.post("/api/report/pregame")
def api_pregame():
    return {"reply": _run(reports.build_pregame_report)}


@app.post("/api/report/postgame")
def api_postgame(body: PostgameIn | None = None):
    return {"reply": _run(reports.build_postgame_report, body.date if body else None)}


@app.get("/api/health")
def api_health():
    checks = []

    missing = settings.missing_for_llm()
    checks.append({
        "name": "LLM",
        "ok": not missing,
        "detail": f"{settings.llm_provider} / {settings.llm_model}" if not missing
        else "请在 .env 中填写: " + ", ".join(missing),
    })

    try:
        info = fantasy.get_league_info()
        checks.append({"name": "ESPN Fantasy", "ok": True,
                       "detail": f"{info['name']}（{info['team_count']} 队，{info['scoring_type']}）"})
    except DataError as e:
        checks.append({"name": "ESPN Fantasy", "ok": False, "detail": str(e)})

    try:
        games = nba.get_scoreboard()
        checks.append({"name": "NBA 数据", "ok": True, "detail": f"今天 {len(games)} 场比赛"})
    except DataError as e:
        checks.append({"name": "NBA 数据", "ok": False, "detail": str(e)})

    return {"ok": all(c["ok"] for c in checks), "checks": checks}
