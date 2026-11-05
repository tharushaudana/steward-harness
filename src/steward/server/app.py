"""FastAPI router for the admin page and API. See steward.server for usage."""

from __future__ import annotations

import csv
import inspect
import io
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

try:
    from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, PlainTextResponse, Response
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    raise ImportError('the admin server needs FastAPI: pip install "steward-harness[server]"') from None

from steward.app import Steward
from steward.config.catalog import catalog_json
from steward.config.models import Config
from steward.engine.core import PROFILE_LABELS
from steward.errors import ConfigError, Issue, RevisionConflict
from steward.types import Message


@dataclass(frozen=True)
class Principal:
    id: str
    roles: frozenset[str] | set[str] = field(default_factory=lambda: frozenset({"viewer"}))


AuthFn = Callable[[Request], "Principal | Awaitable[Principal | None] | None"]
ROLE_ORDER = ("viewer", "editor", "publisher")


def _issue_dict(i: Issue, cfg: dict[str, Any] | None) -> dict[str, Any]:
    d = {k: v for k, v in asdict(i).items() if v not in (None, "")}
    m = re.match(r"policies\[(\d+)\]", i.path)
    if m and cfg and int(m.group(1)) < len(cfg.get("policies", [])):
        d["policy"] = cfg["policies"][int(m.group(1))].get("id")
    m = re.match(r"templates\.([^.]+)", i.path)
    if m:
        d["template"] = m.group(1)
    return d


def _reply_json(r: Any) -> dict[str, Any]:
    t = r.trace.totals
    return {"answer": r.text, "status": r.status, "standalone": r.standalone, "state": r.state, "evidence": r.evidence,
            "harness": {"journeys": r.journeys, "policies": r.policies},
            "totals": {"cost_usd": t.cost_usd, "latency_ms": t.latency_ms, "tokens_in": t.tokens_in, "tokens_out": t.tokens_out,
                       "llm_calls": t.llm_calls, "decision_calls": t.decision_calls},
            "trace": r.trace.to_dicts()}


class ChatIn(BaseModel):
    text: str
    session: str = ""
    channel: str = "dm"
    config: str = "draft"
    is_test: bool = True


class ConfigIn(BaseModel):
    config: dict[str, Any]
    base_rev: str


class NoteIn(BaseModel):
    note: str = ""


class RestoreIn(BaseModel):
    id: str


class AgentsIn(BaseModel):
    online: bool


def create_router(bot: Steward, *, auth: AuthFn | None = None, insecure_local: bool = False,
                  knowledge_admin: Any = None) -> APIRouter:
    """The admin API + page. `knowledge_admin`: an optional steward.build.KnowledgeAdmin for the approved-knowledge
    tab (supersede review, approved documents)."""
    if auth is None and not insecure_local:
        raise ValueError("create_router needs auth=...: an admin API without authentication would let anyone change "
                         "the assistant (pass insecure_local=True only for local development)")

    async def principal(request: Request) -> Principal:
        if auth is None:
            return Principal("local", frozenset(ROLE_ORDER))
        p = auth(request)
        if inspect.isawaitable(p):
            p = await p
        if p is None:
            raise HTTPException(401, "not authenticated")
        return p

    def need(role: str) -> Callable[..., Awaitable[Principal]]:
        async def dep(p: Principal = Depends(principal)) -> Principal:
            if role not in p.roles:
                raise HTTPException(403, f"needs the '{role}' role")
            return p
        return dep

    viewer, editor, publisher = Depends(need("viewer")), Depends(need("editor")), Depends(need("publisher"))
    r = APIRouter()

    # ---------------------------------------------------------------------------------------- page + assets
    @r.get("/admin", response_class=HTMLResponse)
    async def admin_page() -> HTMLResponse:  # the page itself carries no data; every API call is authenticated
        return HTMLResponse(resources.files("steward.server").joinpath("static/admin.html").read_text(encoding="utf-8"))

    @r.get("/admin/vendor/{name}")
    async def vendor(name: str) -> Response:
        if name not in ("drawflow.min.js", "drawflow.min.css"):
            raise HTTPException(404)
        body = resources.files("steward.server").joinpath(f"static/vendor/{name}").read_bytes()
        return Response(body, media_type="text/javascript" if name.endswith(".js") else "text/css",
                        headers={"Cache-Control": "public, max-age=86400"})

    # ---------------------------------------------------------------------------------------- configuration
    @r.get("/config")
    async def get_config(draft: int = 0, _: Principal = viewer) -> dict[str, Any]:
        pub = await bot.ensure_config()
        dr = await bot.stores.config.draft()
        doc = (dr or pub) if draft else pub
        if doc is None:
            raise HTTPException(404, "no configuration")
        issues = await bot.admin.avalidate(doc.config)
        cfg: Config = await bot.config("draft" if draft else "published")
        return {"harness": doc.config, "is_draft": bool(draft and dr is not None), "rev": doc.rev,
                "published_rev": pub.rev if pub else None, "has_draft": dr is not None, "version": pub.version if pub else 0,
                "errors": [_issue_dict(i, doc.config) for i in issues],
                "agents_online": await bot.stores.runtime.get("agents_online", cfg.settings.agents_default_online)}

    @r.put("/config")
    async def put_config(body: ConfigIn, _: Principal = editor) -> dict[str, Any]:
        try:
            doc, issues = await bot.admin.asave_draft(body.config, body.base_rev)
        except RevisionConflict as e:
            raise HTTPException(409, str(e)) from None
        except ConfigError as e:
            raise HTTPException(422, {"errors": [_issue_dict(i, body.config) for i in e.issues]}) from None
        return {"rev": doc.rev, "errors": [_issue_dict(i, body.config) for i in issues]}

    @r.post("/config/validate")
    async def validate_config(body: dict[str, Any], _: Principal = editor) -> dict[str, Any]:
        return {"errors": [_issue_dict(i, body) for i in await bot.admin.avalidate(body)]}

    @r.post("/config/publish")
    async def publish(body: NoteIn, p: Principal = publisher) -> dict[str, Any]:
        try:
            doc = await bot.admin.apublish(body.note or f"published by {p.id}")
        except (ValueError, ConfigError) as e:
            raise HTTPException(400, str(e)) from None
        return {"version": doc.version, "rev": doc.rev}

    @r.post("/config/discard")
    async def discard(_: Principal = publisher) -> dict[str, Any]:
        await bot.admin.adiscard()
        return {"ok": True}

    @r.get("/config/history")
    async def history(_: Principal = viewer) -> dict[str, Any]:
        return {"history": [asdict(v) for v in await bot.admin.ahistory()]}

    @r.post("/config/restore")
    async def restore(body: RestoreIn, _: Principal = publisher) -> dict[str, Any]:
        try:
            doc = await bot.admin.arestore(body.id)
        except ValueError as e:
            raise HTTPException(404, str(e)) from None
        return {"rev": doc.rev}

    @r.get("/catalog")
    async def catalog(_: Principal = viewer) -> dict[str, Any]:
        index = await bot.engine.index()
        cfg = await bot.config("draft")
        return {"cards": catalog_json(), "tools": bot.tools.describe(), "languages": cfg.settings.languages,
                "profile_fields": [list(x) for x in PROFILE_LABELS] + [["greeting", "Greeting"]],
                "units": {s: {"title": x.title, "authority": x.authority} for s, x in index.sections.items()},
                "knowledge_admin": knowledge_admin is not None}

    @r.get("/prompt")
    async def prompt(draft: int = 0, _: Principal = viewer) -> dict[str, Any]:
        return await bot.prompt_preview("draft" if draft else "published")

    # ---------------------------------------------------------------------------------------- test chat, sessions
    @r.post("/chat")
    async def chat(body: ChatIn, _: Principal = editor) -> dict[str, Any]:
        msg = Message(session=body.session or f"test_{uuid.uuid4().hex[:8]}", text=body.text, channel=body.channel,
                      is_test=body.is_test)
        reply = await bot.ahandle(msg, config="draft" if body.config == "draft" else "published")
        return {**_reply_json(reply), "session": msg.session}

    @r.get("/sessions/{session}")
    async def session(session: str, _: Principal = viewer) -> dict[str, Any]:
        return await bot.sessions.aget(session)

    @r.post("/sessions/{session}/reset")
    async def reset(session: str, _: Principal = editor) -> dict[str, Any]:
        await bot.sessions.areset(session)
        return {"ok": True}

    @r.post("/sessions/{session}/release")
    async def release(session: str, p: Principal = editor) -> dict[str, Any]:
        return {"ok": True, "state": await bot.sessions.arelease(session, by=p.id)}

    @r.get("/runtime/agents")
    async def agents(_: Principal = viewer) -> dict[str, Any]:
        cfg = await bot.config()
        return {"online": bool(await bot.stores.runtime.get("agents_online", cfg.settings.agents_default_online))}

    @r.post("/runtime/agents")
    async def set_agents(body: AgentsIn, _: Principal = editor) -> dict[str, Any]:
        await bot.stores.runtime.set("agents_online", body.online)
        return {"online": body.online}

    # ---------------------------------------------------------------------------------------- records
    @r.get("/records")
    async def records(type: str = "", include_test: bool = False, _: Principal = viewer) -> dict[str, Any]:
        rows = await bot.stores.records.list(type or None, 500, include_test)
        return {"records": [asdict(x) for x in rows]}

    @r.get("/records.csv")
    async def records_csv(type: str = "lead", _: Principal = viewer) -> PlainTextResponse:
        rows = await bot.stores.records.list(type or None, 10000)
        keys = ["id", "ts", "type", "session", "status", *sorted({k for x in rows for k in x.data})]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=keys)
        w.writeheader()
        for x in rows:
            w.writerow({"id": x.id, "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(x.ts)), "type": x.type,
                        "session": x.session, "status": x.status, **x.data})
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={type or 'records'}.csv"})

    # ---------------------------------------------------------------------------------------- approved knowledge
    if knowledge_admin is not None:
        knowledge_admin.mount(r, viewer=viewer, editor=editor)
    return r


def create_app(bot: Steward, *, auth: AuthFn | None = None, insecure_local: bool = False, prefix: str = "",
               knowledge_admin: Any = None) -> FastAPI:
    """A ready FastAPI app (admin page at {prefix}/admin). For an existing app use create_router()."""
    app = FastAPI(title="Steward admin")
    app.include_router(create_router(bot, auth=auth, insecure_local=insecure_local, knowledge_admin=knowledge_admin),
                       prefix=prefix)
    return app
