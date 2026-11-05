"""Configuration migrations. `migrate(raw)` upgrades any older format to the current schema_version, returning the
new dict and human-readable notes. Version 1 is the prototype format this project grew out of."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from steward.config.models import SCHEMA_VERSION

_TEMPLATE_META = {"approved", "source", "requires_receipt", "covers", "counts_as_ask"}
_BUILTIN_IMPLS = {"builtin:store.append": "records.append", "builtin:agents.available": "agents.available",
                  "builtin:http": "http.request"}
_NORMALIZE_STEPS = {"strip_non_digits": "strip_non_digits"}


def _v1_to_v2(c: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    c = copy.deepcopy(c)
    c.pop("kb", None)
    c["changelog"] = [{"ts": str(e.get("ts") or e.get("at") or ""), "version": int(e.get("version") or e.get("v") or 0),
                       "note": " ".join(str(x) for x in (e.get("note"), f"({e['by']})" if e.get("by") else None) if x)}
                      for e in c.get("changelog") or [] if isinstance(e, dict)]
    # templates {lang: text, meta...} -> {text: {lang: text}, meta...}
    for tid, t in list((c.get("templates") or {}).items()):
        if isinstance(t, dict) and "text" not in t:
            text = {k: v for k, v in t.items() if k not in _TEMPLATE_META and isinstance(v, str)}
            c["templates"][tid] = {"text": text, **{k: v for k, v in t.items() if k in _TEMPLATE_META}}
    if c.get("templates"):
        notes.append("templates: per-language keys moved under 'text'")
    # tools: builtin:* impl ids -> registered implementation ids
    for spec in (c.get("tools") or {}).values():
        impl = spec.get("impl")
        if impl in _BUILTIN_IMPLS:
            spec["impl"] = _BUILTIN_IMPLS[impl]
    # validators: script-specific digit folding and country prefix -> generic steps
    for v in (c.get("validators") or {}).values():
        steps = []
        for s in v.get("normalize", []):
            if isinstance(s, str) and s.endswith("_digits") and s != "strip_non_digits":
                steps.append("unicode_digits")
            elif isinstance(s, str) and s.endswith("_country_code"):
                notes.append(f"validator step '{s}' replaced by a generic country_code step: set its 'code' and 'trunk'")
                steps.append({"country_code": {"code": "", "trunk": "0"}})
            else:
                steps.append(_NORMALIZE_STEPS.get(s, s) if isinstance(s, str) else s)
        v["normalize"] = steps
    # flow.ask_number -> flow.ask_contact
    flow = c.get("flow") or {}
    if "ask_number" in flow:
        a = flow.pop("ask_number")
        flow["ask_contact"] = {"template": a.get("template"),
                               **({"template_when_unanswered": a["template_name_phone"]} if a.get("template_name_phone") else {})}
    for step in ("welcome", "thank_you"):
        if isinstance(flow.get(step), dict):
            flow[step] = {k: v for k, v in flow[step].items() if k in ("template", "channel")}
    # channels: {name: {template}} -> template_only channels
    for ch in (c.get("channels") or {}).values():
        if "template" in ch and "mode" not in ch:
            ch["mode"] = "template_only"
    # journeys: jev -> decision, extract.from -> source
    for j in c.get("journeys") or []:
        for n in j.get("nodes", []):
            if "jev" in n:
                n["decision"] = n.pop("jev")
            if n.get("type") == "extract" and "from" in n:
                n["source"] = n.pop("from")
            if n.get("type") == "emit":
                n.pop("kind", None)
    for p in c.get("policies") or []:
        p.setdefault("trigger", {}).setdefault("type", "noul")
    # slots: unknown keys of older prototypes
    for sl in (c.get("slots") or {}).values():
        if sl.get("type") not in (None, "string", "phone", "email", "enum", "number"):
            sl["type"] = "string"
    c["schema_version"] = 2
    notes.append("migrated schema_version 1 -> 2")
    return c


_MIGRATIONS: dict[int, Callable[[dict[str, Any], list[str]], dict[str, Any]]] = {1: _v1_to_v2}


def detect_version(raw: dict[str, Any]) -> int:
    v = raw.get("schema_version", 1)
    return int(v) if isinstance(v, int | str) and str(v).isdigit() else 1


def migrate(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    v = detect_version(raw)
    if v > SCHEMA_VERSION:
        raise ValueError(f"this configuration has schema_version {v}; this steward supports up to {SCHEMA_VERSION}. "
                         f"Upgrade steward.")
    out = raw
    while v < SCHEMA_VERSION:
        out = _MIGRATIONS[v](out, notes)
        v = detect_version(out)
    return out, notes
