"""Validate the unified world and print descriptive stats. Exit 1 on any error.

Checks: media hashes; per-project store integrity (object/version hashes, parents,
refs, op parent/result versions, op replay == snapshots, review-op links);
timeline schema + invariants; flagship pull expectations; memory events trace
to existing ops/chat; derived files are reproducible from the logs; planted
brand violations really violate; derivation agrees with generator intent."""
import sys
from collections import Counter, defaultdict

import derive_events
from common import DATA, canonical, content_hash, read_json, read_jsonl, sha256_file
from minischema import check
from timeline_ops import apply_op, clip_end

errors = []
PROJECTS = DATA / "vcs" / "projects"
MEM = DATA / "memory"
WORLD = DATA / "world"


def err(msg):
    errors.append(msg)


def validate_media():
    m = read_json(DATA / "media" / "manifest.json")
    total = 0
    for a in m["assets"]:
        p = DATA / "media" / a["path"]
        if not p.exists():
            err(f"media missing: {p}")
            continue
        if sha256_file(p) != a["sha256"]:
            err(f"media hash mismatch: {a['asset_id']}")
        total += p.stat().st_size
    if total > 20e6:
        err(f"media too large: {total} bytes")
    print(f"media: {len(m['assets'])} assets, {total/1e6:.2f} MB")
    return {"sha256:" + a["sha256"]: a for a in m["assets"]}


def validate_timeline(tl, assets, schema, where):
    for e in check(tl, schema):
        err(f"{where}: schema: {e}")
    clip_ids, track_of = set(), {}
    for t in tl["tracks"]:
        prev_end = -1
        for c in sorted(t["clips"], key=lambda c: c["timeline_start"]):
            if c["id"] in clip_ids:
                err(f"{where}: duplicate clip id {c['id']}")
            clip_ids.add(c["id"])
            track_of[c["id"]] = t["id"]
            a = assets.get(c["asset_hash"])
            if not a:
                err(f"{where}: unknown asset {c['asset_hash'][:20]}")
                continue
            if not (0 <= c["src_in"] < c["src_out"] <= a["probed_duration"] + 1e-3):
                err(f"{where}: {c['id']} src range {c['src_in']}-{c['src_out']} outside asset")
            if (a["kind"] == "audio") != (t["kind"] == "audio"):
                err(f"{where}: {c['id']} asset kind {a['kind']} on {t['kind']} track")
            if c["timeline_start"] < prev_end - 1e-6:
                err(f"{where}: overlap on {t['id']} at {c['id']}")
            prev_end = clip_end(c)
    for x in tl["transitions"]:
        if track_of.get(x["from_clip"]) != x["track_id"] or track_of.get(x["to_clip"]) != x["track_id"]:
            err(f"{where}: transition {x['id']} references clips not on {x['track_id']}")
    for x in tl["effects"]:
        if x["target_id"] not in clip_ids:
            err(f"{where}: effect {x['id']} targets missing {x['target_id']}")
    text_tracks = {t["id"] for t in tl["tracks"] if t["kind"] == "text"}
    for x in tl["text_layers"]:
        if x["track_id"] not in text_tracks:
            err(f"{where}: text {x['id']} not on a text track")


def validate_project(pid, assets, schema, sessions, all_ops):
    pdir = PROJECTS / pid
    meta = read_json(pdir / "project.json")
    objs = {}
    for p in (pdir / "store" / "objects").glob("*.json"):
        if sha256_file(p) != p.stem:
            err(f"{pid}: object file bytes do not hash to its name {p.stem[:12]}")
        tl = read_json(p)
        if canonical(tl).encode() != p.read_bytes():
            err(f"{pid}: object {p.stem[:12]} not stored canonically")
        objs[p.stem] = tl
    vers = {p.stem: read_json(p) for p in (pdir / "store" / "versions").glob("*.json")}
    refs = read_json(pdir / "store" / "refs.json")["branches"]
    ops = read_jsonl(pdir / "ops.jsonl")
    by_id = {o["op_id"]: o for o in ops}
    pos = {o["op_id"]: i for i, o in enumerate(ops)}
    for h, tl in objs.items():
        validate_timeline(tl, assets, schema, f"{pid}:{h[:10]}")
        if tl["project_id"] != pid:
            err(f"{pid}: snapshot {h[:10]} has project_id {tl['project_id']}")
    for vid, v in vers.items():
        if content_hash({k: v[k] for k in v if k != "version_id"}) != vid:
            err(f"{pid}: version id mismatch {vid[:12]}")
        if v["project_id"] != pid or v["timeline_hash"] not in objs:
            err(f"{pid}: version {vid[:12]} bad project or missing object")
        if v["session_id"] not in sessions:
            err(f"{pid}: version {vid[:12]} unknown session")
        for p in v["parents"]:
            if p not in vers:
                err(f"{pid}: version {vid[:12]} parent not in this project's store")
        if v["parents"]:
            tl = objs[vers[v["parents"][0]]["timeline_hash"]]
            for oid in v["op_ids"]:
                op = by_id.get(oid)
                if op is None:
                    err(f"{pid}: version lists unknown op {oid}")
                    continue
                if op["parent_version"] != v["parents"][0] or op["result_version"] != vid:
                    err(f"{oid}: parent/result version mismatch")
                tl = apply_op(tl, op)
            if content_hash(tl) != v["timeline_hash"]:
                err(f"{pid}: replay mismatch for version {vid[:12]}")
    if {"main", "master"} & set(refs):
        err(f"{pid}: has a main branch")
    for b, r in refs.items():
        if r["head"] not in vers or (r["created_from"] and r["created_from"] not in vers):
            err(f"{pid}: ref {b} does not resolve")
    for o in ops:
        if o["project_id"] != pid:
            err(f"{o['op_id']}: wrong project")
        for k in ("parent_version", "result_version"):
            if o[k] not in vers:
                err(f"{o['op_id']}: {k} not in project store")
        if o["session_id"] not in sessions or sessions[o["session_id"]]["project_id"] != pid:
            err(f"{o['op_id']}: session not in this project")
        if o["author"] == "director" and not o["on_behalf_of"]:
            err(f"{o['op_id']}: director op without on_behalf_of")
        if set(o["context"]) != {"footage_type", "prev_edit", "format", "section"}:
            err(f"{o['op_id']}: context keys")
        for link in ("replaces_op", "reverts_op", "accepts_op"):
            tgt = o[link]
            if tgt:
                if tgt not in by_id or pos[tgt] >= pos[o["op_id"]]:
                    err(f"{o['op_id']}: {link} -> {tgt} missing or not earlier")
                elif by_id[tgt]["author"] != "director":
                    err(f"{o['op_id']}: {link} target is not a Director op")
        if o["type"] == "accept" and not o["accepts_op"]:
            err(f"{o['op_id']}: accept without accepts_op")
        if o["type"] == "revert" and not o["reverts_op"]:
            err(f"{o['op_id']}: revert without reverts_op")
        all_ops[o["op_id"]] = o
    return meta, vers, refs, ops, objs


def validate_flagship(pid, assets, schema, refs):
    pdir = PROJECTS / pid
    sc = read_json(pdir / "scenario.json")
    for p in sc["selective_pull_expectations"]:
        if p["expected"] == "clean":
            tl = read_json(pdir / p["result_file"])
            if content_hash(tl) != p["result_timeline_hash"]:
                err(f"flagship expected pull hash mismatch {p['result_file']}")
            validate_timeline(tl, assets, schema, p["result_file"])
    want = {("cara/titles-broll", "ava/tighter-cut"): "clean", ("ben/alt-pacing", "ava/tighter-cut"): "conflict",
            ("cara/titles-broll", "ava/alt-ending"): "clean", ("ben/alt-pacing", "cara/titles-broll"): "clean"}
    for p in sc["selective_pull_expectations"]:
        if want.get((p["from"], p["into"])) != p["expected"]:
            err(f"flagship pull {p['from']}->{p['into']} expected {want.get((p['from'], p['into']))}")
    d_from = refs["ava/alt-ending"]["created_from"]
    if d_from == refs["rough-cut"]["head"]:
        err("flagship D should branch from A, not the base")
    return sc


def main():
    assets = validate_media()
    schema = read_json(DATA / "vcs" / "timeline.schema.json")
    sessions = {s["session_id"]: s for s in read_jsonl(WORLD / "sessions.jsonl")}
    index = read_json(PROJECTS / "index.json")["projects"]
    on_disk = {p.name for p in PROJECTS.iterdir() if p.is_dir()}
    if on_disk != {p["project_id"] for p in index}:
        err("projects/index.json does not match project directories")
    all_ops, tot = {}, Counter()
    sc = None
    for p in index:
        meta, vers, refs, ops, objs = validate_project(p["project_id"], assets, schema, sessions, all_ops)
        tot["versions"] += len(vers)
        tot["snapshots"] += len(objs)
        tot["branches"] += len(refs)
        if p["flagship"]:
            sc = validate_flagship(p["project_id"], assets, schema, refs)
    op_types = Counter(o["type"] for o in all_ops.values())
    print(f"vcs: {len(index)} projects, {tot['versions']} versions, {tot['snapshots']} snapshots, "
          f"{tot['branches']} branches, {len(all_ops)} ops")
    print(f"     op types: {dict(op_types.most_common())}")
    print(f"     op authors: {dict(Counter(o['author'] for o in all_ops.values()))}")
    if sc:
        print(f"     flagship {sc['project_id']}: edit types {len(sc['edit_types_used'])}")
        for p in sc["selective_pull_expectations"]:
            print(f"        pull {p['from']} -> {p['into']}: {p['expected']}"
                  + (f" {[(c['ours'].split('/')[-1], c['theirs'].split('/')[-1], [r['kind'] for r in c['reasons']]) for c in p['conflicts']]}"
                     if p["conflicts"] else ""))

    # ---------------------------------------------------- memory: derived + traceable
    vocab = read_json(WORLD / "vocab.json")
    brand = read_json(WORLD / "brand_guidelines.json")
    gt = read_json(MEM / "ground_truth.json")
    chat = {c["chat_id"]: c for c in read_jsonl(WORLD / "chat.jsonl")}
    events = read_jsonl(MEM / "events.jsonl")
    decisions = read_jsonl(MEM / "decisions_labeled.jsonl")
    ev2, dec2, _ = derive_events.derive()
    if ev2 != events or dec2 != decisions:
        err("memory files are stale: re-run derive_events.py")
    for e in events:
        if e["type"] == "explicit_statement":
            c = chat.get(e["chat_id"])
            if not c or c["text"] != e["text"] or e["op_id"]:
                err(f"{e['event_id']}: does not trace to chat")
            continue
        o = all_ops.get(e["op_id"])
        if not o:
            err(f"{e['event_id']}: op {e['op_id']} does not exist")
            continue
        if o["result_version"] != e["version_id"] or o["session_id"] != e["session_id"]:
            err(f"{e['event_id']}: version/session disagree with op")
        if e["type"] not in vocab["event_types"]:
            err(f"{e['event_id']}: bad type")
        for k, v in e["context"].items():
            if v not in vocab["context_values"][k]:
                err(f"{e['event_id']}: bad context {k}={v}")
        if e["action"] is not None and e["action"] not in vocab["actions"][e["category"]]:
            err(f"{e['event_id']}: action {e['action']} not in vocab")
        if e["type"] in ("user_override", "undo") and not e["replaced_action"]:
            err(f"{e['event_id']}: {e['type']} without replaced_action")
        if e["session_id"] not in sessions:
            err(f"{e['event_id']}: unknown session")
    ev_ids = {e["event_id"] for e in events}
    parts = [{e["event_id"] for e in read_jsonl(MEM / "splits" / f"events_{n}.jsonl")}
             for n in ("train", "heldout", "flagship")]
    if sum(map(len, parts)) != len(ev_ids) or set().union(*parts) != ev_ids:
        err("splits are not a partition of events")
    per = Counter(s["subject"] for s in sessions.values() if s["split"] != "flagship")
    for subj, n in per.items():
        if not 30 <= n <= 40:
            err(f"{subj}: {n} generated sessions")
        tr = [s["started_at"] for s in sessions.values() if s["subject"] == subj and s["split"] == "train"]
        ho = [s["started_at"] for s in sessions.values() if s["subject"] == subj and s["split"] == "heldout"]
        if max(tr) >= min(ho):
            err(f"{subj}: held-out not strictly later than train")
    # generator intent vs derivation (only accept-then-undo may differ: final None)
    labels = {l["decision_id"]: l for l in read_jsonl(WORLD / "planted_labels.jsonl")}
    mism = [d for d in decisions if d["decision_id"] in labels
            and d["final_action"] != labels[d["decision_id"]]["intended_user_action"]]
    if any(d["final_action"] is not None for d in mism):
        err(f"{sum(d['final_action'] is not None for d in mism)} decisions derive a different final_action than intended")
    if set(labels) - {d["decision_id"] for d in decisions}:
        err("some planted decisions produced no events")
    # brand violations
    hc = brand["hard_constraints"]
    banned = {"font": set(hc["fonts"]["banned"]), "transition": set(hc["transitions"]["banned"]),
              "music_mood": set(hc["music_mood"]["banned"])}
    ev_by_op = defaultdict(list)
    for e in events:
        ev_by_op[e["op_id"]].append(e)
    for v in gt["brand_violations_planted"]:
        es = ev_by_op.get(v["op_id"])
        if not es:
            err(f"planted violation {v['op_id']} has no event")
            continue
        e = es[0]
        bad = e["action"] in banned.get(e["category"], set()) or (
            e["category"] == "font" and e["action"] not in hc["fonts"]["allowed"])
        if not bad or e["brand_id"] != brand["brand_id"]:
            err(f"planted violation {v['op_id']} does not violate the brand")

    # ---------------------------------------------------- stats
    print(f"memory: {len(sessions)} sessions ({dict(per)} generated + "
          f"{sum(s['split'] == 'flagship' for s in sessions.values())} flagship), "
          f"{len(events)} events, {len(decisions)} decisions")
    print(f"     events by type: {dict(Counter(e['type'] for e in events))}")
    print(f"     events by subject: {dict(Counter(e['subject'] for e in events))}")
    print(f"     decisions by category: {dict(Counter(d['category'] for d in decisions))}")
    print(f"     non-event ops (no preference category): "
          f"{sum(1 for o in all_ops.values() if o['op_id'] not in ev_by_op)}")
    gen = [d for d in decisions if d["split"] != "flagship"]
    print(f"     Director proposal kept by user (generic prior, by construction): "
          f"{sum(d['agent_action'] == d['final_action'] for d in gen) / len(gen):.0%}")
    print(f"     splits: train {len(parts[0])} / heldout {len(parts[1])} / flagship {len(parts[2])} events; "
          f"heldout decisions {sum(d['split'] == 'heldout' for d in decisions)}")
    print(f"     planted brand violations: {len(gt['brand_violations_planted'])} "
          f"{[(v['session_id'], v['who'], v['action']) for v in gt['brand_violations_planted']]}")
    dr = gt["drift"]
    win = defaultdict(Counter)
    for d in gen:
        if d["subject"] == "ava" and d["category"] == "transition" and \
                d["context"]["footage_type"] == "talking_head" and d["context"]["prev_edit"] == "jump_cut":
            n = d["session_no"]
            w = "before" if n < dr["drift_session_no"] else (
                "ramp" if n < dr["drift_session_no"] + dr["ramp_sessions"] else "after")
            win[w][d["final_action"]] += 1
    print(f"     drift (ava, talking_head+jump_cut transitions, final_action; drift at session {dr['drift_session_no']}):")
    for w in ("before", "ramp", "after"):
        print(f"        {w:<6} {dict(win[w].most_common(4))}")
    br = Counter(d["final_action"] for d in gen if d["subject"] == "ava" and d["category"] == "transition"
                 and d["context"]["footage_type"] == "broll" and d["session_no"] >= dr["drift_session_no"])
    print(f"        control: ava broll transitions after drift {dict(br.most_common(3))}")
    cf = defaultdict(Counter)
    for d in gen:
        if d["category"] == "caption_style" and d["context"]["format"] == "9:16" and d["actor"] == "ben":
            cf[f"{d['subject']}/{d['project_scope']}"][d["final_action"]] += 1
    print(f"     conflict (ben as actor, 9:16 caption_style final_action by subject/scope): "
          f"{ {k: dict(v) for k, v in cf.items()} }")
    fl = [(d["category"], d["final_action"], d["actor"]) for d in decisions if d["split"] == "flagship"]
    print(f"     flagship decisions (user_edit): {fl}")
    if errors:
        print(f"\nFAILED with {len(errors)} errors:")
        for e in errors[:50]:
            print("  -", e)
        sys.exit(1)
    print("\nOK: all checks passed")


if __name__ == "__main__":
    main()
