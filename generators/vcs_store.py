"""Per-project content-addressed store, op construction, touches and the
selective-pull conflict rule. Shared by the flagship scenario and the world."""
import shutil

from common import canonical, content_hash, r3, write_json, write_jsonl
from timeline_ops import apply_op, clip_end, find_by_id, find_clip


def touches(tl, op):
    """Entities and (track, time range) an op touches, computed against the
    timeline it applies to. Stored on the op so conflict checks need no replay."""
    p, t = op["payload"], op["type"]
    if t == "revert":
        return touches(tl, {"type": p["inverse"]["type"], "payload": p["inverse"]["payload"]})
    ents, ranges = [], []

    def clip_range(tr_id, c):
        ranges.append({"track_id": tr_id, "start": c["timeline_start"], "end": clip_end(c)})

    if t in ("trim_clip", "set_volume", "set_speed", "delete_clip", "move_clip", "split_clip", "replace_music"):
        tr, c = find_clip(tl, p["clip_id"])
        ents += [f"clip:{c['id']}", f"lineage:{c['lineage']['origin']}"]
        if t == "trim_clip":
            after = {**c, **p["after"]}
            if p["edge"] == "out":
                a, b = clip_end(c), clip_end(after)
            else:
                a, b = c["timeline_start"], after["timeline_start"]
            ranges.append({"track_id": tr["id"], "start": min(a, b), "end": max(a, b)})
        else:
            clip_range(tr["id"], c)
        if t in ("set_volume", "set_speed", "move_clip"):
            after = {**c, **{k: v for k, v in p["after"].items() if k != "track_id"}}
            clip_range(p["after"].get("track_id", tr["id"]), after)
    elif t == "add_clip":
        c = p["clip"]
        ents += [f"clip:{c['id']}", f"lineage:{c['lineage']['origin']}"]
        clip_range(p["track_id"], c)
    elif t in ("change_transition", "remove_transition", "add_transition"):
        x = p["transition"] if t == "add_transition" else find_by_id(tl["transitions"], p["transition_id"], "tr")
        ents.append(f"transition:{x['id']}")
        _, a = find_clip(tl, x["from_clip"])
        cut = clip_end(a)
        d = max(x["duration"], p.get("after", {}).get("duration", 0))
        ranges.append({"track_id": x["track_id"], "start": r3(cut - d / 2), "end": r3(cut + d / 2)})
    elif t in ("add_text", "edit_text", "remove_text", "set_text_style"):
        x = p["text_layer"] if t == "add_text" else find_by_id(tl["text_layers"], p["text_layer_id"], "txt")
        ents.append(f"text:{x['id']}")
        after = {**x, **p.get("after", {})} if t == "edit_text" else x
        for y in (x, after):
            ranges.append({"track_id": y["track_id"], "start": y["start"], "end": r3(y["start"] + y["duration"])})
    elif t in ("add_effect", "update_effect", "remove_effect"):
        x = p["effect"] if t == "add_effect" else find_by_id(tl["effects"], p["effect_id"], "fx")
        ents.append(f"effect:{x['id']}")
        try:
            tr, c = find_clip(tl, x["target_id"])
            ents.append(f"lineage:{c['lineage']['origin']}")
            clip_range(tr["id"], c)
        except Exception:
            pass
    elif t == "add_track":
        ents.append(f"track:{p['track']['id']}")
    elif t == "set_format":
        ents.append("settings:aspect")
    uniq = []
    for r in ranges:
        if r not in uniq:
            uniq.append(r)
    return {"entities": sorted(set(ents)), "ranges": uniq}


def ops_conflict(a, b):
    """Selective-pull conflict rule: same entity with different results, or
    overlapping time ranges on the same track."""
    reasons = []
    shared = sorted(set(a["touches"]["entities"]) & set(b["touches"]["entities"]))
    if shared and a["payload"].get("after") != b["payload"].get("after"):
        reasons.append({"kind": "same_entity", "entities": shared})
    for ra in a["touches"]["ranges"]:
        for rb in b["touches"]["ranges"]:
            if ra["track_id"] == rb["track_id"]:
                lo, hi = max(ra["start"], rb["start"]), min(ra["end"], rb["end"])
                r = {"kind": "overlapping_time_range", "track_id": ra["track_id"], "overlap": [r3(lo), r3(hi)]}
                if hi - lo > 1e-6 and r not in reasons:
                    reasons.append(r)
    return reasons


OP_KEYS = ["op_id", "project_id", "session_id", "branch", "parent_version", "result_version", "author",
           "on_behalf_of", "timestamp", "type", "decision_id", "context", "replaces_op", "reverts_op",
           "accepts_op", "payload", "touches"]


class ProjectStore:
    """Append-only, content-addressed store for ONE project. No main branch."""

    def __init__(self, project_id):
        self.project_id = project_id
        self.objects, self.versions, self.refs, self.ops = {}, {}, {}, []
        self.n_ops = 0

    def put_timeline(self, tl):
        h = content_hash(tl)
        self.objects[h] = tl
        return h

    def commit(self, tl, parents, branch, author, ts, message, op_ids, session_id):
        th = self.put_timeline(tl)
        meta = {"project_id": self.project_id, "timeline_hash": th, "parents": parents, "branch": branch,
                "author": author, "timestamp": ts, "message": message, "op_ids": op_ids,
                "session_id": session_id}
        vid = content_hash(meta)
        self.versions[vid] = {"version_id": vid, **meta}
        return vid

    def timeline(self, vid):
        return self.objects[self.versions[vid]["timeline_hash"]]

    def new_branch(self, name, from_version, author, ts):
        assert name not in self.refs, name
        self.refs[name] = {"head": from_version, "created_from": from_version, "created_by": author,
                           "created_at": ts}

    def make_op(self, tl, typ, payload, *, author, ts, branch, session_id, context=None, decision_id=None,
                on_behalf_of=None, replaces_op=None, reverts_op=None, accepts_op=None):
        self.n_ops += 1
        op = {"op_id": f"{self.project_id}/op_{self.n_ops:04d}", "project_id": self.project_id,
              "session_id": session_id, "branch": branch, "parent_version": self.refs[branch]["head"],
              "result_version": None, "author": author, "on_behalf_of": on_behalf_of, "timestamp": ts,
              "type": typ, "decision_id": decision_id,
              "context": {k: (context or {}).get(k) for k in ("footage_type", "prev_edit", "format", "section")},
              "replaces_op": replaces_op, "reverts_op": reverts_op, "accepts_op": accepts_op,
              "payload": payload}
        op["touches"] = touches(tl, op)
        return {k: op[k] for k in OP_KEYS}

    def run_commit(self, branch, author, ts, message, session_id, op_specs):
        """op_specs: list of dicts with keys type, payload and optional op fields
        (author/ts override per op allowed). Applies sequentially; one commit."""
        head = self.refs[branch]["head"]
        tl = self.timeline(head)
        ops = []
        for spec in op_specs:
            spec = dict(spec)
            typ, payload = spec.pop("type"), spec.pop("payload")
            op = self.make_op(tl, typ, payload, author=spec.pop("author", author), ts=spec.pop("ts", ts),
                              branch=branch, session_id=session_id, **spec)
            tl = apply_op(tl, op)
            ops.append(op)
        vid = self.commit(tl, [head], branch, author, ts, message, [o["op_id"] for o in ops], session_id)
        for o in ops:
            o["result_version"] = vid
        self.ops += ops
        self.refs[branch]["head"] = vid
        return vid, ops

    def write(self, pdir, extra_project_meta):
        for sub in ("store", ):
            if (pdir / sub).exists():
                shutil.rmtree(pdir / sub)
        objdir = pdir / "store" / "objects"
        objdir.mkdir(parents=True, exist_ok=True)
        for h, tl in self.objects.items():
            # canonical bytes on disk, so sha256(file) == filename
            (objdir / f"{h}.json").write_text(canonical(tl), encoding="utf-8")
        for vid, v in self.versions.items():
            write_json(pdir / "store" / "versions" / f"{vid}.json", v)
        write_json(pdir / "store" / "refs.json", {"project_id": self.project_id,
                                                   "note": "Branch refs. No main: every branch is a peer.",
                                                   "branches": self.refs})
        write_jsonl(pdir / "ops.jsonl", self.ops)
        write_json(pdir / "project.json", {"project_id": self.project_id, **extra_project_meta,
                                           "n_versions": len(self.versions), "n_ops": len(self.ops),
                                           "branches": sorted(self.refs)})
