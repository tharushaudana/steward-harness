"""Client rules: the editable rules block of the answer prompt, code checks on the written answer, and
deterministic fixes when one rewrite did not satisfy them."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from steward.config.models import Config
from steward.engine.generate import Checker, Fixer
from steward.knowledge.index import KnowledgeIndex
from steward.types import Receipt

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF️‍]")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？।۔])\s+")

# Locked lines: the system, not the model, greets, asks for contact details, thanks, and reports actions.
CORE_RULES = (
    "Do NOT greet the customer, do NOT ask for their name or contact details and do NOT thank them for contact details: "
    "the system adds greetings, contact requests and thank-you messages itself.",
    "Never say that a message was forwarded, a person was notified or someone will call, unless RECEIPTS lists it.",
    "Never state whether team members are available, that the customer's details were saved or noted, or that someone "
    "will contact them: the system reports these itself, from the tools it actually ran this turn. What earlier "
    "assistant messages said about this is not evidence and must not be repeated.",
)


def rules_block(cfg: Config) -> str:
    r = cfg.rules
    lines = list(r.voice) + [w.instruction for w in r.required_wording if w.instruction]
    if r.forbidden_phrases:
        lines.append("Never use these words or phrases: " + ", ".join(f"'{f.phrase}'" for f in r.forbidden_phrases) + ".")
    if r.plain_text:
        lines.append("Plain text only: no markdown (no bold, italics, headings or tables) and no bullet symbols; "
                     "use short lines and line breaks instead.")
    if cfg.prompts.answer_extra:
        lines.append(cfg.prompts.answer_extra)
    lines += CORE_RULES
    return "CLIENT RULES:\n- " + "\n- ".join(lines) + "\n\n"


def _mentions(text: str, names: Sequence[str]) -> re.Match[str] | None:
    for n in names:
        m = re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I)
        if m:
            return m
    return None


def make_checker(cfg: Config, index: KnowledgeIndex | None, lang: str, receipts: Sequence[Receipt]) -> Checker:
    r = cfg.rules
    ok_tools = {x.tool for x in receipts if x.ok}

    def check(text: str) -> tuple[list[str], dict[str, Any]]:
        low = text.lower()
        problems: list[str] = []
        details: dict[str, Any] = {}
        bad = [f.phrase for f in r.forbidden_phrases if f.phrase.lower() in low]
        if bad:
            problems.append(f"Do not use these phrases: {bad}.")
            details["forbidden"] = bad
        for w in r.required_wording:
            if w.when_mentions_entity_types:
                cond = bool(index and _mentions(text, index.entity_names(w.when_mentions_entity_types)))
            elif w.when_regex:
                cond = bool(re.search(w.when_regex, text, re.I))
            else:
                cond = False
            chk = w.check.get(lang) or next(iter(w.check.values()), None)
            if cond and chk and not re.search(chk, text, re.I):
                problems.append(w.instruction or f"Use the required wording '{w.id}'.")
                details.setdefault("required_missing", []).append(w.id)
        cl = r.claims_without_receipt
        if cl.phrases and not (ok_tools & set(cl.receipt_tools)):
            claims = [p for p in cl.phrases if p.lower() in low]
            if claims:
                problems.append(f"Do not claim these actions, they did not happen: {claims}.")
                details["claims"] = claims
        return problems, details

    return check


def make_fixer(cfg: Config, index: KnowledgeIndex | None, lang: str) -> Fixer:
    r = cfg.rules

    def fix(text: str, details: dict[str, Any]) -> str:
        for f in r.forbidden_phrases:
            text = re.sub(re.escape(f.phrase), f.replace, text, flags=re.I)
        for w in r.required_wording:
            if w.id not in details.get("required_missing", []):
                continue
            ins = w.insert.get(lang) or next(iter(w.insert.values()), None)
            if not ins:
                continue
            if w.when_mentions_entity_types and index:
                m = _mentions(text, index.entity_names(w.when_mentions_entity_types))
                if m:
                    text = text[:m.start()] + f"{ins} " + text[m.start():]
            else:
                text = f"{text.rstrip()} {ins}"
        for p in details.get("claims", []):  # drop sentences that claim an action without a receipt
            text = " ".join(s for s in _SENTENCE_END.split(text) if p.lower() not in s.lower())
        return text

    return fix


def strip_emoji(text: str) -> str:
    return EMOJI_RE.sub("", text)


_MD_EMPH = re.compile(r"(\*\*|__)(.+?)\1|(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_MD_LINE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*•][ \t]+|\d{1,2}[.)][ \t]+(?=\D))", re.M)


def strip_markdown(text: str) -> str:
    """rules.plain_text: remove emphasis markers, heading marks and list bullets; keep the words and line breaks.
    Numbered list markers like "1. " are removed only before text (not "2.5 Lakh")."""
    text = _MD_EMPH.sub(lambda m: m.group(2) or m.group(3) or "", text)
    text = _MD_LINE.sub("", text)
    text = re.sub(r"^[ \t]*\|?[ \t]*:?-{3,}.*$", "", text, flags=re.M)  # table separator rows
    return re.sub(r"\n{3,}", "\n\n", text).strip()
