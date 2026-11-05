"""Steward inside an existing FastAPI app: your chat endpoint, plus the admin page and API under /steward.

    uv sync --extra server
    OPENROUTER_API_KEY=... uv run uvicorn examples.fastapi_app:app --port 8000
    open http://127.0.0.1:8000/steward/admin

Replace `auth` with your own login: it receives the request and returns a Principal (or raises 401/403)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from steward import Message, Steward
from steward.knowledge import MemoryKnowledgeStore
from steward.providers import OpenRouter
from steward.server import Principal, create_router
from steward.stores.sqlite import SqliteStores

HERE = Path(__file__).parent / "northwind"

bot = Steward.from_file(HERE / "northwind.steward.json", provider=OpenRouter.from_env(spend_cap_usd=1.0),
                        stores=SqliteStores("northwind.db"), knowledge=MemoryKnowledgeStore.from_file(HERE / "kb.json"))


def auth(request: Request) -> Principal:
    token = request.headers.get("authorization", "")
    if token != "Bearer change-me":  # your real auth here (session cookie, JWT, ...)
        raise HTTPException(401, "login required")
    return Principal(id="admin", roles={"viewer", "editor", "publisher"})


app = FastAPI()
app.include_router(create_router(bot, auth=auth), prefix="/steward")


class ChatIn(BaseModel):
    session: str
    text: str


@app.post("/chat")
async def chat(body: ChatIn) -> dict:
    reply = await bot.ahandle(Message(session=body.session, text=body.text, channel="web"))
    return {"text": reply.text, "status": reply.status}
