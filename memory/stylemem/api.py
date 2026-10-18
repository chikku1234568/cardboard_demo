"""FastAPI service over the Neo4j graph.

  uvicorn stylemem.api:app --port 8808     (from memory/)

The style profile is materialised from Neo4j at startup (and on POST /reload) into a
read cache, so suggest() never walks the graph in the middle of an edit.
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import data
from .brand import Brand
from .config import CATEGORIES, DEFAULT
from .context import key as ctx_key
from .graph import Neo4jGraph
from .suggest import suggest as _suggest

app = FastAPI(title="Cardboard style memory (#8 demo, synthetic data)", version="0.1")
STATE: dict = {}


def _load():
    g = STATE.get("graph") or Neo4jGraph()
    STATE["graph"] = g
    STATE["profile"] = g.load_profile()
    STATE["loaded_slots"] = len(STATE["profile"].views)


@app.on_event("startup")
def startup():
    _load()


class Context(BaseModel):
    footage_type: str | None = None
    prev_edit: str | None = None
    format: str | None = None
    section: str | None = None


class SuggestRequest(BaseModel):
    context: Context
    actor: str = Field(examples=["ava"])
    project_scope: Literal["private", "team"] = "private"
    category: str | None = Field(None, description="one of the vocab categories; omit for all")
    project_owner: str | None = Field(None, description="defaults to actor (private) or the actor's team (team)")
    brand_id: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "slots_cached": STATE.get("loaded_slots"), "params": DEFAULT.__dict__}


@app.post("/reload")
def reload():
    _load()
    return {"slots_cached": STATE["loaded_slots"]}


@app.post("/suggest")
def suggest(req: SuggestRequest):
    cats = [req.category] if req.category else list(CATEGORIES)
    for c in cats:
        if c not in CATEGORIES:
            raise HTTPException(400, f"unknown category {c}")
    out = [_suggest(STATE["profile"], req.context.model_dump(), req.actor, req.project_scope, c,
                    project_owner=req.project_owner, brand_id=req.brand_id) for c in cats]
    return out[0] if req.category else {"suggestions": out}


@app.get("/check")
def check(category: str, action: str, brand_id: str = "northwind"):
    """Brand hard-constraint gate for a proposed action (e.g. a Director proposal)."""
    b = Brand.load(brand_id)
    if b is None:
        raise HTTPException(404, "unknown brand")
    rule = b.blocks(category, action)
    return {"category": category, "action": action, "brand_id": brand_id, "allowed": rule is None, "rule": rule}


@app.get("/stats")
def stats():
    return STATE["graph"].stats()


@app.get("/preferences/history")
def history(owner: str, category: str, footage_type: str | None = None, prev_edit: str | None = None,
            format: str | None = None, section: str | None = None, observed_in: str | None = None,
            all_contexts: bool = False):
    ck = None if all_contexts else ctx_key({"footage_type": footage_type, "prev_edit": prev_edit,
                                            "format": format, "section": section})
    return {"owner": owner, "category": category, "context_key": ck,
            "history": STATE["graph"].history(owner, category, ck, observed_in)}
