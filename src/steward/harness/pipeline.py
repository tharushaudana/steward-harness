"""One customer turn through the harness.

  1 load      session state (compare-and-set on save; one retry on a concurrent update)
  2 gates     handover pause -> holding message; template-only channel -> its template (no model calls)
  3 triage    ONE decision call (journey + policy triggers, resume, refusal) in parallel with understanding the message
  4 resume    a journey waiting at an Ask: resume it, count re-asks, or give up
  5 match     takeover journeys / exclusive policies / taps; contact-only and small-talk short-cuts
  6 execute   journeys run concurrently with the knowledge path; per-part routing lets part triggers ride along and
              claim their part; late triggers when the rewrite changed the message and the triage was unsure
  7 compose   prefix | body | suffix with approved templates (steward.harness.compose)
  8 commit    save state, then deliver events (outbox)
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from steward.config.models import Config
from steward.engine.calc import run_calcs
from steward.engine.common import signature
from steward.engine.core import Engine, greeting_text, profile_block
from steward.engine.generate import GenerateOptions, generate
from steward.engine.preprocess import meta_answer, understand
from steward.engine.route import merge_part_plans, route
from steward.errors import BudgetExceeded, ProviderError, StateConflict
from steward.events import EventBus, Outbox, Trace
from steward.harness.compose import compose
from steward.harness.expr import ExprEngine
from steward.harness.journeys import run_journey
from steward.harness.rules import make_checker, make_fixer, rules_block, strip_emoji
from steward.harness.templates import all_template_text, template_text
from steward.harness.triggers import Fired, fired, trigger_questions
from steward.harness.turn import HISTORY_TURNS, Outcome, TurnContext, make_env, new_state
from steward.knowledge.index import KnowledgeIndex, script_ratio
from steward.providers.base import Question
from steward.stores.base import Stores, TurnLog
from steward.tools.registry import ToolRegistry
from steward.types import Message, Reply


def reply_language(cfg: Config, msg: Message) -> str:
    langs = cfg.settings.languages or ["en"]
    if msg.lang:
        return msg.lang
    sm = cfg.settings.script_lang
    if not sm:
        return langs[0]
    return sm.get("latin", langs[0]) if script_ratio(msg.text) >= 0.5 else sm.get("other", sm.get("latin", langs[0]))


def business_context(cfg: Config, index: KnowledgeIndex) -> str:
    """What the business is and offers, so triggers don't judge in-domain products as off-topic. From the profile and
    the knowledge base itself (topic titles + approved section titles)."""
    p = cfg.profile
    approved = index.approved_titles()
    return (f"{p.identity} {p.purpose}".strip() + "\nTopics it covers: " + "; ".join(t.title for t in index.topics)
            + ("\nApproved offerings and information: " + "; ".join(approved) if approved else ""))[:2500]


class Pipeline:
    def __init__(self, engine: Engine, stores: Stores, tools: ToolRegistry, expr: ExprEngine, events: EventBus) -> None:
        self.engine, self.stores, self.tools, self.expr, self.events = engine, stores, tools, expr, events

    async def handle(self, cfg: Config, msg: Message) -> Reply:
        for attempt in range(2):
            reply, saved = await self._turn(cfg, msg, retry=attempt > 0)
            if saved:
                return reply
        raise StateConflict(f"session {msg.session!r} was updated concurrently twice; try again")

    # --------------------------------------------------------------------------------------------------- the turn
    async def _turn(self, cfg: Config, msg: Message, retry: bool) -> tuple[Reply, bool]:
        loaded, version = await self.stores.sessions.load(msg.session)
        state = loaded or new_state()
        for k, v in new_state().items():
            state.setdefault(k, v)
        state["turn"] += 1
        lang = reply_language(cfg, msg)
        trace = Trace()
        run = self.engine.run(trace)
        outbox = Outbox()
        ctx = TurnContext(cfg, msg, state, make_env(cfg, state, msg, lang), lang, run, self.tools, self.expr, outbox,
                          cfg.settings.tools_mode)
        flags = state["flags"]
        history: list[dict[str, str]] = state["history"]
        meta: dict[str, Any] = {"standalone": None, "policies": [], "units": [], "results": []}

        async def finish(text: str, status: str) -> tuple[Reply, bool]:
            if cfg.rules.no_emoji:
                text = strip_emoji(text)
            std = meta["standalone"] or msg.text
            state["history"] = [*history, {"q": std, "a": text}][-HISTORY_TURNS:]
            handover_now = bool(flags.get("handover_active")) and status == "handover"
            ok = await self.stores.sessions.compare_and_set(msg.session, state, version)
            if not ok:
                return Reply(text, "error", msg.session, trace), False
            run.step("state", "state", "session state saved",
                     {"slots": dict(state["slots"]), "flags": dict(flags), "pending": state.get("pending")})
            reply = Reply(text=text, status=status, session=msg.session, trace=trace,  # type: ignore[arg-type]
                          standalone=meta["standalone"] if meta["standalone"] and meta["standalone"] != msg.text else None,
                          state={"slots": dict(state["slots"]), "flags": dict(flags), "pending": state.get("pending"),
                                 "turn": state["turn"]},
                          journeys=[o.summary() for o in ctx.outcomes], policies=list(meta["policies"]),
                          evidence={"sections": meta["units"],
                                    "values": sorted({v for r in meta["results"] for v in r.get("value_ids", [])})})
            for o in ctx.outcomes:
                outbox.add("journey_end", {"session": msg.session, "journey": o.journey, "status": o.status})
            if handover_now:
                outbox.add("handover", {"session": msg.session, "slots": dict(state["slots"]), "message": msg.text})
            outbox.add("turn_end", {"session": msg.session, "status": status, "cost_usd": trace.totals.cost_usd})
            if self.stores.chatlog is not None:
                await self.stores.chatlog.append(TurnLog(msg.session, msg.text, text, status, time.time(),
                                                         trace.totals.cost_usd, trace.totals.latency_ms,
                                                         reply.standalone, trace.to_dicts(), msg.is_test))
            await outbox.flush(self.events)
            return reply, True

        try:
            # ------------------------------------------------------------------ 2. gates (code only, $0)
            if flags.get("handover_active"):
                text = template_text(cfg, cfg.settings.paused_template, lang, ctx.env) or "A member of our team will reply shortly."
                run.step("paused", "code", "handover active: the assistant is paused until a human releases this session", text)
                return await finish(text, "paused")
            channel = cfg.channels.get(msg.channel)
            if channel is not None and channel.mode == "template_only":
                text = template_text(cfg, channel.template, lang, ctx.env)
                run.step("channel", "code", f"template-only channel '{msg.channel}'", text)
                return await finish(text, "template")

            # ------------------------------------------------------------------ 3. triage || understanding
            index = await self.engine.index()
            tri_qs = trigger_questions(cfg, self.expr, ctx.eval_ctx())
            pend = state.get("pending")
            contact = cfg.settings.contact_slot
            if pend:
                tri_qs["r_resume"] = {"type": "noul", "instructions": "Does the customer's latest message give what the assistant "
                                      f"asked for ({', '.join(pend['require'])}) or reply to that request?"}
            if not state["slots"].get(contact) and state.get("asks_made", 0) > 0 and not flags.get("contact_refused"):
                tri_qs["r_refuse"] = {"type": "noul",
                                      "instructions": "Does the customer refuse or decline to share their contact details?"}
            tri_task = (asyncio.create_task(run.decide("triage", {
                "latest_message": msg.text, "earlier_customer_messages": [h["q"] for h in history[-3:]],
                "business": business_context(cfg, index)}, tri_qs, f"{len(tri_qs)} harness questions"))
                if tri_qs else None)
            u = await understand(run, index, msg.text, history)
            tri: dict[str, Any] = await tri_task if tri_task else {}
            meta["standalone"] = u.standalone
            ctx.env["turn"]["standalone"] = u.standalone
            if float((tri.get("r_refuse") or {}).get("noul", 0) or 0) >= 0.6:
                flags["contact_refused"] = True
            if ctx.env["turn"]["contact_in_message"]:
                flags.pop("contact_refused", None)

            # ------------------------------------------------------------------ 4. resume a waiting journey
            resumed = await self._resume(ctx, pend, tri)

            # ------------------------------------------------------------------ 5. match
            fired_all = [f for f in fired(cfg, tri) if f.id != resumed]
            jf = [f for f in fired_all if f.kind == "journey"]
            pf = [f for f in fired_all if f.kind == "policy"]
            takeover = next((f for f in jf if f.takeover == "turn" and f.mode == "exclusive"), None)
            excl_policy = next((f for f in pf if f.policy and f.policy.action.exclusive), None)
            # contact-only: the message is (almost) just the contact, e.g. "Ada, 202 555 0147" -> no knowledge answer
            residual = re.sub(r"[\d+\-\s,.()]+", " ", msg.text).split() if ctx.env["turn"]["contact_in_message"] else None
            contact_only = residual is not None and len(residual) <= 4
            kind = u.kind
            run.step("harness_match", "code", f"kind={kind}", {
                "fired": [(f.id, f.choice, round(f.score, 2)) for f in fired_all], "takeover": takeover and takeover.id,
                "exclusive_policy": excl_policy and excl_policy.id, "contact_only": contact_only, "resumed": resumed})
            policy_tpls: list[str] = []
            instructions: list[str] = []
            run_js: list[Fired]
            kb_needed = True
            if takeover:
                run_js = [takeover] + [f for f in jf if f.mode == "tap" and f is not takeover]
                kb_needed = False
            elif excl_policy:
                self._apply_policy(excl_policy, meta, instructions, policy_tpls)
                run_js = [f for f in jf if f.mode == "tap"]
                kb_needed = False
            else:
                run_js = [f for f in jf if f.mode == "tap" or (f.scope == "part" and not u.parts)]
                if not u.parts and any(f.mode == "exclusive" for f in run_js):
                    kb_needed = False
                if contact_only or kind in ("chitchat", "about_assistant", "about_conversation") or resumed:
                    kb_needed = kb_needed and kind in (None, "request") and not contact_only and not resumed
            if not kb_needed and not run_js and not ctx.outcomes and not policy_tpls:
                if kind in ("about_assistant", "about_conversation"):
                    return await finish(await meta_answer(run, profile_block(cfg.profile), msg.text, history), "meta")
                if kind == "chitchat":
                    if flags.get("welcomed"):
                        return await finish(await meta_answer(run, profile_block(cfg.profile), msg.text, history), "chitchat")
                    flags["welcomed"] = True
                    return await finish(greeting_text(cfg.profile, lang), "chitchat")

            # ------------------------------------------------------------------ 6. journeys || knowledge
            async def run_all() -> None:
                for f in run_js:
                    assert f.journey is not None
                    ctx.outcomes.append(await run_journey(ctx, f.journey, Outcome(f.id), trigger=f))

            journeys_task = asyncio.create_task(run_all())
            kb_text, status = "", "answered"
            try:
                if kb_needed:
                    kb_text, status = await self._knowledge(ctx, index, u, tri, pf, run_js, resumed, meta, instructions,
                                                            policy_tpls)
            finally:
                await journeys_task
            # ------------------------------------------------------------------ 7. compose + 8. commit
            text, st = compose(ctx, policy_tpls, kb_text, status)
            return await finish(text, st)
        except BudgetExceeded as e:
            outbox.add("budget_exceeded", {"session": msg.session, "error": str(e)})
            return await finish(cfg.profile.escalation or "Sorry, I can't answer right now.", "budget_exceeded")
        except ProviderError as e:
            outbox.add("error", {"session": msg.session, "error": str(e)})
            return await finish(cfg.profile.escalation or "Sorry, something went wrong. Please try again.", "error")

    # --------------------------------------------------------------------------------------------------- helpers
    async def _resume(self, ctx: TurnContext, pend: dict[str, Any] | None, tri: dict[str, Any]) -> str | None:
        if not pend:
            return None
        cfg, state, run = ctx.cfg, ctx.state, ctx.run
        j = cfg.journey(pend["journey"])
        got_contact = bool(ctx.env["turn"]["contact_in_message"]) and cfg.settings.contact_slot in pend["require"]
        if j and (got_contact or float((tri.get("r_resume") or {}).get("noul", 0) or 0) >= 0.5):
            start = next((e.to for e in j.edges if e.from_ == f"{pend['node']}.resume"), None)
            state["pending"] = None
            o = await run_journey(ctx, j, Outcome(j.id), start=start)
            if o.status == "suspended" and o.pending:  # replied, but asked again (e.g. an invalid value): cap the loop
                o.pending["reasks"] = pend.get("reasks", 0) + 1
                if o.pending["reasks"] > cfg.settings.max_reasks:
                    run.step("pending_abandoned", "code", pend["journey"], {"reasks": o.pending["reasks"]})
                    ctx.outbox.add("ask_abandoned", {"session": ctx.msg.session, "journey": j.id})
                    gave_up = next((e.to for e in j.edges if e.from_ == f"{o.pending['node']}.gave_up"), None)
                    o = Outcome(j.id)
                    if gave_up:
                        await run_journey(ctx, j, o, start=gave_up)
                    else:
                        o.status = "abandoned"
            ctx.outcomes.append(o)
            return j.id
        pend["tries"] = pend.get("tries", 0) + 1
        if pend["tries"] >= 2 or state["turn"] - (pend.get("asked_turn") or state["turn"]) > cfg.settings.ask_timeout_turns:
            run.step("pending_abandoned", "code", pend["journey"], {"tries": pend["tries"]})
            ctx.outbox.add("ask_abandoned", {"session": ctx.msg.session, "journey": pend["journey"]})
            state["pending"] = None
            gave_up = next((e.to for e in (j.edges if j else []) if e.from_ == f"{pend['node']}.gave_up"), None)
            if j and gave_up:
                ctx.outcomes.append(await run_journey(ctx, j, Outcome(j.id), start=gave_up))
        return None

    @staticmethod
    def _apply_policy(f: Fired, meta: dict[str, Any], instructions: list[str], policy_tpls: list[str]) -> None:
        if f.id in meta["policies"] or f.policy is None:
            return
        meta["policies"].append(f.id)
        a = f.policy.action
        if a.type == "inject" and a.text:
            instructions.append(a.text)
        if a.template and a.template not in policy_tpls:
            policy_tpls.append(a.template)

    async def _knowledge(self, ctx: TurnContext, index: KnowledgeIndex, u: Any, tri: dict[str, Any], pf: list[Fired],
                         run_js: list[Fired], resumed: str | None, meta: dict[str, Any], instructions: list[str],
                         policy_tpls: list[str]) -> tuple[str, str]:
        cfg, msg, run, state = ctx.cfg, ctx.msg, ctx.run, ctx.state
        for f in pf:
            if not u.parts or f.scope == "turn":
                self._apply_policy(f, meta, instructions, policy_tpls)
        suppress = any(f.policy and f.policy.action.suppress_kb for f in pf if f.id in meta["policies"])
        late_claimed = False
        query_kb: str | None = None
        plan: dict[str, Any] = {"units": [], "topics": []}
        if u.parts:  # per-part routing; part triggers ride in each part's route call and may claim their part
            pq = trigger_questions(cfg, self.expr, ctx.eval_ctx(), scope="part")
            plans = list(await asyncio.gather(*(route(run, index, p, original=p, tag=f"[{i + 1}]", extra_qs=pq)
                                                for i, p in enumerate(u.parts[: run.settings.max_parts]))))
            claimed: set[int] = set()
            have = {f.id for f in run_js} | {resumed}
            for i, pl in enumerate(plans):
                for f in fired(cfg, pl.get("extra", {}), scope="part"):
                    if f.kind == "journey" and f.mode == "exclusive" and f.journey is not None:
                        claimed.add(i)
                        if f.id not in have:
                            have.add(f.id)
                            ctx.outcomes.append(await run_journey(ctx, f.journey, Outcome(f.id, part=i), trigger=f))
                    elif f.kind == "policy":
                        self._apply_policy(f, meta, instructions, policy_tpls)
                        if f.policy and f.policy.action.suppress_kb:
                            claimed.add(i)
            plan = merge_part_plans(run, u.parts, plans, claimed)
            if claimed:  # the knowledge answer only addresses the unclaimed parts
                query_kb = " ".join(p for i, p in enumerate(u.parts) if i not in claimed)
        elif not suppress:
            late_qs: dict[str, Question] | None = None
            if signature(u.standalone) != signature(msg.text):  # context-dependent message: confirm unsure triggers
                late_qs = {k: q for k, q in trigger_questions(cfg, self.expr, ctx.eval_ctx(), scope="part").items()
                           if k.startswith("j_") and float((tri.get(k) or {}).get("noul", 0) or 0) >= 0.3} or None
            cached = None if late_qs else await self.engine.cached_plan(index, u.search)
            if cached is not None:
                plan = cached
                run.step("plan_cache", "cache", "plan cache hit", {k: plan.get(k) for k in ("units", "calcs")})
            else:
                plan = await route(run, index, u.search, original=msg.text if u.kind is None else None, extra_qs=late_qs)
                if plan.get("kind") in ("chitchat", "about_assistant", "about_conversation"):
                    if plan["kind"] == "chitchat":
                        return greeting_text(cfg.profile, ctx.lang), "chitchat"
                    return await meta_answer(run, profile_block(cfg.profile), msg.text, state["history"]), "meta"
            if late_qs:
                have = {f.id for f in run_js} | {resumed}
                late = [f for f in fired(cfg, plan.get("extra", {}), scope="part") if f.kind == "journey" and f.id not in have]
                if late:
                    run.step("late_trigger", "code", "journey triggers re-asked on the rewritten message",
                             {"fired": [(f.id, round(f.score, 2)) for f in late]})
                for f in late:
                    assert f.journey is not None
                    ctx.outcomes.append(await run_journey(ctx, f.journey, Outcome(f.id), trigger=f))
                if any(f.mode == "exclusive" for f in late):
                    plan, late_claimed = {"units": [], "topics": []}, True
        units = plan.get("units", [])
        results = run_calcs(run, index, plan)[0] if plan.get("calcs") else []
        meta["units"], meta["results"] = units, results
        opts = GenerateOptions(profile_block=profile_block(cfg.profile), rules_block=rules_block(cfg), instructions=instructions,
                               reply_like=msg.text, extra_allowed_text=all_template_text(cfg),
                               checker=make_checker(cfg, index, ctx.lang, []), fixer=make_fixer(cfg, index, ctx.lang))
        if units or results:
            for tid in policy_tpls:  # the composer appends these approved texts: the answer must not repeat them
                instructions.append("After your answer the system appends this approved text, so do not repeat its contact "
                                    f"details or wording: «{template_text(cfg, tid, ctx.lang, ctx.env)}»")
            text = await generate(run, index, query_kb or u.standalone, units, results, [],
                                  original=None if query_kb else msg.text, history=state["history"], opts=opts)
            if text and plan.get("confident") and not query_kb and not late_claimed:
                await self.engine.store_plan(index, u.search, plan)
            return text or "", "answered"
        if not policy_tpls and not late_claimed:
            state["flags"]["cannot_answer_turn"] = state["turn"]
            text = await generate(run, index, u.standalone, [], [], [], original=msg.text, history=state["history"], opts=opts)
            return text or "", "insufficient"
        return "", "answered"
