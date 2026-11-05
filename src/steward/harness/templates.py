"""Approved templates: language selection and placeholder rendering.

Placeholders: {{slots.name}}, {{contacts.sales.phone}}, {{vars.invalid.phone}}, {{turn.channel}} ...
Filters: {{slots.name|prefix:', '}} -> ", Ada" or "" when empty; also suffix:'x', default:'x', upper, lower.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from steward.config.models import Config

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*((?:\|\s*\w+(?::\s*'[^']*')?\s*)*)\}\}")
_FILTER = re.compile(r"\|\s*(\w+)(?::\s*'([^']*)')?")


def lookup(env: Mapping[str, Any], path: str) -> Any:
    cur: Any = env
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, Mapping) else None
    return cur


def _apply(value: Any, filters: str) -> str:
    text = "" if value is None else str(value)
    for name, arg in _FILTER.findall(filters or ""):
        if name == "prefix":
            text = f"{arg}{text}" if text else ""
        elif name == "suffix":
            text = f"{text}{arg}" if text else ""
        elif name == "default":
            text = text or arg
        elif name == "upper":
            text = text.upper()
        elif name == "lower":
            text = text.lower()
    return text


def render(text: str, env: Mapping[str, Any]) -> str:
    return _PLACEHOLDER.sub(lambda m: _apply(lookup(env, m.group(1)), m.group(2)), text or "")


def render_obj(obj: Any, env: Mapping[str, Any]) -> Any:
    """Render placeholders in every string of a nested structure (tool args)."""
    if isinstance(obj, str):
        return render(obj, env)
    if isinstance(obj, Mapping):
        return {k: render_obj(v, env) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [render_obj(v, env) for v in obj]
    return obj


def template_text(cfg: Config, tid: str | None, lang: str, env: Mapping[str, Any] | None = None) -> str:
    t = cfg.templates.get(tid or "")
    if t is None:
        return ""
    default = (cfg.settings.languages or ["en"])[0]
    txt = t.text.get(lang) or t.text.get(default) or next(iter(t.text.values()), "")
    return render(txt, env) if env is not None else txt


def all_template_text(cfg: Config) -> str:
    """Every template text and the contacts: numbers in them are approved, so the answer check allows them."""
    return " ".join(v for t in cfg.templates.values() for v in t.text.values()) + " " + json.dumps(cfg.contacts, ensure_ascii=False)
