"""Grounded answer generation and its code checks.

The answer model sees ONLY the selected sections (approved ones labelled APPROVED), computed CALC lines and ASK
lines. Code then checks the reply: every number must come from the evidence (or be a sum/difference/product of
supported numbers it states), the reply must be in the customer's script, and the harness's client rules (passed in
as a checker) must hold. One rewrite is attempted; unsupported numbers are never shown.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from steward.engine.calc import calc_lines
from steward.engine.common import Run
from steward.knowledge.index import KnowledgeIndex, script_ratio

SYS_ANSWER = """You are the customer assistant for the business described by the SECTIONS. Answer the customer's question using ONLY the SECTIONS and CALC lines.
Rules:
- Never state a number, price, limit or capability that is not in SECTIONS/CALC. If the sections don't contain the answer, say the available information doesn't specify it.
- CALC results are already computed exactly: use them as given and show the short working. Do not do other arithmetic beyond simple differences/sums of numbers shown.
- If the sections give several prices/values for the same thing (e.g. monthly vs annual billing), state both or say which one you used.
- If the sections don't state the answer directly but clearly imply it, give the implied answer, say it is implied rather than spelled out, and suggest confirming with the team.
- If ASK lines are present, end by asking those questions.
- Cite section ids in square brackets like [S13] after the facts you use.
- Sections marked APPROVED are the business's management-approved information: when they differ from other sections, follow APPROVED.
- Reply in the language of the CUSTOMER'S OWN WORDS (or the QUESTION if not given), whatever language the SECTIONS are in.
- Be concise and friendly: 2-6 sentences or a short list."""

Checker = Callable[[str], tuple[list[str], dict[str, Any]]]  # text -> (problems for the rewrite prompt, details)
Fixer = Callable[[str, dict[str, Any]], str]  # deterministic fixes when the rewrite still breaks a rule


@dataclass
class GenerateOptions:
    profile_block: str = ""
    rules_block: str = ""  # client rules (harness)
    instructions: list[str] = field(default_factory=list)  # policy lines
    reply_like: str | None = None  # the text whose language the reply must use
    extra_allowed_text: str = ""  # e.g. approved templates and contacts: numbers in them are fine
    checker: Checker | None = None
    fixer: Fixer | None = None
    history_turns: int = 2


def section_context(index: KnowledgeIndex, ids: list[str], limit: int = 3000) -> str:
    def head(sid: str) -> str:
        s = index.sections[sid]
        if s.authority == "approved":
            return f"[{sid}] APPROVED (management-approved information; overrides other sections) - {s.heading_path or s.title}"
        return f"[{sid}] {s.heading_path or s.title}" + (f" - {s.url}" if s.url else "")
    return "\n\n".join(f"{head(s)}\n{index.text(s)[:limit]}" for s in ids if s in index.sections)


def allowed_numbers(text: str) -> set[float]:
    nums: set[float] = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        with contextlib.suppress(ValueError):
            nums.add(float(m.replace(",", "")))
    return nums


def number_check(text: str, allowed: set[float]) -> list[str]:
    """Numbers in the answer that the evidence does not support (small counts <= 12 without a currency are exempt)."""
    found = [(m, float(m.lstrip("$").replace(",", ""))) for m in
             re.findall(r"(?<![\w\[.])\$?\d[\d,]*(?:\.\d+)?", re.sub(r"\[[^\]]*\]", "", text))]
    ok = [v for _, v in found if any(abs(v - a) < 1e-6 for a in allowed)]
    bad = []
    for m, v in found:
        if (v <= 12 and "$" not in m) or any(abs(v - a) < 1e-6 for a in allowed):
            continue
        if any(abs(v - x) < 1e-6 for a in ok for b in ok for x in (a + b, abs(a - b), a * b)):
            continue
        bad.append(m)
    return bad


async def generate(run: Run, index: KnowledgeIndex, q: str, section_ids: list[str], results: list[dict[str, Any]],
                   asks: list[str], *, original: str | None = None, history: list[dict[str, str]] | None = None,
                   opts: GenerateOptions | None = None) -> str | None:
    """The grounded answer, or None when unsupported numbers survive the rewrite (the caller falls back)."""
    opts = opts or GenerateOptions()
    lines = calc_lines(results)
    own = f"CUSTOMER'S OWN WORDS: {original}\n" if original and original.strip() != q.strip() else ""
    if history:
        own = ("EARLIER IN THIS CONVERSATION (context only, not a source of facts):\n" +
               "\n".join(f"Customer: {h['q']}\nAssistant: {h['a'][:400]}" for h in history[-opts.history_turns:]) + "\n\n" + own)
    cust = (opts.reply_like or original or q).strip()
    policy = ("\n\nPOLICY (follow strictly):\n- " + "\n- ".join(opts.instructions)) if opts.instructions else ""
    user = (f"{own}QUESTION: {q}\n\nSECTIONS:\n{section_context(index, section_ids, run.settings.section_chars) or '(none)'}"
            + ("\n\nCALC:\n" + "\n".join(lines) if lines else "") + ("\n\nASK: " + " ".join(asks) if asks else "") + policy
            # last line on purpose (recency): sections may be in another language than the customer's message
            + f"\n\nREPLY LANGUAGE: write the whole reply in the same language as this customer message: «{cust}»")
    msgs: list[Any] = [{"role": "system", "content": opts.profile_block + opts.rules_block + SYS_ANSWER},
                       {"role": "user", "content": user}]
    r = await run.chat("answer", msgs, purpose="answer", max_tokens=700)
    evidence = " ".join([q, own, opts.profile_block, opts.extra_allowed_text, json.dumps(results), *lines,
                         *(index.text(s) for s in section_ids if s in index.sections)])
    allowed = allowed_numbers(evidence)

    def check(text: str) -> tuple[list[str], bool, list[str], dict[str, Any]]:
        prose = re.sub(r"https?://\S+|\[[^\]]*\]|\S+@\S+", " ", text)
        probs, det = opts.checker(text) if opts.checker else ([], {})
        return number_check(text, allowed), abs(script_ratio(prose) - script_ratio(cust)) > 0.5, probs, det

    t0 = time.perf_counter()
    bad, wrong_lang, probs, det = check(r.text)
    run.step("answer_check", "code", f"{len(allowed)} allowed numbers; customer script={script_ratio(cust):.2f}",
             {"unsupported": bad, "wrong_language": wrong_lang, **({"client_rules": det} if det else {})}, t0)
    text = r.text
    if bad or wrong_lang or probs:
        fix = list(probs)
        if bad:
            fix.append(f"These numbers are not in the SECTIONS/CALC: {bad}. Remove them.")
        if wrong_lang:
            fix.append(f"Write the whole reply in the same language as the customer's message: «{cust}».")
        msgs += [{"role": "assistant", "content": r.text}, {"role": "user", "content": " ".join(fix) + " Rewrite the answer."}]
        r2 = await run.chat("answer_retry", msgs, purpose="answer", max_tokens=700)
        t0 = time.perf_counter()
        bad2, wrong2, _, det2 = check(r2.text)
        run.step("answer_check_retry", "code", "after rewrite",
                 {"unsupported": bad2, "wrong_language": wrong2, **({"client_rules": det2} if det2 else {})}, t0)
        if bad2:
            return None  # unsupported numbers are never shown; a language miss is tolerated
        text = r2.text
        if det2 and opts.fixer:
            text = opts.fixer(text, det2)
            run.step("client_rule_fallback", "code", "deterministic fixes", det2)
    return text


SYS_SOURCES_ONLY = """You are the customer assistant for the business described by the PASSAGES. Answer using ONLY the PASSAGES. If they don't contain the answer, say you don't know.
Do any arithmetic needed carefully and show it. Cite passage ids like [S13]. Be concise (2-6 sentences)."""


async def keyword_answer(run: Run, index: KnowledgeIndex, query: str, profile_block: str = "", k: int = 6) -> tuple[str, list[str]]:
    """Baseline (classic keyword RAG): top-k search hits straight to the answer model. For evaluation comparisons."""
    hits = [h.id for h in await index.search(query, "section", k)]
    run.step("keyword_search", "code", query, hits)
    r = await run.chat("answer", [{"role": "system", "content": profile_block + SYS_SOURCES_ONLY},
                                  {"role": "user", "content": f"QUESTION: {query}\n\nPASSAGES:\n{section_context(index, hits) or '(none)'}"}],
                       purpose="answer", max_tokens=700)
    return r.text, hits
