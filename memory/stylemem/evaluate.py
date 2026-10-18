"""Held-out evaluation. Real numbers only; everything is synthetic (planted rules).

Protocols
* static : memory built from events_train only, then every held-out decision is predicted.
* online : prequential over the held-out sessions in time order: predict a session's
           decisions from memory, then ingest that session's events, then the next.
Baselines (computed from training decisions only, per category, ignoring context):
* most_frequent_overall     : most frequent final action across all subjects
* most_frequent_per_subject : most frequent final action of that subject (ground_truth.baseline_definition)
Hyper-parameters are selected on TRAIN ONLY (prequential over training sessions), never on held-out.

Run: python -m stylemem.evaluate [--grid]
"""
from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from dataclasses import asdict, replace

from . import data
from .config import DEFAULT, RESULTS, Params
from .engine import POSITIVE, MemoryEngine
from .extract import RuleBasedExtractor, _Labelled
from .suggest import suggest


def decisions_from_events(evs: list[dict]) -> list[dict]:
    """Final user action per decision, derived from events only (no planted labels)."""
    by = defaultdict(list)
    for e in evs:
        if e.get("decision_id") and e.get("category") and e["type"] != "explicit_statement":
            by[e["decision_id"]].append(e)
    out = []
    for did, es in by.items():
        es.sort(key=lambda x: x["ts"])
        final = None
        for x in es:
            if x["type"] in POSITIVE and x.get("action"):
                final = x["action"]
            elif x["type"] == "undo" and x.get("replaced_action") == final:
                final = None          # accepted, then undone later
        e0 = es[0]
        ctx = next((x["context"] for x in es if x["context"]), e0["context"])
        out.append({"decision_id": did, "session_id": e0["session_id"], "subject": e0["subject"],
                    "actor": e0["actor"], "project_scope": e0["project_scope"], "brand_id": e0["brand_id"],
                    "category": e0["category"], "context": ctx, "final_action": final, "ts": es[-1]["ts"]})
    out.sort(key=lambda d: d["ts"])
    return out


def baselines(train_decs: list[dict]):
    overall = defaultdict(Counter)
    per_subj = defaultdict(Counter)
    for d in train_decs:
        if d["final_action"] is None:
            continue
        overall[d["category"]][d["final_action"]] += 1
        per_subj[(d["subject"], d["category"])][d["final_action"]] += 1
    mf_overall = {c: cnt.most_common(1)[0][0] for c, cnt in overall.items()}
    mf_subj = {k: cnt.most_common(1)[0][0] for k, cnt in per_subj.items()}
    return mf_overall, mf_subj


def predict(profile, d, params):
    return suggest(profile, d["context"], d["actor"], d["project_scope"], d["category"],
                   project_owner=d["subject"], brand_id=d["brand_id"], p=params)


def score(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    answered = [r for r in rows if not r["abstain"]]
    return {
        "n": n,
        "baseline_overall_acc": round(sum(r["bl_overall"] == r["label"] for r in rows) / n, 4),
        "baseline_per_subject_acc": round(sum(r["bl_subject"] == r["label"] for r in rows) / n, 4),
        "memory_best_guess_acc": round(sum(r["guess"] == r["label"] for r in rows) / n, 4),
        "memory_abstain_counts_as_wrong_acc": round(sum((not r["abstain"]) and r["pred"] == r["label"] for r in rows) / n, 4),
        "coverage": round(len(answered) / n, 4),
        "abstention_rate": round(1 - len(answered) / n, 4),
        "acc_when_answering": round(sum(r["pred"] == r["label"] for r in answered) / len(answered), 4) if answered else None,
        "acc_on_abstained_best_guess": round(sum(r["guess"] == r["label"] for r in rows if r["abstain"]) /
                                             max(1, n - len(answered)), 4) if n - len(answered) else None,
    }


def _row(d, res, mf_o, mf_s):
    guess = res["best_guess"] or mf_s.get((d["subject"], d["category"]))
    return {"decision_id": d["decision_id"], "subject": d["subject"], "category": d["category"],
            "project_scope": d["project_scope"], "context": d["context"], "label": d["final_action"],
            "pred": res["action"], "guess": guess, "abstain": res["abstain"], "confidence": res["confidence"],
            "bl_overall": mf_o.get(d["category"]), "bl_subject": mf_s.get((d["subject"], d["category"])),
            "n_conflicts": len(res["conflicts"]), "context_used": res["context_used"]}


def _engine(params, **kw):
    return MemoryEngine(params, extractor=_Labelled(RuleBasedExtractor()) if kw.pop("rule", True) else None, **kw)


def run_heldout(params: Params, protocol: str = "static", use_statements: bool = True):
    train = data.events("train")
    held = data.events("heldout")
    train_decs = decisions_from_events(train)
    mf_o, mf_s = baselines(train_decs)
    eng = _engine(params, use_statements=use_statements)
    eng.ingest(train)
    labels = {d["decision_id"]: d for d in data.heldout_decisions()}
    held_decs = [d for d in decisions_from_events(held)]
    assert all(labels[d["decision_id"]]["final_action"] == d["final_action"] for d in held_decs), "label mismatch"
    rows = []
    if protocol == "static":
        prof = eng.profile()
        for d in held_decs:
            rows.append(_row(d, predict(prof, d, params), mf_o, mf_s))
    else:
        by_sess = defaultdict(list)
        for e in held:
            by_sess[e["session_id"]].append(e)
        for sid in data.sorted_sessions_for(held):
            prof = eng.profile()
            for d in [x for x in held_decs if x["session_id"] == sid]:
                rows.append(_row(d, predict(prof, d, params), mf_o, mf_s))
            eng.ingest(by_sess[sid])
    excluded = [r for r in rows if r["label"] is None]
    rows = [r for r in rows if r["label"] is not None]
    return rows, excluded, {"mf_overall": mf_o, "mf_per_subject": {f"{k[0]}/{k[1]}": v for k, v in mf_s.items()}}


def train_prequential(params: Params, warmup: int = 6) -> float:
    """Selection metric on TRAIN only: predict each training session from the sessions before it."""
    train = data.events("train")
    decs = decisions_from_events(train)
    by_sess = defaultdict(list)
    for e in train:
        by_sess[e["session_id"]].append(e)
    eng = _engine(params)
    seen = Counter()
    hit = tot = 0
    for sid in data.sorted_sessions_for(train):
        owner = by_sess[sid][0]["subject"]
        if seen[owner] >= warmup:
            prof = eng.profile()
            for d in (x for x in decs if x["session_id"] == sid and x["final_action"]):
                r = predict(prof, d, params)
                hit += r["best_guess"] == d["final_action"]
                tot += 1
        eng.ingest(by_sess[sid])
        seen[owner] += 1
    return hit / tot if tot else 0.0


DRIFT_PROBE = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}
CONTROL_PROBE = {"footage_type": "broll", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}   # ava's most common b-roll context
CONTROL_PROBE_RARE = {"footage_type": "broll", "prev_edit": "none", "format": "16:9", "section": "body"}


def drift_trace(params: Params, evs=None, use_statements=True):
    """After each of ava's sessions: what would suggest() say for the drift probe and the control?"""
    evs = evs if evs is not None else data.events("train")
    eng = _engine(params, use_statements=use_statements)
    trace = []

    def cb(e, sid):
        if not sid.startswith("ava_"):
            return
        prof = e.profile()
        r = suggest(prof, DRIFT_PROBE, "ava", "private", "transition", project_owner="ava", p=params)
        c = suggest(prof, CONTROL_PROBE, "ava", "private", "transition", project_owner="ava", p=params)
        c2 = suggest(prof, CONTROL_PROBE_RARE, "ava", "private", "transition", project_owner="ava", p=params)
        trace.append({"session_id": sid, "session_no": int(sid.split("_s")[1]),
                      "drift_probe": r["best_guess"], "drift_conf": r["confidence"], "drift_abstain": r["abstain"],
                      "drift_context_used": r["context_used"],
                      "control_probe": c["best_guess"], "control_conf": c["confidence"], "control_abstain": c["abstain"],
                      "control_rare_probe": c2["best_guess"], "control_rare_conf": c2["confidence"],
                      "control_rare_abstain": c2["abstain"]})

    eng.ingest(evs, after_session=cb)
    gt = data.ground_truth()["drift"]
    first = next((t["session_no"] for t in trace if t["session_no"] >= gt["drift_session_no"]
                  and t["drift_probe"] == gt["to_action"] and not t["drift_abstain"]), None)
    lingering = sum(1 for t in trace if first and t["session_no"] > first and t["drift_probe"] != gt["to_action"])
    after = [t for t in trace if t["session_no"] >= gt["drift_session_no"]]
    control_ok = all(t["control_probe"] == "crossfade" or t["control_abstain"] for t in after)
    control_rare_wrong = sum(1 for t in after if t["control_rare_probe"] != "crossfade" and not t["control_rare_abstain"])
    return {"switched_at_session": first,
            "sessions_after_drift_start": (first - gt["drift_session_no"]) if first else None,
            "old_style_lingering_sessions_after_switch": lingering,
            "control_broll_stays_crossfade": control_ok,
            "control_rare_broll_confident_wrong_sessions": control_rare_wrong,
            "n_supersessions": len(eng.supersessions)}, trace


def grid(base: Params = DEFAULT):
    out = []
    for sel, hl, clock, fhl, n in itertools.product(["conditional_chain", "backoff_chain", "max_confidence"], [8, 1e9],
                                                    ["owner_session"], [2, 3], [2, 3]):
        p = replace(base, slot_selection=sel, half_life_sessions=hl, decay_clock=clock, fast_half_life_sessions=fhl,
                    supersede_after_sessions=n)
        d, _ = drift_trace(p)
        out.append({"slot_selection": sel, "half_life_sessions": hl, "decay_clock": clock, "fast_half_life_sessions": fhl,
                    "supersede_after_sessions": n,
                    "train_prequential_acc": round(train_prequential(p), 4), **{f"train_drift_{k}": v for k, v in d.items()}})
    out.sort(key=lambda r: -r["train_prequential_acc"])
    return out


SELECTION_RULE = ("TRAIN ONLY. Among configs within 1.0 accuracy point of the best train-prequential accuracy, "
                  "pick the fastest switch on the training drift (ava s18) with no lingering and the b-roll control "
                  "intact; ties -> fewer supersessions (stability).")


def select(g):
    best = g[0]["train_prequential_acc"]
    ok = [r for r in g if r["train_prequential_acc"] >= best - 0.01 and r["train_drift_switched_at_session"]
          and r["train_drift_old_style_lingering_sessions_after_switch"] == 0 and r["train_drift_control_broll_stays_crossfade"]]
    if not ok:
        return g[0]
    return min(ok, key=lambda r: (r["train_drift_switched_at_session"],
                                  r["train_drift_control_rare_broll_confident_wrong_sessions"],
                                  -r["train_prequential_acc"], r["train_drift_n_supersessions"]))


def threshold_sweep(rows_by_tau):
    return rows_by_tau


def breakdown(rows, key):
    g = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return {k: score(v) for k, v in sorted(g.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true", help="select params on train-only prequential accuracy")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    params = DEFAULT
    report = {"WARNING": "Synthetic data with planted rules; numbers measure recovery of planted structure only."}
    if args.grid:
        g = grid()
        report["param_selection_train_only"] = g
        report["selection_rule"] = SELECTION_RULE
        best = select(g)
        report["selected"] = best
        report["pool_order_comparison_train_only"] = []
        for po in ("level_first", "scope_first"):
            pp = replace(DEFAULT, slot_selection=best["slot_selection"], half_life_sessions=best["half_life_sessions"],
                         decay_clock=best["decay_clock"], fast_half_life_sessions=best["fast_half_life_sessions"],
                         supersede_after_sessions=best["supersede_after_sessions"], pool_order=po)
            report["pool_order_comparison_train_only"].append(
                {"pool_order": po, "train_prequential_acc": round(train_prequential(pp), 4)})
        params = replace(DEFAULT, slot_selection=best["slot_selection"], half_life_sessions=best["half_life_sessions"], decay_clock=best["decay_clock"],
                         fast_half_life_sessions=best["fast_half_life_sessions"],
                         supersede_after_sessions=best["supersede_after_sessions"])
    report["params"] = asdict(params)
    for proto in ("static", "online"):
        rows, excluded, bl = run_heldout(params, proto)
        report[proto] = {
            "overall": score(rows),
            "by_subject": breakdown(rows, lambda r: r["subject"]),
            "by_category": breakdown(rows, lambda r: r["category"]),
            "excluded_null_label": len(excluded),
        }
        if proto == "static":
            report["baselines"] = bl
            report["slices"] = {
                "ava_transition_talking_head_after_jump_cut (drift)": score(
                    [r for r in rows if r["subject"] == "ava" and r["category"] == "transition"
                     and r["context"]["footage_type"] == "talking_head" and r["context"]["prev_edit"] == "jump_cut"]),
                "ava_transition_broll (must not drift)": score(
                    [r for r in rows if r["subject"] == "ava" and r["category"] == "transition"
                     and r["context"]["footage_type"] == "broll"]),
                "ben_caption_9x16_private": score(
                    [r for r in rows if r["subject"] == "ben" and r["category"] == "caption_style"
                     and r["context"]["format"] == "9:16" and r["project_scope"] == "private"]),
            }
            sweep = []
            for tau in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7):
                p2 = replace(params, abstain_below=tau)
                r2, _, _ = run_heldout(p2, "static")
                s = score(r2)
                sweep.append({"abstain_below": tau, "coverage": s["coverage"], "acc_when_answering": s["acc_when_answering"]})
            report["threshold_sweep_static"] = sweep
            with open(RESULTS / "eval_rows_static.jsonl", "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
    rows, _, _ = run_heldout(params, "static", use_statements=False)
    report["ablation_no_chat_statements_static"] = score(rows)
    p_nochange = replace(params, supersede_after_sessions=10**6)
    rows, _, _ = run_heldout(p_nochange, "static")
    report["ablation_no_change_detection_static"] = score(rows)
    report["ablation_no_change_detection_drift_slice"] = score(
        [r for r in rows if r["subject"] == "ava" and r["category"] == "transition"
         and r["context"]["footage_type"] == "talking_head" and r["context"]["prev_edit"] == "jump_cut"])
    p_slowdecay = replace(params, half_life_sessions=8)
    rows, _, _ = run_heldout(p_slowdecay, "static")
    report["ablation_slow_decay_hl8_static"] = score(rows)
    d, tr = drift_trace(params, data.events(None))
    report["drift_full_history"] = d
    d2, tr2 = drift_trace(params, data.events(None), use_statements=False)
    report["drift_full_history_without_statements"] = d2
    (RESULTS / "drift_trace.json").write_text(json.dumps({"with_statements": tr, "without_statements": tr2}, indent=1))
    (RESULTS / "eval.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("params", "static", "online") if k in report}, indent=1)[:6000])
    if "param_selection_train_only" in report:
        for r in report["param_selection_train_only"]:
            print("grid", r)
        print("selected", report["selected"])
    print("drift", report["drift_full_history"], report["drift_full_history_without_statements"])
    print("slices:", json.dumps(report["slices"], indent=1))
    print("sweep:", report["threshold_sweep_static"])
    print("no-statements:", report["ablation_no_chat_statements_static"])
    print("no-change-detection:", report["ablation_no_change_detection_static"], report["ablation_no_change_detection_drift_slice"])
    print("slow-decay-hl8:", report["ablation_slow_decay_hl8_static"])


if __name__ == "__main__":
    main()
