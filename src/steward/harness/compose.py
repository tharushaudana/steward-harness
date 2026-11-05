"""The output collector: assembles the reply as prefix | body | suffix.

  journeys  emit approved templates at a position (before, body, replace, suffix); a pending Ask adds its template
  knowledge the grounded answer (body), unless a journey replaced it
  policies  their templates (body) after the answer
  flow      the welcome on the first inbox turn; the contact request after details were shared (once per turn,
            at most settings.max_asks per session, never after a refusal or when the contact is known)

Template flags: requires_receipt (only sent if that tool succeeded this turn), covers (makes others redundant),
counts_as_ask (counts as asking for a slot), once_per_session (never repeated in a conversation). Each template is
sent at most once per reply.
"""

from __future__ import annotations

from typing import Any

from steward.harness.templates import template_text
from steward.harness.turn import TurnContext


def compose(ctx: TurnContext, policy_templates: list[str], kb_text: str, status: str) -> tuple[str, str]:
    cfg, state, env, lang = ctx.cfg, ctx.state, ctx.env, ctx.lang
    flags, slots = state["flags"], state["slots"]
    ok_tools = {r.tool for r in ctx.receipts if r.ok}
    prefix: list[str] = []
    body: list[str] = []
    replace: list[str] = []
    suffix: list[str] = []
    segments: list[dict[str, Any]] = []
    asked: set[str] = set()
    used: list[str] = []
    no_ask = any(p.action.no_ask for p in cfg.policies if p.action.template in policy_templates)

    def add(target: list[str], tid: str | None, slot: str) -> None:
        t = cfg.templates.get(tid or "")
        if t is None or tid is None:
            return
        if t.requires_receipt and t.requires_receipt not in ok_tools:
            segments.append({"slot": "skipped", "source": f"template:{tid}", "why": f"no {t.requires_receipt} receipt"})
            return
        if tid in used:
            return
        if any(tid in (cfg.templates[u].covers if u in cfg.templates else []) for u in used):
            segments.append({"slot": "skipped", "source": f"template:{tid}", "why": "covered by another template"})
            return
        if t.once_per_session and tid in flags.get("sent_once", []):
            segments.append({"slot": "skipped", "source": f"template:{tid}", "why": "already sent in this conversation"})
            return
        used.append(tid)
        if t.once_per_session:
            flags["sent_once"] = [*flags.get("sent_once", []), tid]
        txt = template_text(cfg, tid, lang, env)
        if txt and txt not in target:
            target.append(txt)
            segments.append({"slot": slot, "source": f"template:{tid}"})
            if t.counts_as_ask:
                asked.add(t.counts_as_ask)

    emits = [(o, e) for o in ctx.outcomes for e in o.emits]
    for _, e in sorted(emits, key=lambda x: x[1].get("position") == "suffix"):  # body/replace first, so 'covers' can apply
        pos = e.get("position", "body")
        target = {"before": prefix, "replace": replace, "suffix": suffix}.get(pos, body)
        add(target, e["template"], pos if pos in ("before", "replace", "suffix") else "body")
    for o in ctx.outcomes:
        if o.status == "suspended" and o.pending:
            state["pending"] = dict(o.pending, asked_turn=state["turn"])
            for a in o.asks:
                asked |= set(a["slots"])
                if a.get("template"):  # empty: an Output card in the journey already asked
                    add(suffix, a["template"], "suffix")
            state["asks_made"] = state.get("asks_made", 0) + 1
    if replace:
        main = replace
    else:
        main = ([kb_text] if kb_text else []) + body
        if kb_text:
            segments.insert(0, {"slot": "body", "source": "knowledge"})
        for tid in policy_templates:
            add(main, tid, "body")
    welcome = cfg.flow.welcome
    if (welcome and not flags.get("welcomed") and (welcome.channel is None or welcome.channel == ctx.msg.channel)
            and not replace and not prefix):
        prefix.insert(0, template_text(cfg, welcome.template, lang, env))
        segments.insert(0, {"slot": "prefix", "source": f"template:{welcome.template}"})
    flags["welcomed"] = True
    ask = cfg.flow.ask_contact
    contact = cfg.settings.contact_slot
    details_shared = (bool(kb_text) and status == "answered") or bool(policy_templates)
    cannot = flags.get("cannot_answer_turn") == state["turn"]
    if (ask and not suffix and not asked and not replace and not no_ask and not slots.get(contact)
            and not flags.get("contact_refused") and (details_shared or cannot)
            and state.get("asks_made", 0) < cfg.settings.max_asks):
        add(suffix, ask.template_when_unanswered if cannot and ask.template_when_unanswered else ask.template, "suffix")
        state["asks_made"] = state.get("asks_made", 0) + 1
    text = "\n\n".join(x for x in prefix + main + suffix if x)
    if not text.strip():
        g = cfg.profile.greeting
        text = g if isinstance(g, str) else (g.get(lang) or next(iter(g.values()), ""))
        segments.append({"slot": "body", "source": "fallback:greeting"})
    ctx.run.step("compose", "code", f"lang={lang} channel={ctx.msg.channel}", segments)
    # 'handover' only on the turn a journey made it (with after_handover='continue' the flag stays set while the
    # conversation goes on)
    handed = any(o.status == "handover" for o in ctx.outcomes)
    st = "handover" if handed else ("needs_input" if state.get("pending") else status)
    return text, st
