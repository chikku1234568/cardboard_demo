"""Small LangGraph agent: given a new cut, ask memory per slot and pre-fill edits as VCS ops.

Graph:  plan_slots -> query_memory -> brand_gate -> compose_ops -> (llm_note | END)
* Every node is deterministic. If an LLM key is present, an optional final node phrases a
  one-paragraph note for the user; with no key that node is skipped (labelled).
* Output ops use the VCS op vocabulary (add_transition, add_effect color_grade, trim_clip,
  add_clip music, add_text captions/title), authored by `director` on_behalf_of the actor.
  Abstentions become `ask_user` items instead of ops; conflicts are attached to the op.
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import data
from .brand import Brand
from .extract import LLMExtractor


class CutState(TypedDict, total=False):
    cut: dict
    slots: list
    suggestions: list
    ops: list
    ask_user: list
    notes: list
    llm_note: str


CUT_BUCKET_SECONDS = {"short": 1.2, "medium": 2.8, "long": 5.5}


def make_agent(suggest_fn):
    """suggest_fn(context, actor, project_scope, category, project_owner, brand_id) -> dict"""

    def plan_slots(s: CutState) -> CutState:
        cut = s["cut"]
        fmt = cut["format"]
        slots = []
        clips = cut["clips"]
        for i, c in enumerate(clips):
            ctx = {"footage_type": c["footage_type"], "prev_edit": c["prev_edit"], "format": fmt, "section": c["section"]}
            if i > 0:
                slots.append({"category": "transition", "clip": c, "from_clip": clips[i - 1]["clip_id"], "context": ctx})
            slots.append({"category": "color_grade", "clip": c, "context": ctx})
            slots.append({"category": "cut_length", "clip": c, "context": ctx})
        for sec in dict.fromkeys(c["section"] for c in clips):
            first = next(c for c in clips if c["section"] == sec)
            slots.append({"category": "music_mood", "clip": first, "section": sec,
                          "context": {"footage_type": first["footage_type"], "format": fmt, "section": sec}})
        slots.append({"category": "font", "clip": clips[0], "role": "title",
                      "context": {"format": fmt, "section": clips[0]["section"]}})
        b = Brand.load(cut["brand_id"]) if cut.get("brand_id") else None
        if fmt == "9:16" or (b and b.requirements(fmt, None)):
            slots.append({"category": "caption_style", "clip": clips[0], "role": "captions",
                          "context": {"format": fmt, "footage_type": clips[0]["footage_type"], "section": "hook"}})
        return {"slots": slots, "notes": [f"planned {len(slots)} memory lookups for {len(clips)} clips"]}

    def query_memory(s: CutState) -> CutState:
        cut = s["cut"]
        out = []
        for sl in s["slots"]:
            r = suggest_fn(sl["context"], cut["actor"], cut["project_scope"], sl["category"],
                           cut.get("project_owner"), cut.get("brand_id"))
            out.append({**sl, "result": r})
        return {"suggestions": out}

    def brand_gate(s: CutState) -> CutState:
        cut = s["cut"]
        b = Brand.load(cut["brand_id"]) if cut.get("brand_id") else None
        notes = list(s.get("notes", []))
        if b is None:
            return {"notes": notes + ["no brand: private project, no hard constraints applied"]}
        for sl in s["suggestions"]:
            a = sl["result"]["action"]
            if a and b.blocks(sl["category"], a):     # defence in depth; suggest() already filters
                sl["result"] = {**sl["result"], "action": None, "abstain": True}
                notes.append(f"brand gate blocked {a}")
        notes += b.requirements(cut["format"], "outro")
        return {"suggestions": s["suggestions"], "notes": notes}

    def compose_ops(s: CutState) -> CutState:
        cut = s["cut"]
        ops, ask = [], []
        n = 0
        for sl in s["suggestions"]:
            r, c, cat = sl["result"], sl["clip"], sl["category"]
            if r["abstain"] or not r["action"]:
                ask.append({"category": cat, "clip_id": c["clip_id"], "context": sl["context"],
                            "best_guess": r["best_guess"], "confidence": r["confidence"], "why": r["why"][-1]})
                continue
            n += 1
            a = r["action"]
            if cat == "transition":
                payload = {"transition": {"id": f"tr_new_{n:02d}", "track_id": "V1", "from_clip": sl["from_clip"],
                                          "to_clip": c["clip_id"], "type": a, "duration": 0.4}}
                typ = "add_transition"
            elif cat == "color_grade":
                payload = {"effect": {"id": f"fx_new_{n:02d}", "target_id": c["clip_id"], "type": "color_grade",
                                      "params": {"look": a, "intensity": 0.6}}}
                typ = "add_effect"
            elif cat == "cut_length":
                payload = {"clip_id": c["clip_id"], "edge": "out",
                           "after": {"duration": CUT_BUCKET_SECONDS[a]}, "bucket": a}
                typ = "trim_clip"
            elif cat == "music_mood":
                payload = {"track_id": "A1", "clip": {"id": f"clp_music_{n:02d}", "music_mood": a,
                                                      "asset_label": "music_upbeat" if a in ("upbeat", "corporate") else "music_calm",
                                                      "section": sl["section"]}}
                typ = "add_clip"
            elif cat == "font":
                payload = {"text_layer": {"id": f"txt_title_{n:02d}", "track_id": "T1", "role": "title",
                                          "text": cut.get("title", "Untitled"), "style": {"font": a}}}
                typ = "add_text"
            else:
                payload = {"text_layer": {"id": f"txt_cap_{n:02d}", "track_id": "T1", "role": "captions",
                                          "style": {"preset": a}}}
                typ = "add_text"
            ops.append({"type": typ, "author": "director", "on_behalf_of": cut["actor"], "category": cat,
                        "context": sl["context"], "payload": payload,
                        "memory": {"action": a, "confidence": r["confidence"], "why": r["why"], "source_events": r["source_events"],
                                   "preference_id": r["preference_id"], "conflicts": r["conflicts"],
                                   "blocked": r["blocked"]}})
        return {"ops": ops, "ask_user": ask}

    def llm_note(s: CutState) -> CutState:
        llm = LLMExtractor()
        prompt = json.dumps({"ops": [(o["type"], o["category"], o["memory"]["confidence"]) for o in s["ops"]],
                             "ask_user": [(a["category"], a["clip_id"]) for a in s["ask_user"]]})
        try:
            txt = llm._call("Summarise for a video editor in 2 sentences what was pre-filled and what needs a decision.",
                            prompt)
        except Exception as exc:  # noqa: BLE001
            txt = f"(llm note failed: {type(exc).__name__})"
        return {"llm_note": txt}

    def route(s: CutState):
        return "llm_note" if LLMExtractor().available else END

    g = StateGraph(CutState)
    for name, fn in [("plan_slots", plan_slots), ("query_memory", query_memory), ("brand_gate", brand_gate),
                     ("compose_ops", compose_ops), ("llm_note", llm_note)]:
        g.add_node(name, fn)
    g.set_entry_point("plan_slots")
    g.add_edge("plan_slots", "query_memory")
    g.add_edge("query_memory", "brand_gate")
    g.add_edge("brand_gate", "compose_ops")
    g.add_conditional_edges("compose_ops", route, {"llm_note": "llm_note", END: END})
    g.add_edge("llm_note", END)
    return g.compile()


def http_suggest(base_url: str):
    import httpx

    def fn(context, actor, scope, category, owner, brand_id):
        r = httpx.post(f"{base_url}/suggest", json={"context": context, "actor": actor, "project_scope": scope,
                                                     "category": category, "project_owner": owner,
                                                     "brand_id": brand_id}, timeout=10)
        r.raise_for_status()
        return r.json()
    return fn


def local_suggest(profile):
    from .suggest import suggest

    def fn(context, actor, scope, category, owner, brand_id):
        return suggest(profile, context, actor, scope, category, project_owner=owner, brand_id=brand_id)
    return fn


def cut_from_project(project_id: str, actor: str, fmt: str, title: str = "New cut") -> dict:
    p = data.projects()[project_id]
    return {"project_id": project_id, "actor": actor, "project_scope": p["project_scope"],
            "project_owner": p["owner_subject"], "brand_id": p["brand_id"], "format": fmt, "title": title,
            "clips": [{k: s[k] for k in ("clip_id", "section", "footage_type", "prev_edit")} for s in p["sections"]]}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=None, help="e.g. http://localhost:8808 ; default: load profile from Neo4j directly")
    ap.add_argument("--project", default="proj_ava_12")
    ap.add_argument("--actor", default="ava")
    ap.add_argument("--format", default="16:9")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.api:
        fn = http_suggest(a.api)
    else:
        from .graph import Neo4jGraph
        fn = local_suggest(Neo4jGraph().load_profile())
    cut = cut_from_project(a.project, a.actor, a.format)
    res = make_agent(fn).invoke({"cut": cut})
    out = {"cut": cut, "ops": res["ops"], "ask_user": res["ask_user"], "notes": res.get("notes"),
           "llm_note": res.get("llm_note", "(no LLM key in env: deterministic agent, no LLM note)")}
    s = json.dumps(out, indent=1)
    if a.out:
        open(a.out, "w").write(s)
    print(json.dumps({"n_ops": len(out["ops"]), "n_ask_user": len(out["ask_user"]),
                      "ops": [f'{o["type"]:15s} {o["category"]:13s} {o["context"].get("section") or "":6s} '
                              f'{o["context"].get("footage_type") or "":17s} -> {o["memory"]["action"]:15s} '
                              f'conf={o["memory"]["confidence"]:.2f} conflicts={len(o["memory"]["conflicts"])}'
                              for o in out["ops"]],
                      "ask_user": [(x["category"], x["clip_id"], x["best_guess"], x["confidence"]) for x in out["ask_user"]],
                      "notes": out["notes"], "llm_note": out["llm_note"]}, indent=1))


if __name__ == "__main__":
    main()
