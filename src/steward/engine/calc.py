"""Calculators: safe evaluation of discovered formulas and binding of their inputs (values, user numbers, defaults).
Arithmetic is always done here, in code, never by a language model."""

from __future__ import annotations

import ast
import contextlib
import re
import time
from decimal import Decimal
from typing import Any

from steward.engine.common import Run
from steward.knowledge.index import KnowledgeIndex

ALLOWED_AST = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.Constant,
               ast.Name, ast.Load, ast.Call)
_FUNCS = {"max": max, "min": min, "round": round}


def safe_eval(expr: str, env: dict[str, Any]) -> Any:
    """Evaluate + - * / max min round over numbers and variables. Anything else raises ValueError."""
    tree = ast.parse(expr, mode="eval")
    for n in ast.walk(tree):
        if not isinstance(n, ALLOWED_AST):
            raise ValueError(f"disallowed {type(n).__name__}")
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in _FUNCS):
            raise ValueError("disallowed call")
        if isinstance(n, ast.Constant) and not isinstance(n.value, int | float):
            raise ValueError("non-numeric constant")
        if isinstance(n, ast.Name) and n.id not in _FUNCS and n.id not in env:
            raise ValueError(f"unknown variable '{n.id}'")
    return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}, **_FUNCS}, env)


def expr_vars(expr: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)} - set(_FUNCS)


def fmt(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _steps(expr: str, env: dict[str, Any], val: float) -> str:
    s = expr
    for var, v in sorted(env.items(), key=lambda x: -len(x[0])):
        s = re.sub(rf"\b{re.escape(var)}\b", fmt(v), s)
    return f"{s} = {fmt(round(val, 2))}"


def subvalues(expr: str, env: dict[str, Any]) -> list[float]:
    """Intermediate results of sub-expressions (allowed numbers for the answer check)."""
    out, known = [], {k: Decimal(str(v)) for k, v in env.items() if v is not None}
    for n in ast.walk(ast.parse(expr, mode="eval")):
        if isinstance(n, ast.BinOp | ast.Call):
            names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)} - set(_FUNCS)
            if names <= set(known):
                with contextlib.suppress(Exception):  # a sub-expression that fails is simply not listed
                    out.append(round(float(safe_eval(ast.unparse(n), dict(known))), 4))
    return out


def run_calcs(run: Run, index: KnowledgeIndex, plan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Evaluate the plan's calculators for the bound entities. Returns (results, questions to ask the customer)."""
    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    asks: list[str] = []
    for cid in plan.get("calcs", []):
        c = index.calcs.get(cid)
        if c is None:
            continue
        bound = [e for e in plan.get("entities", []) if e in c.applies_to]
        entity_known = bool(bound)
        if not bound:
            bound = list(c.applies_to)
            names = ", ".join(index.entities[e].title for e in bound if e in index.entities)
            asks.append(f"Which {c.entity_type.replace('_', ' ')} are you on or considering ({names})?")
        for ent in bound:
            env: dict[str, Any] = {}
            used, missing, assumptions, user_missing = [], [], [], []
            for inp in c.inputs:
                v = None
                for e in ([ent] if inp.entity == "$entity" else [inp.entity]) + ([inp.fallback_entity] if inp.fallback_entity else []):
                    v = index.value(e, inp.attribute)
                    if v:
                        break
                if v:
                    env[inp.var] = v.num
                    used.append(v.id)
                    run.followed.add((f"C:{cid}", f"V:{v.id}"))
                    run.retrieved.add(f"V:{v.id}")
                else:
                    missing.append(inp.attribute)
            for ui in c.user_inputs:
                if ui.var in plan.get("bind", {}):
                    env[ui.var] = plan["bind"][ui.var]
                elif ui.default is not None:
                    env[ui.var] = ui.default
                    assumptions.append(ui.assumption or f"{ui.var} = {fmt(ui.default)}")
                else:
                    env[ui.var] = None
                    user_missing.append(ui)
            r: dict[str, Any] = {"calc": cid, "entity": ent, "entity_title": index.entities[ent].title if ent in index.entities else ent,
                                 "title": c.title, "value_ids": used, "assumptions": assumptions, "entity_known": entity_known}
            if missing:
                r.update(status="MISSING_KB", missing=missing)
            elif user_missing:
                expr = c.expression
                for var, val in env.items():
                    if val is not None:
                        expr = re.sub(rf"\b{re.escape(var)}\b", fmt(val), expr)
                const = float(safe_eval(c.expression, {k: Decimal(str(v if v is not None else 0)) for k, v in env.items()}))
                r.update(status="PARTIAL", expression=expr, const=round(const, 4), unknown=[u.var for u in user_missing])
                asks += [u.ask for u in user_missing if u.ask]
            else:
                val = float(safe_eval(c.expression, {k: Decimal(str(v)) for k, v in env.items()}))
                r.update(status="OK", value=round(val, 2), steps=_steps(c.expression, env, val),
                         subvalues=subvalues(c.expression, env))
            results.append(r)
        run.selected.add(f"C:{cid}")
    run.step("calc", "code", plan.get("calcs"), [{x: r.get(x) for x in ("calc", "entity", "status", "value", "expression")}
                                                  for r in results], t0)
    return results, list(dict.fromkeys(asks))


def calc_lines(results: list[dict[str, Any]]) -> list[str]:
    """CALC lines for the answer prompt, plus a combined total per entity (computed here)."""
    out = []
    for r in results:
        tag = f"[C:{r['calc']}:{r['entity']}]"
        assume = f" (assuming {', '.join(r['assumptions'])})" if r.get("assumptions") else ""
        if r["status"] == "OK":
            out.append(f"{tag} {r['title']} for {r['entity_title']}: {r['steps']}{assume}")
        elif r["status"] == "PARTIAL":
            out.append(f"{tag} {r['title']} for {r['entity_title']}: = {r['expression']}  (unknown: {', '.join(r['unknown'])}){assume}")
        else:
            out.append(f"{tag} {r['title']} for {r['entity_title']}: cannot compute, the sources don't list {', '.join(r['missing'])}")
    by_ent: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if r["status"] in ("OK", "PARTIAL"):
            by_ent.setdefault(r["entity"], []).append(r)
    for ent, rs in by_ent.items():
        if len(rs) > 1:
            const = sum(r["value"] if r["status"] == "OK" else r["const"] for r in rs)
            parts = [r["expression"] if r["status"] == "PARTIAL" else fmt(r["value"]) for r in rs]
            tail = f" = {fmt(round(const, 2))}" if all(r["status"] == "OK" for r in rs) else f" (known part = {fmt(round(const, 2))})"
            out.append(f"[C:total:{ent}] Combined total for {rs[0]['entity_title']}: " + " + ".join(parts) + tail)
    return out
