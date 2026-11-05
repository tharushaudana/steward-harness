"""Semantic validation of a Config: graph checks for journeys, references between sections, safe expressions.

Structural errors (wrong types, unknown keys) are caught by the pydantic models when loading; this module checks
what a schema cannot: that every referenced template, tool, slot and port exists, expressions are safe, journeys
are well-formed graphs, and (when a tool registry is given) that every tool implementation is registered.
"""

from __future__ import annotations

import difflib
from collections.abc import Collection

from steward.config.catalog import card, node_ports
from steward.config.models import (
    AskNode,
    ConditionNode,
    Config,
    CustomNode,
    DecisionNode,
    EmitNode,
    EndNode,
    ExtractNode,
    SetNode,
    ToolNode,
    TriggerNode,
)
from steward.errors import Issue
from steward.harness.expr import ExprEngine, ExprError, default_engine


def _close(name: str, options: Collection[str]) -> str:
    m = difflib.get_close_matches(name, list(options), n=1)
    return f"did you mean '{m[0]}'?" if m else ""


def validate(cfg: Config, *, tool_impls: Collection[str] | None = None, expr: ExprEngine | None = None) -> list[Issue]:
    """All problems of a configuration. `tool_impls`: registered tool implementation ids (None = unknown, skip)."""
    expr = expr or default_engine
    issues: list[Issue] = []
    add = issues.append
    templates, slots, tools = cfg.templates, cfg.slots, cfg.tools
    langs = cfg.settings.languages or ["en"]

    def check_template(tid: str | None, path: str, journey: str | None = None, node: str | None = None) -> None:
        if tid and tid not in templates:
            add(Issue(f"unknown template '{tid}'", path, "unknown_template", hint=_close(tid, templates)
                      or "add it under 'templates'", journey=journey, node=node))

    def check_expr(e: str, path: str, journey: str | None = None, node: str | None = None) -> None:
        try:
            expr.check(e)
        except ExprError as err:
            add(Issue(f"expression {e!r}: {err}", path, "bad_expression", journey=journey, node=node))

    # ------------------------------------------------------------------------------ journeys
    seen_j: set[str] = set()
    for ji, j in enumerate(cfg.journeys):
        jp = f"journeys[{ji}]"
        if j.id in seen_j:
            add(Issue(f"duplicate journey id '{j.id}'", jp, "duplicate_id", journey=j.id))
        seen_j.add(j.id)
        ids = [n.id for n in j.nodes]
        byid = {n.id: n for n in j.nodes}
        if len(ids) != len(set(ids)):
            dups = sorted({i for i in ids if ids.count(i) > 1})
            add(Issue(f"duplicate card ids: {', '.join(dups)}", jp, "duplicate_id", journey=j.id))
        triggers = [n for n in j.nodes if isinstance(n, TriggerNode)]
        if len(triggers) != 1:
            add(Issue(f"a journey needs exactly one trigger card (found {len(triggers)})", jp, "trigger_count", journey=j.id))
        if not any(isinstance(n, EndNode) for n in j.nodes):
            add(Issue("a journey needs at least one end card", jp, "no_end", journey=j.id))
        seen_from: set[str] = set()
        targets: set[str] = set()
        for ei, e in enumerate(j.edges):
            ep = f"{jp}.edges[{ei}]"
            src, _, port = e.from_.partition(".")
            if src not in byid:
                add(Issue(f"arrow from unknown card '{src}'", ep, "unknown_node", journey=j.id))
                continue
            if e.to not in byid:
                add(Issue(f"arrow to unknown card '{e.to}'", ep, "unknown_node", journey=j.id, node=src, port=port))
                continue
            ports = node_ports(byid[src])
            if port not in ports:
                add(Issue(f"card '{src}' has no output port '{port}'", ep, "unknown_port", journey=j.id, node=src, port=port,
                          hint=f"ports: {', '.join(ports) or 'none'}"))
            if e.from_ in seen_from:
                add(Issue(f"port '{port}' of card '{src}' has more than one arrow", ep, "multiple_arrows",
                          journey=j.id, node=src, port=port))
            seen_from.add(e.from_)
            targets.add(e.to)
        for ni, n in enumerate(j.nodes):
            np_ = f"{jp}.nodes[{ni}]"
            w = {"journey": j.id, "node": n.id}
            if not isinstance(n, TriggerNode) and n.id not in targets:
                add(Issue("orphan card: no arrow leads to it", np_, "orphan", severity="warning", **w))
            if isinstance(n, CustomNode):
                if card(n.type) is None:
                    add(Issue(f"unknown card type '{n.type}'", np_ + ".type", "unknown_card",
                              hint="register it with steward.config.catalog.register_card", **w))
                continue
            if isinstance(n, TriggerNode | DecisionNode):
                if not n.decision.instructions.strip():
                    add(Issue("the decision question is empty", np_ + ".decision.instructions", "empty_question", **w))
                if n.decision.type == "choice" and not [k for k in n.decision.criteria if k != "none"]:
                    add(Issue("a choice question needs at least one option besides 'none'", np_ + ".decision.criteria",
                              "no_options", **w))
            if isinstance(n, TriggerNode) and n.when:
                check_expr(n.when, np_ + ".when", **w)
            if isinstance(n, ConditionNode):
                check_expr(n.expr, np_ + ".expr", **w)
            if isinstance(n, SetNode):
                for k, v in n.assign.items():
                    root = k.partition(".")[0]
                    if root not in ("state", "vars", "slots") or "." not in k:
                        add(Issue(f"cannot assign to '{k}' (use state.x, vars.x or slots.x)", np_ + ".assign",
                                  "bad_assign", **w))
                    if isinstance(v, str):
                        check_expr(v, f"{np_}.assign.{k}", **w)
            if isinstance(n, EmitNode):
                check_template(n.template, np_ + ".template", **w)
            if isinstance(n, AskNode):
                check_template(n.template, np_ + ".template", **w)
                if not any(e.from_ == f"{n.id}.resume" for e in j.edges):
                    add(Issue("an ask card needs a 'resume' arrow", np_, "ask_without_resume", **w))
                for s in n.require:
                    if s not in slots:
                        add(Issue(f"unknown slot '{s}'", np_ + ".require", "unknown_slot", hint=_close(s, slots), **w))
            if isinstance(n, ExtractNode):
                if not n.slots:
                    add(Issue("choose at least one slot to extract", np_ + ".slots", "no_slots", **w))
                for s in [*n.slots, *n.require]:
                    if s not in slots:
                        add(Issue(f"unknown slot '{s}'", np_ + ".slots", "unknown_slot", hint=_close(s, slots), **w))
            if isinstance(n, ToolNode):
                if n.tool not in tools:
                    add(Issue(f"unknown tool '{n.tool}'", np_ + ".tool", "unknown_tool",
                              hint=_close(n.tool, tools) or "declare it under 'tools'", **w))
                elif tools[n.tool].side_effect and not tools[n.tool].idempotency_key:
                    add(Issue(f"tool '{n.tool}' has side effects but no idempotency_key", f"tools.{n.tool}",
                              "no_idempotency_key", hint="e.g. 'lead:{{slots.phone}}:{{turn.date}}'", **w))

    # ------------------------------------------------------------------------------ tools
    for key, spec in tools.items():
        if tool_impls is not None and spec.impl not in tool_impls:
            add(Issue(f"tool '{key}': implementation '{spec.impl}' is not registered", f"tools.{key}.impl",
                      "unregistered_impl", hint=_close(spec.impl or "", tool_impls)
                      or f"register it in code: @bot.tool('{spec.impl}')"))

    # ------------------------------------------------------------------------------ templates
    for tid, t in templates.items():
        tp = f"templates.{tid}"
        for lg in langs:
            if not t.text.get(lg):
                add(Issue(f"template '{tid}' has no '{lg}' text", tp + ".text", "missing_language", severity="warning"))
        if t.requires_receipt and t.requires_receipt not in tools:
            add(Issue(f"requires_receipt names unknown tool '{t.requires_receipt}'", tp + ".requires_receipt",
                      "unknown_tool", hint=_close(t.requires_receipt, tools)))
        for cov in t.covers:
            if cov not in templates:
                add(Issue(f"covers unknown template '{cov}'", tp + ".covers", "unknown_template", severity="warning"))
        if t.counts_as_ask and t.counts_as_ask not in slots:
            add(Issue(f"counts_as_ask names unknown slot '{t.counts_as_ask}'", tp + ".counts_as_ask", "unknown_slot"))

    # ------------------------------------------------------------------------------ slots, validators
    for sid, s in slots.items():
        if s.validator and s.validator not in cfg.validators:
            add(Issue(f"slot '{sid}' uses unknown validator '{s.validator}'", f"slots.{sid}.validator",
                      "unknown_validator", hint=_close(s.validator, cfg.validators)))
        if s.type == "enum" and not s.values:
            add(Issue(f"enum slot '{sid}' has no values", f"slots.{sid}.values", "no_values"))
        for k in s.value_descriptions:
            if k not in s.values:
                add(Issue(f"value_descriptions has '{k}', which is not one of the slot's values", f"slots.{sid}",
                          "unknown_value", severity="warning"))

    # ------------------------------------------------------------------------------ policies
    seen_p: set[str] = set()
    for pi, p in enumerate(cfg.policies):
        pp = f"policies[{pi}]"
        if p.id in seen_p:
            add(Issue(f"duplicate policy id '{p.id}'", pp, "duplicate_id"))
        seen_p.add(p.id)
        if not p.trigger.instructions.strip():
            add(Issue(f"policy '{p.id}' has an empty question", pp + ".trigger.instructions", "empty_question"))
        if p.when:
            check_expr(p.when, pp + ".when")
        if p.action.type == "template":
            if not p.action.template:
                add(Issue(f"policy '{p.id}' sends a template but names none", pp + ".action.template", "no_template"))
            check_template(p.action.template, pp + ".action.template")
        elif not (p.action.text or "").strip():
            add(Issue(f"policy '{p.id}' injects an empty instruction", pp + ".action.text", "empty_instruction"))

    # ------------------------------------------------------------------------------ flow, channels, settings, rules
    for name, step in (("welcome", cfg.flow.welcome), ("thank_you", cfg.flow.thank_you), ("ask_contact", cfg.flow.ask_contact)):
        if step is not None:
            check_template(step.template, f"flow.{name}.template")
    if cfg.flow.ask_contact is not None:
        check_template(cfg.flow.ask_contact.template_when_unanswered, "flow.ask_contact.template_when_unanswered")
    for name, ch in cfg.channels.items():
        if ch.mode == "template_only" and not ch.template:
            add(Issue(f"template-only channel '{name}' names no template", f"channels.{name}.template", "no_template"))
        check_template(ch.template, f"channels.{name}.template")
    check_template(cfg.settings.paused_template or None, "settings.paused_template")
    if cfg.slots and cfg.settings.contact_slot not in cfg.slots:
        add(Issue(f"settings.contact_slot '{cfg.settings.contact_slot}' is not a slot", "settings.contact_slot",
                  "unknown_slot", severity="warning", hint=_close(cfg.settings.contact_slot, cfg.slots)))
    for t in cfg.rules.claims_without_receipt.receipt_tools:
        if t not in tools:
            add(Issue(f"claims_without_receipt names unknown tool '{t}'", "rules.claims_without_receipt.receipt_tools",
                      "unknown_tool", hint=_close(t, tools)))
    return issues


def errors_only(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]
