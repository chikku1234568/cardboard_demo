"""Pure functions that apply edit ops to a timeline dict.

Semantics (kept deliberately simple for the demo):
- All times are absolute timeline seconds (no implicit ripple). Ripple is a
  separate, recomputed concern; trimming a clip can leave a gap.
- Every op records `before` and `after` so diffs and conflict checks need no
  replay.
- split_clip removes the original clip and creates two new clips whose
  lineage.parent points at the original. Transitions/effects are rewired.
- Review ops: `accept` is a no-op marker (top-level `accepts_op`); `revert`
  applies `payload.inverse` (top-level `reverts_op`). An override is an ordinary
  op with a top-level `replaces_op`.
"""
import copy

from common import r3


class OpError(Exception):
    pass


def find_clip(tl, clip_id):
    for t in tl["tracks"]:
        for c in t["clips"]:
            if c["id"] == clip_id:
                return t, c
    raise OpError(f"clip not found: {clip_id}")


def find_track(tl, track_id):
    for t in tl["tracks"]:
        if t["id"] == track_id:
            return t
    raise OpError(f"track not found: {track_id}")


def find_by_id(items, item_id, what):
    for x in items:
        if x["id"] == item_id:
            return x
    raise OpError(f"{what} not found: {item_id}")


def clip_end(c):
    return r3(c["timeline_start"] + (c["src_out"] - c["src_in"]) / c.get("speed", 1.0))


def sort_track(t):
    t["clips"].sort(key=lambda c: (c["timeline_start"], c["id"]))


def check_before(actual: dict, expected: dict, ctx):
    for k, v in expected.items():
        if actual.get(k) != v:
            raise OpError(f"{ctx}: precondition failed on {k}: have {actual.get(k)!r}, op expects {v!r}")


def apply_op(tl, op):
    tl = copy.deepcopy(tl)
    t = op["type"]
    p = op["payload"]
    if t == "add_track":
        if any(x["id"] == p["track"]["id"] for x in tl["tracks"]):
            raise OpError("track exists")
        tl["tracks"].append(copy.deepcopy(p["track"]))
    elif t == "add_clip":
        tr = find_track(tl, p["track_id"])
        tr["clips"].append(copy.deepcopy(p["clip"]))
        sort_track(tr)
    elif t == "delete_clip":
        tr, c = find_clip(tl, p["clip_id"])
        check_before(c, p["before"], t)
        tr["clips"] = [x for x in tr["clips"] if x["id"] != p["clip_id"]]
        tl["transitions"] = [x for x in tl["transitions"]
                             if p["clip_id"] not in (x["from_clip"], x["to_clip"])]
        tl["effects"] = [x for x in tl["effects"] if x["target_id"] != p["clip_id"]]
    elif t in ("trim_clip", "set_volume", "set_speed"):
        tr, c = find_clip(tl, p["clip_id"])
        check_before(c, p["before"], t)
        c.update(p["after"])
        sort_track(tr)
    elif t == "move_clip":
        tr, c = find_clip(tl, p["clip_id"])
        check_before({**c, "track_id": tr["id"]}, p["before"], t)
        new_tr = find_track(tl, p["after"].get("track_id", tr["id"]))
        c["timeline_start"] = p["after"]["timeline_start"]
        if new_tr is not tr:
            tr["clips"].remove(c)
            new_tr["clips"].append(c)
        sort_track(new_tr)
    elif t == "split_clip":
        tr, c = find_clip(tl, p["clip_id"])
        at = p["at"]
        if not (c["timeline_start"] < at < clip_end(c)):
            raise OpError("split point outside clip")
        offset = r3((at - c["timeline_start"]) * c.get("speed", 1.0))
        left_id, right_id = p["new_ids"]
        left = copy.deepcopy(c)
        right = copy.deepcopy(c)
        left.update(id=left_id, src_out=r3(c["src_in"] + offset))
        right.update(id=right_id, src_in=r3(c["src_in"] + offset), timeline_start=r3(at))
        for piece in (left, right):
            piece["lineage"] = {"origin": c["lineage"]["origin"], "parent": c["id"],
                                "created_by_op": op["op_id"]}
        idx = tr["clips"].index(c)
        tr["clips"][idx:idx + 1] = [left, right]
        for x in tl["transitions"]:
            if x["to_clip"] == c["id"]:
                x["to_clip"] = left_id
            if x["from_clip"] == c["id"]:
                x["from_clip"] = right_id
        new_fx = []
        for fx in tl["effects"]:
            if fx["target_id"] == c["id"]:
                fx["target_id"] = left_id
                dup = copy.deepcopy(fx)
                dup["id"] = fx["id"] + "_r"
                dup["target_id"] = right_id
                new_fx.append(dup)
        tl["effects"].extend(new_fx)
    elif t in ("change_transition",):
        x = find_by_id(tl["transitions"], p["transition_id"], "transition")
        check_before(x, p["before"], t)
        x.update(p["after"])
    elif t == "add_transition":
        tl["transitions"].append(copy.deepcopy(p["transition"]))
    elif t == "remove_transition":
        x = find_by_id(tl["transitions"], p["transition_id"], "transition")
        tl["transitions"].remove(x)
    elif t == "add_text":
        tl["text_layers"].append(copy.deepcopy(p["text_layer"]))
    elif t == "edit_text":
        x = find_by_id(tl["text_layers"], p["text_layer_id"], "text layer")
        check_before(x, p["before"], t)
        x.update(copy.deepcopy(p["after"]))
    elif t == "add_effect":
        tl["effects"].append(copy.deepcopy(p["effect"]))
    elif t == "update_effect":
        x = find_by_id(tl["effects"], p["effect_id"], "effect")
        check_before(x["params"], p["before"]["params"], t)
        x["params"].update(p["after"]["params"])
    elif t == "remove_effect":
        x = find_by_id(tl["effects"], p["effect_id"], "effect")
        tl["effects"].remove(x)
    elif t == "remove_text":
        x = find_by_id(tl["text_layers"], p["text_layer_id"], "text layer")
        tl["text_layers"].remove(x)
    elif t == "set_text_style":
        x = find_by_id(tl["text_layers"], p["text_layer_id"], "text layer")
        check_before(x["style"], p["before"], t)
        x["style"].update(p["after"])
    elif t == "replace_music":
        tr, c = find_clip(tl, p["clip_id"])
        check_before(c, p["before"], t)
        c.update(p["after"])
    elif t == "set_format":
        check_before(tl["settings"], p["before"], t)
        tl["settings"].update(p["after"])
    elif t == "accept":
        pass  # marker: user accepted op["accepts_op"]; timeline unchanged
    elif t == "revert":
        inv = p["inverse"]
        return apply_op(tl, {"op_id": op["op_id"], "type": inv["type"], "payload": inv["payload"]})
    else:
        raise OpError(f"unknown op type {t}")
    tl["transitions"].sort(key=lambda x: x["id"])
    tl["text_layers"].sort(key=lambda x: x["id"])
    tl["effects"].sort(key=lambda x: x["id"])
    return tl


OP_TYPES = ["add_track", "add_clip", "delete_clip", "trim_clip", "move_clip", "split_clip",
            "set_volume", "set_speed", "add_transition", "change_transition", "remove_transition",
            "add_text", "edit_text", "remove_text", "set_text_style", "add_effect", "update_effect",
            "remove_effect", "replace_music", "set_format", "accept", "revert"]
