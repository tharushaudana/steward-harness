"""HTTP admin API and admin page (pip install "steward-harness[server]").

    from fastapi import FastAPI
    from steward.server import create_router, Principal

    async def auth(request) -> Principal | None:          # your login: return None to reject
        user = await my_sessions.user(request)
        return Principal(user.id, {"viewer", "editor", "publisher"}) if user and user.is_admin else None

    app = FastAPI()
    app.include_router(create_router(bot, auth=auth), prefix="/steward")   # admin page at /steward/admin

Roles: viewer (read config, records, sessions), editor (save drafts, validate, test chat, reset/release sessions,
agents switch), publisher (publish, discard, restore). There is no unauthenticated admin: pass insecure_local=True
only for local development.
"""

from steward.server.app import Principal, create_app, create_router

__all__ = ["Principal", "create_app", "create_router"]
