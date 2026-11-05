"""The safe expression language used by Condition cards, trigger `when` and Set cards.

Python syntax, but only a whitelist: comparisons, and/or/not, attribute access on known roots, constants, lists,
and registered functions. No arithmetic, no subscripts, no lambdas, no dunder attributes, never eval().

    has(slots.name) and valid(slots.phone, 'phone')
    trigger.choice == 'complaint'
    state.lead_saved != slots.phone

Roots: slots, vars, turn, trigger, state, contacts (+ any you add). Functions: has, len, lower, valid
(+ any you register, e.g. engine.register("open_now", fn)).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

MAX_LEN = 300
DEFAULT_ROOTS = frozenset({"slots", "vars", "turn", "trigger", "state", "contacts"})

_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare, ast.Eq, ast.NotEq,
            ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Name, ast.Attribute,
            ast.Constant, ast.Call, ast.Load, ast.List, ast.Tuple)

_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in (b or []), ast.NotIn: lambda a, b: a not in (b or []),
    ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b,
}


class ExprError(ValueError):
    """An expression is not allowed or cannot be evaluated."""


@dataclass
class EvalContext:
    """What functions see besides their arguments: the environment and a validator hook (for valid())."""

    env: Mapping[str, Any]
    validate: Callable[[Any, str], Any | None] = lambda value, name: None


ExprFunction = Callable[..., Any]  # fn(ctx: EvalContext, *args)


def _has(ctx: EvalContext, value: Any = None) -> bool:
    return value not in (None, "", [], {})


def _len(ctx: EvalContext, value: Any = None) -> int:
    try:
        return len(value or [])
    except TypeError:
        return 0


def _lower(ctx: EvalContext, value: Any = None) -> str:
    return str(value or "").lower()


def _valid(ctx: EvalContext, value: Any = None, validator: Any = None) -> bool:
    return ctx.validate(value, str(validator)) is not None


@dataclass
class ExprEngine:
    roots: set[str] = field(default_factory=lambda: set(DEFAULT_ROOTS))
    functions: dict[str, ExprFunction] = field(
        default_factory=lambda: {"has": _has, "len": _len, "lower": _lower, "valid": _valid})

    def register(self, name: str, fn: ExprFunction) -> ExprFunction:
        """Add a function: fn(ctx: EvalContext, *args). Example: engine.register("open_now", lambda ctx: ...)."""
        if not name.isidentifier() or name.startswith("_"):
            raise ValueError(f"invalid function name {name!r}")
        self.functions[name] = fn
        _check_cached.cache_clear()
        return fn

    def add_root(self, name: str) -> None:
        self.roots.add(name)
        _check_cached.cache_clear()

    def check(self, expr: str) -> ast.Expression:
        """Parse and validate; raises ExprError with a message a human can act on."""
        return _check_cached(expr, frozenset(self.roots), frozenset(self.functions))

    def eval(self, expr: str, ctx: EvalContext) -> Any:
        tree = self.check(expr)
        return self._ev(tree, ctx)

    # --------------------------------------------------------------------------------------- evaluation
    def _ev(self, n: ast.AST, ctx: EvalContext) -> Any:
        if isinstance(n, ast.Expression):
            return self._ev(n.body, ctx)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.List | ast.Tuple):
            return [self._ev(x, ctx) for x in n.elts]
        if isinstance(n, ast.Name):
            return ctx.env.get(n.id)
        if isinstance(n, ast.Attribute):
            base = self._ev(n.value, ctx)
            return base.get(n.attr) if isinstance(base, Mapping) else None
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(self._ev(v, ctx) for v in n.values)
            return any(self._ev(v, ctx) for v in n.values)
        if isinstance(n, ast.UnaryOp):  # only `not` is allowed
            return not self._ev(n.operand, ctx)
        if isinstance(n, ast.Compare):
            left = self._ev(n.left, ctx)
            for op, comp in zip(n.ops, n.comparators, strict=True):
                right = self._ev(comp, ctx)
                try:
                    if not _OPS[type(op)](left, right):
                        return False
                except TypeError:  # e.g. None < 3: treat as false, never crash a conversation
                    return False
                left = right
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            return self.functions[n.func.id](ctx, *[self._ev(a, ctx) for a in n.args])
        raise ExprError(f"cannot evaluate {type(n).__name__}")


@lru_cache(maxsize=2048)
def _check_cached(expr: str, roots: frozenset[str], functions: frozenset[str]) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip() or len(expr) > MAX_LEN:
        raise ExprError(f"an expression must be a non-empty string of at most {MAX_LEN} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from None
    for n in ast.walk(tree):
        if not isinstance(n, _ALLOWED):
            raise ExprError(f"'{type(n).__name__}' is not allowed (only comparisons, and/or/not, names, constants, "
                            f"lists and function calls)")
        if isinstance(n, ast.UnaryOp) and not isinstance(n.op, ast.Not):
            raise ExprError("only the 'not' unary operator is allowed")
        if isinstance(n, ast.Call) and (not (isinstance(n.func, ast.Name) and n.func.id in functions) or n.keywords):
            raise ExprError(f"only these functions can be called (positional arguments): {', '.join(sorted(functions))}")
        if isinstance(n, ast.Attribute) and n.attr.startswith("_"):
            raise ExprError(f"attribute '{n.attr}' is not allowed")
        if isinstance(n, ast.Name) and n.id not in roots and n.id not in functions:
            raise ExprError(f"unknown name '{n.id}' (use {', '.join(sorted(r + '.' for r in roots))})")
    return tree


default_engine = ExprEngine()


def check_expr(expr: str) -> ast.Expression:
    return default_engine.check(expr)
