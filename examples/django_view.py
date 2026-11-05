"""Steward in a synchronous host (Django shown; Flask is the same): use bot.handle(), the sync facade.

Create the bot once per process (module level), not per request: it keeps a private event loop for the async core.
Stores must be shared between workers, so use MongoDB or Firestore (or SQLite for a single process)."""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse  # type: ignore[import-not-found]
from django.views.decorators.http import require_POST  # type: ignore[import-not-found]

from steward import Message, Steward
from steward.providers import OpenRouter
from steward.stores.sqlite import SqliteStores

bot = Steward.from_file("assistant.steward.json", provider=OpenRouter.from_env(spend_cap_usd=5.0),
                        stores=SqliteStores("assistant.db"))
bot.check()


@require_POST
def chat(request: HttpRequest) -> JsonResponse:
    body = json.loads(request.body)
    reply = bot.handle(Message(session=str(body["session"]), text=str(body["text"]), channel="web"))
    return JsonResponse({"text": reply.text, "status": reply.status})
