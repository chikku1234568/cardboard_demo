"""Scripted demo outputs (drift, team conflict, brand, abstain, agent) -> docs/results/*.json.

Reads the profile back from Neo4j (run `python -m stylemem.ingest --reset` first), so the
answers shown are what the service returns. Also checks that the Neo4j-loaded profile and
the in-process engine give identical suggestions.
"""
from __future__ import annotations

import json

from . import data
from .agent import cut_from_project, local_suggest, make_agent
from .brand import Brand
from .config import RESULTS
from .engine import MemoryEngine
from .evaluate import decisions_from_events
from .graph import Neo4jGraph
from .suggest import suggest

TH_JUMP = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}
BROLL = {"footage_type": "broll", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}
VERTICAL = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "9:16", "section": "hook"}


def _w(name, obj):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / name).write_text(json.dumps(obj, indent=1))
    return obj


def main():
    g = Neo4jGraph()
    prof = g.load_profile()
    out = {}

    # ---- drift
    hist = g.history("ava", "transition", "footage_type=talking_head|prev_edit=jump_cut", "any")
    hist_b = g.history("ava", "transition", "footage_type=broll", "any")
    trace = json.loads((RESULTS / "drift_trace.json").read_text())["with_statements"] if (RESULTS / "drift_trace.json").exists() else []
    out["drift"] = _w("demo_drift.json", {
        "planted": data.ground_truth()["drift"],
        "graph_history_talking_head_after_jump_cut": hist,
        "graph_history_broll_control": hist_b,
        "suggest_now_talking_head_after_jump_cut": suggest(prof, TH_JUMP, "ava", "private", "transition"),
        "suggest_now_broll": suggest(prof, BROLL, "ava", "private", "transition"),
        "suggest_probe_by_session": [{k: t[k] for k in ("session_id", "drift_probe", "drift_conf", "drift_abstain",
                                                        "control_probe", "control_conf")} for t in trace],
    })

    # ---- team vs private conflict (ben, 9:16 captions)
    out["conflict"] = _w("demo_conflict.json", {
        "team_project (proj_team_northwind_*, owner team)": suggest(prof, VERTICAL, "ben", "team", "caption_style"),
        "ben_private_project": suggest(prof, VERTICAL, "ben", "private", "caption_style"),
        "ben_own_project_under_team_brand_scope": suggest(prof, VERTICAL, "ben", "team", "caption_style",
                                                          project_owner="ben"),
    })

    # ---- brand
    b = Brand.load("northwind")
    viol = [dict(r) for r in g.run("""MATCH (o:Op)-[v:VIOLATES]->(b:Brand)
        RETURN o.op_id AS op_id, o.author AS author, o.event_type AS event_type, o.category AS category,
               o.action AS action, v.rule AS rule ORDER BY o.ts""")]
    planted = {v["op_id"] for v in data.ground_truth()["brand_violations_planted"]}
    out["brand"] = _w("demo_brand.json", {
        "ben_font_in_brand_project": suggest(prof, {"format": "9:16", "section": "hook"}, "ben", "team", "font",
                                             project_owner="ben"),
        "ben_font_in_team_project": suggest(prof, {"format": "9:16", "section": "hook"}, "ben", "team", "font"),
        "ben_font_in_private_project (no brand)": suggest(prof, {"format": "9:16", "section": "hook"}, "ben",
                                                          "private", "font"),
        "check_director_proposals": [{"category": c, "action": a, "allowed": b.blocks(c, a) is None,
                                      "rule": b.blocks(c, a)} for c, a in
                                     [("transition", "glitch"), ("font", "Comic Sans MS"), ("font", "Bebas Neue"),
                                      ("music_mood", "dramatic_horror"), ("transition", "whip_pan"), ("font", "Inter")]],
        "violating_ops_in_history": {"n": len(viol), "planted_covered": sorted(planted & {v["op_id"] for v in viol}),
                                     "planted_total": len(planted), "ops": viol},
    })

    # ---- abstain
    out["abstain"] = _w("demo_abstain.json", {
        "fully_unseen_context": suggest(prof, {"footage_type": "drone", "prev_edit": "aerial_reveal", "format": "4:5"},
                                        "ava", "private", "transition"),
        "partly_unseen_context (only section=hook known)": suggest(prof, {"footage_type": "drone", "prev_edit": "aerial_reveal",
                                                                          "format": "4:5", "section": "hook"},
                                                                   "ava", "private", "transition"),
        "sparse_context_ava_product_9x16_outro_caption": suggest(prof, {"footage_type": "product", "format": "1:1",
                                                                        "section": "outro"}, "ava", "private",
                                                                 "caption_style"),
        "cara_private_no_memory": suggest(prof, TH_JUMP, "cara", "private", "transition"),
    })

    # ---- engine vs Neo4j consistency
    eng = MemoryEngine()
    eng.ingest(data.events())
    eng.finalize()
    ep = eng.profile()
    decs = [d for d in decisions_from_events(data.events("heldout"))]
    mism = []
    for d in decs:
        a = suggest(ep, d["context"], d["actor"], d["project_scope"], d["category"], d["subject"], d["brand_id"])
        b2 = suggest(prof, d["context"], d["actor"], d["project_scope"], d["category"], d["subject"], d["brand_id"])
        if (a["best_guess"], a["abstain"], round(a["confidence"], 3)) != (b2["best_guess"], b2["abstain"], round(b2["confidence"], 3)):
            mism.append({"decision_id": d["decision_id"], "engine": (a["best_guess"], a["confidence"]),
                         "neo4j": (b2["best_guess"], b2["confidence"])})
    out["consistency"] = _w("check_engine_vs_neo4j.json", {"n_queries": len(decs), "mismatches": len(mism),
                                                            "examples": mism[:10]})

    # ---- agent
    for name, (pid, actor, fmt) in {"agent_ava_private_16x9.json": ("proj_ava_12", "ava", "16:9"),
                                    "agent_ben_team_project_9x16.json": ("proj_team_northwind_11", "ben", "9:16"),
                                    "agent_ben_private_9x16.json": ("proj_ben_12", "ben", "9:16")}.items():
        cut = cut_from_project(pid, actor, fmt, title="Demo cut")
        res = make_agent(local_suggest(prof)).invoke({"cut": cut})
        _w(name, {"cut": cut, "ops": res["ops"], "ask_user": res["ask_user"], "notes": res.get("notes"),
                  "llm_note": res.get("llm_note", "(no LLM key in env: deterministic agent, no LLM note)")})
    g.close()
    short = {
        "drift_now": {k: out["drift"]["suggest_now_talking_head_after_jump_cut"][k] for k in ("action", "confidence", "abstain")},
        "broll_now": {k: out["drift"]["suggest_now_broll"][k] for k in ("action", "confidence", "abstain")},
        "conflict": {k: (v["action"], v["confidence"], [(c["private"]["action"], c["team"]["action"]) for c in v["conflicts"]])
                     for k, v in out["conflict"].items()},
        "brand": {k: (v["action"], v["best_guess"], [x["action"] for x in v["blocked"]])
                  for k, v in out["brand"].items() if isinstance(v, dict) and "action" in v},
        "abstain": {k: (v["abstain"], v["why"][-1]) for k, v in out["abstain"].items()},
        "consistency": {k: out["consistency"][k] for k in ("n_queries", "mismatches")},
    }
    print(json.dumps(short, indent=1))


if __name__ == "__main__":
    main()
