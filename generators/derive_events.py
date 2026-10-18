"""Derive memory events from the VCS op logs and the chat log.

This is the ONLY producer of data/memory/events.jsonl, decisions_labeled.jsonl
and splits/. The inputs are:
  data/vcs/projects/*/ops.jsonl + project.json   (every edit, incl. review ops)
  data/world/chat.jsonl                           (explicit statements)
  data/world/sessions.jsonl                       (session -> subject/actor/split)
  data/world/planted_labels.jsonl                 (ONLY to annotate decisions_labeled; never used for events)

Mapping op -> event:
  op by 'director' with a preference category  -> agent_edit
  `accept` (accepts_op)                          -> user_accept   (action = accepted op's action)
  user op with replaces_op                       -> user_override (replaced_action = replaced op's action)
  `revert` (reverts_op)                          -> undo          (replaced_action = reverted op's action)
  other user op with a preference category       -> user_edit     (no agent proposal, e.g. the flagship demo)
  chat message                                   -> explicit_statement
Ops with no preference category (move, split, delete, volume, text copy, format) produce no event.
"""
from common import DATA, read_json, read_jsonl, write_jsonl

PROJECTS = DATA / "vcs" / "projects"
MEM = DATA / "memory"
CUT_EDGES = (1.5, 4.0)


def bucket(dur):
    return "short" if dur < CUT_EDGES[0] else ("medium" if dur <= CUT_EDGES[1] else "long")


def category_action(op, ops_by_id):
    """(category, action) an op expresses, or (None, None)."""
    t, p = op["type"], op["payload"]
    if t in ("accept", "revert"):
        target = ops_by_id[op["accepts_op"] or op["reverts_op"]]
        cat, act = category_action(target, ops_by_id)
        return cat, (act if t == "accept" else None)
    if t == "add_transition":
        return "transition", p["transition"]["type"]
    if t == "change_transition" and "type" in p["after"]:
        return "transition", p["after"]["type"]
    if t == "add_text":
        x = p["text_layer"]
        if x.get("role") == "captions":
            return "caption_style", x["style"].get("preset")
        return "font", x["style"]["font"]
    if t == "set_text_style":
        if p["role"] == "captions" and "preset" in p["after"]:
            return "caption_style", p["after"]["preset"]
        if "font" in p["after"]:
            return "font", p["after"]["font"]
    if t == "add_effect" and p["effect"]["type"] == "color_grade":
        return "color_grade", p["effect"]["params"]["look"]
    if t == "update_effect" and "look" in p["after"]["params"]:
        return "color_grade", p["after"]["params"]["look"]
    if t == "trim_clip":
        b, a = p["before"], {**p["before"], **p["after"]}
        return "cut_length", bucket(a["src_out"] - a["src_in"])
    if t == "add_clip" and p["clip"].get("music_mood"):
        return "music_mood", p["clip"]["music_mood"]
    if t == "replace_music":
        return "music_mood", p["after"]["music_mood"]
    return None, None


def derive():
    sessions = {s["session_id"]: s for s in read_jsonl(DATA / "world" / "sessions.jsonl")}
    index = read_json(PROJECTS / "index.json")["projects"]
    rows = []
    for pinfo in index:
        pid = pinfo["project_id"]
        meta = read_json(PROJECTS / pid / "project.json")
        ops = read_jsonl(PROJECTS / pid / "ops.jsonl")
        by_id = {o["op_id"]: o for o in ops}
        for seq, op in enumerate(ops):
            cat, act = category_action(op, by_id)
            if cat is None:
                continue
            s = sessions[op["session_id"]]
            if op["type"] == "accept":
                typ, replaced = "user_accept", None
            elif op["type"] == "revert":
                typ, replaced = "undo", category_action(by_id[op["reverts_op"]], by_id)[1]
            elif op["author"] == "director":
                typ, replaced = "agent_edit", None
            elif op["replaces_op"]:
                typ, replaced = "user_override", category_action(by_id[op["replaces_op"]], by_id)[1]
            else:
                typ, replaced = "user_edit", None
            rows.append(((op["timestamp"], pid, seq), {
                "ts": op["timestamp"], "session_id": op["session_id"], "subject": meta["owner_subject"],
                "actor": s["actor"] if op["author"] == "director" else op["author"],
                "project_id": pid, "branch": op["branch"], "project_scope": meta["project_scope"],
                "brand_id": meta["brand_id"], "type": typ, "category": cat, "context": op["context"],
                "action": act, "replaced_action": replaced,
                "decision_id": op["decision_id"] or f"dec:{op['op_id']}",
                "source": "director" if op["author"] == "director" else "user", "text": None,
                "op_id": op["op_id"], "version_id": op["result_version"], "chat_id": None}))
    for c in read_jsonl(DATA / "world" / "chat.jsonl"):
        s = sessions[c["session_id"]]
        rows.append(((c["ts"], c["project_id"], -1), {
            "ts": c["ts"], "session_id": c["session_id"], "subject": s["subject"], "actor": c["actor"],
            "project_id": c["project_id"], "branch": c["branch"], "project_scope": s["project_scope"],
            "brand_id": s["brand_id"], "type": "explicit_statement", "category": None, "context": None,
            "action": None, "replaced_action": None, "decision_id": None, "source": "chat", "text": c["text"],
            "op_id": None, "version_id": None, "chat_id": c["chat_id"]}))
    rows.sort(key=lambda r: r[0])
    events = [{"event_id": f"ev_{i + 1:05d}", **r} for i, (_, r) in enumerate(rows)]

    # decisions: fold each decision's events in order
    labels = {l["decision_id"]: l for l in read_jsonl(DATA / "world" / "planted_labels.jsonl")}
    decs = {}
    for e in events:
        if not e["decision_id"]:
            continue
        d = decs.get(e["decision_id"])
        if d is None:
            s = sessions[e["session_id"]]
            lab = labels.get(e["decision_id"], {"planted_rule": "flagship_scripted", "generated_by": "flagship"})
            d = decs[e["decision_id"]] = {
                "decision_id": e["decision_id"], "session_id": e["session_id"], "subject": e["subject"],
                "actor": e["actor"], "session_no": s["session_no"], "ts": e["ts"], "project_id": e["project_id"],
                "branch": e["branch"], "project_scope": e["project_scope"], "brand_id": e["brand_id"],
                "category": e["category"], "context": e["context"], "agent_action": None, "final_action": None,
                "event_ids": [], "op_ids": [], "planted_rule": lab["planted_rule"],
                "generated_by": lab["generated_by"], "split": s["split"]}
        d["event_ids"].append(e["event_id"])
        d["op_ids"].append(e["op_id"])
        d["ts"] = e["ts"]
        if e["type"] == "agent_edit":
            d["agent_action"] = d["final_action"] = e["action"]
        elif e["type"] in ("user_accept", "user_override", "user_edit"):
            d["final_action"] = e["action"]
        elif e["type"] == "undo":
            d["final_action"] = None
    return events, list(decs.values()), sessions


def main():
    events, decisions, sessions = derive()
    write_jsonl(MEM / "events.jsonl", events)
    write_jsonl(MEM / "decisions_labeled.jsonl", decisions)
    split_of = {sid: s["split"] for sid, s in sessions.items()}
    for name in ("train", "heldout", "flagship"):
        write_jsonl(MEM / "splits" / f"events_{name}.jsonl", [e for e in events if split_of[e["session_id"]] == name])
    write_jsonl(MEM / "splits" / "decisions_heldout.jsonl",
                [{k: v for k, v in d.items() if k not in ("planted_rule", "generated_by")}
                 for d in decisions if d["split"] == "heldout"])
    print(f"[derive] events={len(events)} decisions={len(decisions)}")


if __name__ == "__main__":
    main()
