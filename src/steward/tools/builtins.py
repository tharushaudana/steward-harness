"""Built-in tool implementations, registered under these ids (a host may register its own under the same id to
replace one):

  records.append    args {"table": "lead", "record": {...}}: append to the RecordStore (idempotent by key; a
                    duplicate merges new non-empty fields into the existing record)
  agents.available  {"available": bool}: the runtime switch "agents_online" (admin toggle), else the config default
  http.request      args {"method", "url", "headers", "body"}: only hosts in settings.allow_hosts; ${env:NAME} in
                    headers is replaced from the environment (secrets never live in the config)
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from steward.stores.base import NewRecord, Stores
from steward.tools.registry import ToolRegistry
from steward.types import ToolCall, ToolResult

_ENV = re.compile(r"\$\{env:(\w+)\}")


def register_builtins(registry: ToolRegistry, stores: Stores) -> None:
    async def records_append(call: ToolCall) -> ToolResult:
        record = dict(call.args.get("record") or {})
        res = await stores.records.append(NewRecord(type=str(call.args.get("table") or "record"), data=record,
                                                    session=call.turn.session, key=call.idempotency_key,
                                                    is_test=call.turn.is_test))
        return ToolResult(ok=True, data={"id": res.id, "enriched": res.enriched}, duplicate=res.duplicate,
                          receipt={"record_id": res.id})

    async def agents_available(call: ToolCall) -> ToolResult:
        online = await stores.runtime.get("agents_online", bool(call.settings.get("agents_default_online", False)))
        return ToolResult(ok=True, data={"available": bool(online)})

    async def http_request(call: ToolCall) -> ToolResult:
        url = str(call.args.get("url") or "")
        host = urlparse(url).hostname or ""
        if host not in (call.settings.get("allow_hosts") or []):
            return ToolResult.failure(f"host '{host}' is not in settings.allow_hosts")
        headers = {k: _ENV.sub(lambda m: os.environ.get(m.group(1), ""), str(v)) for k, v in (call.args.get("headers") or {}).items()}
        async with httpx.AsyncClient(timeout=call.timeout_s) as client:
            r = await client.request(str(call.args.get("method") or "POST"), url, headers=headers, json=call.args.get("body"))
        data: Any = r.json() if "json" in r.headers.get("content-type", "") else {"text": r.text[:500]}
        if not isinstance(data, dict):
            data = {"value": data}
        return ToolResult(ok=r.status_code < 400, data=data, error=None if r.status_code < 400 else f"HTTP {r.status_code}",
                          receipt={"status": r.status_code} if r.status_code < 400 else None)

    registry.register("records.append", records_append, description="Append a record (idempotent by key)",
                      mock={"ok": True, "data": {"id": "mock", "enriched": []}, "receipt": {"record_id": "mock"}})
    registry.register("agents.available", agents_available, side_effect=False,
                      description="Whether human agents are online (admin switch)")
    registry.register("http.request", http_request, description="Call an allow-listed HTTP endpoint",
                      mock={"ok": True, "data": {"mock": True}, "receipt": {"status": 200}})
