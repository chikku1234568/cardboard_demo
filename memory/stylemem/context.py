"""Context keys and the specific -> broad fallback lattice."""
from __future__ import annotations

from itertools import combinations

from .config import CONTEXT_KEYS


def normalize(ctx: dict | None) -> dict:
    ctx = ctx or {}
    return {k: ctx[k] for k in CONTEXT_KEYS if ctx.get(k) not in (None, "", "*")}


def key(ctx: dict) -> str:
    ctx = normalize(ctx)
    if not ctx:
        return "*"
    return "|".join(f"{k}={ctx[k]}" for k in CONTEXT_KEYS if k in ctx)


def parse_key(k: str) -> dict:
    if k == "*":
        return {}
    return dict(part.split("=", 1) for part in k.split("|"))


def subsets(ctx: dict) -> list[dict]:
    """All sub-contexts, most specific first (the full context ... down to '*')."""
    ctx = normalize(ctx)
    keys = [k for k in CONTEXT_KEYS if k in ctx]
    out = []
    for r in range(len(keys), -1, -1):
        for combo in combinations(keys, r):
            out.append({k: ctx[k] for k in combo})
    return out


def parents(ctx: dict) -> list[dict]:
    """Immediate generalisations: drop one key."""
    ctx = normalize(ctx)
    return [{k: v for k, v in ctx.items() if k != drop} for drop in ctx]


def describe(ctx: dict) -> str:
    ctx = normalize(ctx)
    if not ctx:
        return "any context"
    return ", ".join(f"{k}={v}" for k, v in ctx.items())
