"""Understanding the message before retrieval: rewrite it to stand alone (resolve references only, never add),
split multi-part messages, translate the search text into the knowledge base's language, and classify short
messages (request / chitchat / about the assistant / about the conversation)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from steward.engine.common import Run, extract_numbers, looks_multi
from steward.engine.route import KIND_Q
from steward.knowledge.index import KnowledgeIndex, script_ratio

SYS_CONDENSE = """Rewrite the customer's LATEST MESSAGE as one standalone question that can be understood without the conversation.
Carry over what it refers to (products, plans, quantities the customer gave earlier). Keep every number exactly.
If the latest message answers a question the assistant just asked, restate the customer's original request together with that answer,
using the assistant's own terms for it (e.g. assistant asked "how many people do you chat with per month?", customer says "about 5000"
-> "Which plan suits me if I chat with about 5000 people (active contacts) per month, and what would it cost?").
If the latest message starts a NEW topic, do not attach details from earlier topics (e.g. "give contact details" after a pricing chat
-> "What are your contact details?").
If the latest message is already standalone, or is just a greeting/thanks, return it unchanged.
Only RESOLVE what the latest message refers to. Never ADD what the customer did not say in it: no new requests (e.g. "please
contact me", "connect me to an agent"), no contact details, names or phone numbers, and no quantities or products from earlier
turns unless the latest message clearly refers back to them (e.g. "do you have helmets?" stays a question about helmets,
without an earlier quantity; "I want to buy 14 helmets" does not become "...please contact me at <number>").
Return JSON: "standalone" = the whole rewritten message covering ALL of its parts; "parts" = if the message asks about two or
more DISTINCT things (e.g. who you are, a price, and a policy), one standalone question per thing (every part listed); otherwise []."""

CONDENSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["standalone", "search", "parts"],
                   "properties": {"standalone": {"type": "string"}, "search": {"type": "string"},
                                  "parts": {"type": "array", "items": {"type": "string"}}}}

LANG_NOTE = """
"search" = TRANSLATE the standalone message into the language of the knowledge base, whose text looks like this: "{sample}".
Translate even when the customer wrote in another language (this version is only used for searching); keep names, model
names and numbers exactly. Write every item of "parts" in that knowledge-base language too."""

SYS_META = """You are answering a message about YOURSELF or about THIS CONVERSATION, not about the business's knowledge content.
Use only the ASSISTANT PROFILE and the CONVERSATION. Do not state prices, limits or other business facts unless they appear in the CONVERSATION.
If asked to summarise the conversation, briefly list what the customer asked and what you answered. Be concise and friendly."""


@dataclass
class Understood:
    standalone: str
    search: str  # in the knowledge base's language
    kind: str | None  # decided for short messages; None = ask in the route call
    parts: list[str] = field(default_factory=list)


def history_text(history: list[dict[str, str]] | None, n: int = 3, chars: int = 700) -> str:
    return "\n".join(f"Customer: {h['q']}\nAssistant: {h['a'][:chars]}" for h in (history or [])[-n:]) or "(no earlier messages)"


def parse_condense(out: Any, fallback: str) -> tuple[str, list[str]]:
    if isinstance(out, dict):
        q = (out.get("standalone") or fallback).strip().strip('"')
        parts = [p.strip().strip('"') for p in out.get("parts") or [] if p and p.strip()]
    else:
        q, parts = ((str(out).strip().splitlines() or [fallback])[0].strip().strip('"'), [])
    if parts and abs(script_ratio(q) - script_ratio(" ".join(parts))) > 0.5:
        return q, (parts if len(parts) >= 2 else [])
    if len(parts) == 1 and parts[0].lower() not in q.lower():
        parts = [q, parts[0]]
    if parts and q.lower().strip(" ?.!") == fallback.lower().strip(" ?.!"):
        q = " ".join(parts)
    elif parts and len(q) < 0.8 * sum(len(p) for p in parts):
        parts = list(dict.fromkeys([q, *parts]))
        q = " ".join(parts)
    return q, (parts if len(parts) >= 2 else [])


def needs_translation(index: KnowledgeIndex, text: str) -> bool:
    return abs(script_ratio(text) - index.latin) > 0.5


async def understand(run: Run, index: KnowledgeIndex, query: str, history: list[dict[str, str]] | None) -> Understood:
    q, parts, search = query, [], query
    short = len(re.findall(r"\w+", query)) <= 6 and not extract_numbers(query)
    translate = needs_translation(index, query)
    if history or translate or (not short and looks_multi(query)):
        r = await run.chat("condense", [
            {"role": "system", "content": SYS_CONDENSE + LANG_NOTE.format(sample=index.sample_text)},
            {"role": "user", "content": f"CONVERSATION:\n{history_text(history)}\n\nLATEST MESSAGE: {query}"}],
            purpose="condense", max_tokens=500, schema=CONDENSE_SCHEMA, schema_name="condense")
        out = r.json if r.json is not None else r.text
        q, parts = parse_condense(out, query)
        if isinstance(out, dict) and str(out.get("search", "")).strip():
            search = str(out["search"]).strip()
        elif not translate:
            search = q
        if translate and needs_translation(index, search):  # the model skipped the translation: one dedicated call
            t = await run.chat("translate", [
                {"role": "system", "content": f'Translate the customer\'s message into the language of this text: "{index.sample_text}". '
                                              "Output only the translation. Keep names, model names and numbers exactly."},
                {"role": "user", "content": q}], purpose="condense", max_tokens=200)
            search = t.text.strip().strip('"') or search
    kind = None
    if short:
        a = await run.decide("kind", {"latest_message": query, "standalone_version": search}, KIND_Q, purpose="route")
        kind = a["kind"].get("choice") if float(a["kind"].get("confidence", 0) or 0) >= 0.6 else "request"
    return Understood(q, search, kind, parts)


async def meta_answer(run: Run, profile_block: str, original: str, history: list[dict[str, str]] | None) -> str:
    r = await run.chat("meta_answer", [
        {"role": "system", "content": profile_block + SYS_META},
        {"role": "user", "content": f"CONVERSATION:\n{history_text(history, 6, 900)}\n\nLATEST MESSAGE: {original}"}],
        purpose="meta", max_tokens=500)
    return r.text
