"""Generate the unified world: one cast, one team/brand, many VCS projects.

Every edit, by a user or by the Director agent acting for a user, is a VCS op
in a project's op log. Review actions are ops as well: an `accept` marker, an
override op (`replaces_op`), or a `revert` (`reverts_op`). Chat statements go
to a separate chat log. Memory events are NOT generated here. derive_events.py
derives them from these logs.

Outputs:
  data/world/{cast,brand_guidelines,vocab}.json, sessions.jsonl, chat.jsonl,
  planted_labels.jsonl (generator-only labels, never memory input)
  data/vcs/projects/<project_id>/{project.json, ops.jsonl, store/...}
  data/vcs/projects/index.json
  data/memory/ground_truth.json
"""
import random
import shutil
from datetime import datetime, timedelta

import flagship
from common import DATA, r3, read_json, write_json, write_jsonl
from world_spec import (ACTIONS, AGENT_PRIOR, BRAND, BRAND_VIOLATIONS, CAST, CONTEXT_KEYS, CUT_SECONDS,
                        DRIFT_SESSION, FORCED_BRAND, HELDOUT_LAST_N, IST, NOISE, RULES, SESSIONS_PER_PROJECT,
                        STATEMENTS, SUBJECTS, VOCAB, applicable_rule)
from vcs_store import ProjectStore

SEED = 8
rng = random.Random(SEED)
WORLD = DATA / "world"
PROJECTS = DATA / "vcs" / "projects"
EVENT_TYPES = ["agent_edit", "user_accept", "user_override", "undo", "user_edit", "explicit_statement"]
TURN_SIZE = 5            # decisions per Director turn (one agent commit + one review commit)
BANNED_ANYWHERE = ("glitch", "Comic Sans MS", "dramatic_horror")
# sampling ranges strictly inside each cut-length bucket (bucket edges: 1.5s, 4.0s)
CUT_SAMPLE = {"short": (0.6, 1.4), "medium": (1.6, 3.9), "long": (4.1, 7.5)}
FOOTAGE_ASSETS = {"talking_head": ["a_cam", "b_cam"], "broll": ["broll_city", "broll_desk"],
                  "product": ["broll_product"], "screen_recording": ["broll_screen"]}
MUSIC_ASSET = {"upbeat": "music_upbeat", "corporate": "music_upbeat"}   # others -> music_calm (only 2 test beds)
SECTIONS = ["hook", "body", "body", "body", "body", "body", "outro"]

manifest = read_json(DATA / "media" / "manifest.json")
ASSETS = {a["asset_id"]: a for a in manifest["assets"]}


def H(asset_id):
    return "sha256:" + ASSETS[asset_id]["sha256"]


def wchoice(d):
    keys = list(d)
    return rng.choices(keys, weights=[d[k] for k in keys])[0]


def pick_user_action(subject, category, ctx, scope, sess_no):
    r = applicable_rule(subject, category, ctx, scope, sess_no)
    if r.get("ramp") and sess_no < r["sessions"][0] + r["ramp"]:
        if rng.random() > 0.65:
            return r["ramp_old_action"], r, "ramp_old_style"
        return r["action"], r, "rule"
    if rng.random() < r["strength"]:
        return r["action"], r, "rule"
    if r.get("alt_action"):
        return r["alt_action"], r, "rule_alt"
    alts = [a for a in ACTIONS[category] if a != r["action"] and a not in BANNED_ANYWHERE]
    return rng.choice(alts), r, "noise"


def session_dates(n, start, end):
    days = sorted(rng.sample(range((end - start).days), n))
    return [(start + timedelta(days=d)).replace(hour=rng.randint(9, 21), minute=rng.randint(0, 59), tzinfo=IST)
            for d in days]


# ------------------------------------------------------------------ project base
def make_clip(cid, asset_id, src_in, src_out, start, **kw):
    c = {"id": cid, "asset_hash": H(asset_id), "asset_label": asset_id, "src_in": r3(src_in), "src_out": r3(src_out),
         "timeline_start": r3(start), "speed": 1.0, "volume": kw.pop("volume", 1.0),
         "lineage": {"origin": cid, "parent": None, "created_by_op": kw.pop("created_by_op", "import")}}
    c.update(kw)
    return c


def project_base(pid, name, footage_mix):
    """Imported footage: title card + 7 section clips laid end to end on V1.
    Section footage_type and the kind of cut into each clip (prev_edit) are
    properties of the footage, so they are fixed per project."""
    clips = [make_clip("clp_0001", "title_card", 0.0, 3.0, 0.0)]
    sections, t = [], 3.0
    for k, sec in enumerate(SECTIONS):
        ft = wchoice(footage_mix)
        if sec == "hook" and rng.random() < 0.6 and "talking_head" in footage_mix:
            ft = "talking_head"
        prev = rng.choices(VOCAB["prev_edit"], weights=[0.45, 0.25, 0.2, 0.1])[0]
        asset = rng.choice(FOOTAGE_ASSETS[ft])
        avail = ASSETS[asset]["probed_duration"]
        dur = min(8.0, avail)
        src_in = rng.choice([0.0, 2.0, 4.0]) if avail - dur >= 4.0 else 0.0
        cid = f"clp_{k + 2:04d}"
        clips.append(make_clip(cid, asset, src_in, src_in + dur, t))
        sections.append({"index": k, "section": sec, "clip_id": cid, "footage_type": ft, "prev_edit": prev,
                         "asset_id": asset, "start": r3(t), "duration": r3(dur)})
        t += dur
    tl = {"schema_version": "1.0", "project_id": pid, "name": name,
          "settings": {"fps": 30, "width": 640, "height": 360, "sample_rate": 48000, "time_unit": "seconds",
                       "aspect": "16:9"},
          "tracks": [{"id": "V1", "kind": "video", "name": "Main", "clips": clips},
                     {"id": "V2", "kind": "video", "name": "B-roll overlay", "clips": []},
                     {"id": "T1", "kind": "text", "name": "Titles & captions", "clips": []},
                     {"id": "A1", "kind": "audio", "name": "Music", "clips": []}],
          "transitions": [], "text_layers": [], "effects": []}
    return tl, sections, r3(t)


def decision_points(sections, fmt, footage_mix):
    pts = []
    for s in sections:
        base = {"footage_type": s["footage_type"], "format": fmt, "section": s["section"]}
        pts.append(("transition", {**base, "prev_edit": s["prev_edit"]}, s))
        if rng.random() < 0.6:
            pts.append(("cut_length", {**base, "prev_edit": s["prev_edit"]}, s))
        if s["section"] != "body" or rng.random() < 0.3:
            pts.append(("music_mood", {**base, "prev_edit": "none"}, s))
        if rng.random() < 0.35:
            pts.append(("color_grade", {**base, "prev_edit": "none"}, s))
    ft = wchoice(footage_mix)
    pts.append(("font", {"footage_type": ft, "format": fmt, "section": "hook", "prev_edit": "none"}, None))
    pts.append(("caption_style", {"footage_type": ft, "format": fmt, "section": "body", "prev_edit": "none"}, None))
    if not any(c == "color_grade" for c, _, _ in pts):
        s = next((x for x in sections if x["footage_type"] == "talking_head"), sections[1])
        pts.append(("color_grade", {"footage_type": s["footage_type"], "format": fmt, "section": s["section"],
                                    "prev_edit": "none"}, s))
    return pts


# ------------------------------------------------------------------ op builders
class Ids:
    def __init__(self):
        self.n = {"tr": 0, "txt": 0, "fx": 0, "clp": 20}

    def next(self, kind):
        self.n[kind] += 1
        return f"{kind}_{self.n[kind]:04d}" if kind == "clp" else f"{kind}_{self.n[kind]:02d}"


def sample_dur(bucket, avail):
    lo, hi = CUT_SAMPLE[bucket]
    return r3(rng.uniform(lo, min(hi, avail - 0.05)))


class Decision:
    """Holds the entity a decision creates/edits so later ops can reference it."""

    def __init__(self, cat, ctx, sec, did):
        self.cat, self.ctx, self.sec, self.did = cat, ctx, sec, did
        self.entity = None
        self.trim_base = None       # (src_in, src_out, timeline_start) before the agent trim
        self.state = None           # current value of the decided property


def forward_spec(d, action, ids, project_name, total, prev_clip_id):
    """The op that applies `action` to a not-yet-existing entity (agent proposal,
    or a user re-doing it after an undo)."""
    c = d.cat
    if c == "transition":
        d.entity = d.entity or ids.next("tr")
        return dict(type="add_transition", payload={"transition": {
            "id": d.entity, "track_id": "V1", "from_clip": prev_clip_id, "to_clip": d.sec["clip_id"],
            "type": action, "duration": 0.4}})
    if c in ("font", "caption_style"):
        d.entity = d.entity or ids.next("txt")
        if c == "font":
            tl = {"id": d.entity, "track_id": "T1", "role": "title", "text": project_name, "start": 0.3,
                  "duration": 2.4, "style": {"font": action, "size": 48, "color": "#FFFFFF", "position": "center"}}
        else:
            tl = {"id": d.entity, "track_id": "T1", "role": "captions", "text": "[auto captions]", "start": 3.0,
                  "duration": r3(total - 3.0), "style": {"font": "Inter", "size": 28, "color": "#FFFFFF",
                                                         "position": "bottom", "preset": action}}
        return dict(type="add_text", payload={"text_layer": tl})
    if c == "color_grade":
        d.entity = d.entity or ids.next("fx")
        return dict(type="add_effect", payload={"effect": {"id": d.entity, "target_id": d.sec["clip_id"],
                                                           "type": "color_grade",
                                                           "params": {"look": action, "intensity": 0.6}}})
    if c == "cut_length":
        si, so, ts = d.trim_base
        dur = sample_dur(action, so - si)
        d.state = r3(si + dur)
        return dict(type="trim_clip", payload={"clip_id": d.sec["clip_id"], "edge": "out",
                                               "before": {"src_in": si, "src_out": so, "timeline_start": ts},
                                               "after": {"src_out": d.state}})
    if c == "music_mood":
        if d.entity is None:
            d.entity = ids.next("clp")
        asset = MUSIC_ASSET.get(action, "music_calm")
        return dict(type="add_clip", payload={"track_id": "A1", "clip": make_clip(
            d.entity, asset, 0.0, d.sec["duration"], d.sec["start"], volume=0.8, music_mood=action,
            created_by_op="__self__")})
    raise ValueError(c)


def override_spec(d, old, new):
    """User replaces the agent's choice in place."""
    c = d.cat
    if c == "transition":
        return dict(type="change_transition", payload={"transition_id": d.entity, "before": {"type": old},
                                                       "after": {"type": new}})
    if c == "font":
        return dict(type="set_text_style", payload={"text_layer_id": d.entity, "role": "title",
                                                    "before": {"font": old}, "after": {"font": new}})
    if c == "caption_style":
        return dict(type="set_text_style", payload={"text_layer_id": d.entity, "role": "captions",
                                                    "before": {"preset": old}, "after": {"preset": new}})
    if c == "color_grade":
        return dict(type="update_effect", payload={"effect_id": d.entity, "before": {"params": {"look": old}},
                                                   "after": {"params": {"look": new}}})
    if c == "cut_length":
        si, so, ts = d.trim_base
        cur = d.state
        d.state = r3(si + sample_dur(new, so - si))
        return dict(type="trim_clip", payload={"clip_id": d.sec["clip_id"], "edge": "out",
                                               "before": {"src_in": si, "src_out": cur, "timeline_start": ts},
                                               "after": {"src_out": d.state}})
    if c == "music_mood":
        return dict(type="replace_music", payload={
            "clip_id": d.entity,
            "before": {"asset_hash": H(MUSIC_ASSET.get(old, "music_calm")), "music_mood": old},
            "after": {"asset_hash": H(MUSIC_ASSET.get(new, "music_calm")),
                      "asset_label": MUSIC_ASSET.get(new, "music_calm"), "music_mood": new}})
    raise ValueError(c)


def inverse_spec(d, agent_action):
    """Inverse of the agent op (for an undo)."""
    c = d.cat
    if c == "transition":
        inv = {"type": "remove_transition", "payload": {"transition_id": d.entity}}
    elif c in ("font", "caption_style"):
        inv = {"type": "remove_text", "payload": {"text_layer_id": d.entity}}
    elif c == "color_grade":
        inv = {"type": "remove_effect", "payload": {"effect_id": d.entity}}
    elif c == "cut_length":
        si, so, ts = d.trim_base
        inv = {"type": "trim_clip", "payload": {"clip_id": d.sec["clip_id"], "edge": "out",
                                                "before": {"src_in": si, "src_out": d.state, "timeline_start": ts},
                                                "after": {"src_out": so}}}
        d.state = so
    elif c == "music_mood":
        inv = {"type": "delete_clip", "payload": {"clip_id": d.entity, "before": {"music_mood": agent_action}}}
    return dict(type="revert", payload={"inverse": inv})


# ------------------------------------------------------------------ main
def main():
    if PROJECTS.exists():
        shutil.rmtree(PROJECTS)
    sessions, chat, labels, projects_index, violations = [], [], [], [], []
    viol_idx = {(s, n, c): (who, a) for s, n, who, c, a in BRAND_VIOLATIONS}
    start, end = datetime(2026, 6, 1, tzinfo=IST), datetime(2026, 10, 15, tzinfo=IST)
    chat_n = [0]

    for subject, cfg in SUBJECTS.items():
        dates = session_dates(cfg["sessions"], start, end)
        n_proj = (cfg["sessions"] + SESSIONS_PER_PROJECT - 1) // SESSIONS_PER_PROJECT
        for pj in range(n_proj):
            sess_nos = list(range(pj * SESSIONS_PER_PROJECT + 1,
                                  min(cfg["sessions"], (pj + 1) * SESSIONS_PER_PROJECT) + 1))
            pid = f"proj_{subject}_{pj + 1:02d}"
            if cfg["kind"] == "team":
                scope, brand = "team", "northwind"
            else:
                is_brand = any((subject, n) in FORCED_BRAND for n in sess_nos) or rng.random() < cfg["brand_share"]
                scope, brand = ("team", "northwind") if is_brand else ("private", None)
            pname = f"{'Northwind' if brand else subject.title()} video {pj + 1:02d}"
            base, sections, total = project_base(pid, pname, cfg["footage"])
            store, ids = ProjectStore(pid), Ids()
            t_first = dates[sess_nos[0] - 1]
            first_actor = None
            for i in sess_nos:
                dt = dates[i - 1]
                sid = f"{subject}_s{i:02d}"
                actor = (rng.choices(cfg["members"], weights=[0.6, 0.2, 0.2])[0]
                         if cfg["kind"] == "team" else subject)
                if first_actor is None:
                    first_actor = actor
                    v0 = store.commit(base, [], "rough-cut", actor, t_first.isoformat(),
                                      "Import footage (rough assembly)", [], sid)
                    store.refs["rough-cut"] = {"head": v0, "created_from": None, "created_by": actor,
                                               "created_at": t_first.isoformat()}
                fmt = wchoice(cfg["formats"])
                branch = f"{actor}/cut-{i:02d}"
                t = dt + timedelta(seconds=30)
                store.new_branch(branch, v0, actor, t.isoformat())   # every session branches off the import
                sessions.append({"session_id": sid, "subject": subject, "session_no": i, "actor": actor,
                                 "started_at": dt.isoformat(), "project_id": pid, "branch": branch,
                                 "project_scope": scope, "brand_id": brand, "format": fmt,
                                 "split": "heldout" if i > cfg["sessions"] - HELDOUT_LAST_N else "train"})
                if fmt != "16:9":
                    t += timedelta(seconds=10)
                    store.run_commit(branch, actor, t.isoformat(), f"Reformat to {fmt}", sid, [dict(
                        type="set_format", context={"footage_type": None, "prev_edit": None, "format": fmt,
                                                    "section": None},
                        payload={"before": {"aspect": "16:9"}, "after": {"aspect": fmt}})])
                for (s_, n_, a_, text, ref) in STATEMENTS:
                    if s_ == subject and n_ == i:
                        t += timedelta(seconds=rng.randint(5, 40))
                        chat_n[0] += 1
                        chat.append({"chat_id": f"chat_{chat_n[0]:04d}", "ts": t.isoformat(), "session_id": sid,
                                     "project_id": pid, "branch": branch, "actor": a_, "text": text})
                pts = decision_points(sections, fmt, cfg["footage"])
                decs = []
                for k, (cat, ctx, sec) in enumerate(pts, start=1):
                    d = Decision(cat, ctx, sec, f"{sid}_d{k:02d}")
                    if cat == "cut_length":
                        c = next(x for x in base["tracks"][0]["clips"] if x["id"] == sec["clip_id"])
                        d.trim_base = (c["src_in"], c["src_out"], c["timeline_start"])
                    user_action, rule, how = pick_user_action(subject, cat, ctx, scope, i)
                    agent_action = user_action if rng.random() < NOISE["agent_matches_pref"] else wchoice(AGENT_PRIOR[cat])
                    v = viol_idx.pop((subject, i, cat), None)
                    planted = None
                    if v and brand:
                        who, bad = v
                        if who == "agent":
                            agent_action = bad
                            if user_action == bad:
                                user_action = rule["action"]
                        else:
                            user_action = bad
                        planted = who
                    d.user_action, d.agent_action, d.planted = user_action, agent_action, planted
                    labels.append({"decision_id": d.did, "planted_rule": rule["id"], "generated_by": how,
                                   "intended_user_action": user_action})
                    decs.append(d)
                # Director turns: agent commit, then the user's review commit
                prev_of = {s["clip_id"]: (sections[s["index"] - 1]["clip_id"] if s["index"] else "clp_0001")
                           for s in sections}
                for turn in range(0, len(decs), TURN_SIZE):
                    batch = decs[turn:turn + TURN_SIZE]
                    agent_specs = []
                    for d in batch:
                        t += timedelta(seconds=rng.randint(20, 240))
                        spec = forward_spec(d, d.agent_action, ids, pname, total,
                                            prev_of.get(d.sec["clip_id"]) if d.sec else None)
                        spec.update(author="director", on_behalf_of=actor, ts=t.isoformat(),
                                    decision_id=d.did, context=d.ctx)
                        agent_specs.append(spec)
                    _, aops = store.run_commit(branch, "director", t.isoformat(),
                                               f"Director: {', '.join(sorted({d.cat for d in batch}))}", sid,
                                               agent_specs)
                    review = []
                    for d, aop in zip(batch, aops):
                        d.agent_op = aop["op_id"]
                        if aop["type"] == "add_clip":
                            aop["payload"]["clip"]["lineage"]["created_by_op"] = aop["op_id"]
                        common = dict(author=actor, decision_id=d.did, context=d.ctx)
                        t += timedelta(seconds=rng.randint(2, 30))
                        if d.user_action == d.agent_action:
                            review.append(dict(type="accept", payload={}, accepts_op=d.agent_op, ts=t.isoformat(), **common))
                            d.review_kind = "accept"
                            if rng.random() < NOISE["undo_no_replacement"]:
                                t += timedelta(seconds=rng.randint(2, 30))
                                review.append(dict(**inverse_spec(d, d.agent_action), reverts_op=d.agent_op,
                                                   ts=t.isoformat(), **common))
                                d.review_kind = "accept_then_undo"
                        else:
                            if rng.random() < NOISE["undo_then_override"]:
                                review.append(dict(**inverse_spec(d, d.agent_action), reverts_op=d.agent_op,
                                                   ts=t.isoformat(), **common))
                                t += timedelta(seconds=rng.randint(2, 30))
                                spec = forward_spec(d, d.user_action, ids, pname, total,
                                                    prev_of.get(d.sec["clip_id"]) if d.sec else None)
                                d.review_kind = "undo_then_override"
                            else:
                                spec = override_spec(d, d.agent_action, d.user_action)
                                d.review_kind = "override"
                            review.append(dict(**spec, replaces_op=d.agent_op, ts=t.isoformat(), **common))
                    _, rops = store.run_commit(branch, actor, t.isoformat(), "Review Director edits", sid, review)
                    for o in rops:
                        if o["type"] == "add_clip":
                            o["payload"]["clip"]["lineage"]["created_by_op"] = o["op_id"]
                    for d in batch:
                        if d.planted:
                            if d.planted == "agent":
                                op_id = d.agent_op
                            else:
                                op_id = next(o["op_id"] for o in rops if o["decision_id"] == d.did and
                                             o["type"] != "revert")
                            violations.append({"op_id": op_id, "decision_id": d.did, "session_id": sid,
                                               "who": d.planted, "category": d.cat,
                                               "action": d.agent_action if d.planted == "agent" else d.user_action})
            # created_by_op placeholders were patched on the op objects; rebuild snapshots so they agree
            store = rebuild(store)
            store.write(PROJECTS / pid, {"owner_subject": subject, "project_scope": scope, "brand_id": brand,
                                         "name": pname, "sections": sections, "flagship": False})
            projects_index.append({"project_id": pid, "owner_subject": subject, "project_scope": scope,
                                   "brand_id": brand, "sessions": [f"{subject}_s{n:02d}" for n in sess_nos],
                                   "n_versions": len(store.versions), "n_ops": len(store.ops), "flagship": False})
    assert not viol_idx, f"unplaced brand violations: {viol_idx}"

    # ---------------------------------------------------- flagship project (team, after the 4-month window)
    n_team = SUBJECTS["team_northwind"]["sessions"]
    f_sids = [f"team_northwind_s{n_team + k:02d}" for k in (1, 2, 3, 4)]
    fdir = PROJECTS / flagship.PROJECT_ID
    fstore, fsess, scenario = flagship.build(H, f_sids, fdir)
    fstore.write(fdir, {"owner_subject": "team_northwind", "project_scope": "team", "brand_id": "northwind",
                        "name": "Launch Week Recap (60s cut)", "sections": None, "flagship": True})
    for k, fs in enumerate(fsess):
        sessions.append({"session_id": fs["session_id"], "subject": "team_northwind", "session_no": n_team + k + 1,
                         "actor": fs["actor"], "started_at": fs["started_at"], "project_id": flagship.PROJECT_ID,
                         "branch": fs["branch"], "project_scope": "team", "brand_id": "northwind", "format": "16:9",
                         "split": "flagship"})
    projects_index.append({"project_id": flagship.PROJECT_ID, "owner_subject": "team_northwind",
                           "project_scope": "team", "brand_id": "northwind", "sessions": f_sids,
                           "n_versions": len(fstore.versions), "n_ops": len(fstore.ops), "flagship": True})

    # ---------------------------------------------------- write world
    sessions.sort(key=lambda s: (s["started_at"], s["session_id"]))
    write_json(WORLD / "cast.json", CAST)
    write_json(WORLD / "brand_guidelines.json", BRAND)
    write_json(WORLD / "vocab.json", {"context_keys": CONTEXT_KEYS, "context_values": VOCAB, "actions": ACTIONS,
                                      "cut_length_buckets_seconds": CUT_SECONDS, "event_types": EVENT_TYPES})
    write_jsonl(WORLD / "sessions.jsonl", sessions)
    write_jsonl(WORLD / "chat.jsonl", chat)
    write_jsonl(WORLD / "planted_labels.jsonl", labels)
    write_json(PROJECTS / "index.json", {"note": "One content-addressed store per project; no main branch anywhere.",
                                         "projects": projects_index})

    drift_sess = next(s for s in sessions if s["subject"] == "ava" and s["session_no"] == DRIFT_SESSION)
    heldout = sorted(s["session_id"] for s in sessions if s["split"] == "heldout")
    gt = {
        "WARNING": f"Synthetic data. Rules were planted by generators/world_spec.py + gen_world.py (seed {SEED}). "
                   "Metrics on this data measure recovery of planted structure only.",
        "seed": SEED, "cast": CAST, "subjects": SUBJECTS, "context_keys": CONTEXT_KEYS,
        "rule_resolution": "For (subject, category, context, project_scope) the most specific matching rule inside its "
                           "session window generates the user's choice with probability `strength`; otherwise "
                           "`alt_action` if the rule has one, else a uniform random non-banned alternative. Ramp "
                           "rules use 0.65 for their first `ramp` sessions with `ramp_old_action` as the off-choice. "
                           "Section footage_type/prev_edit are fixed per project (properties of the imported footage); "
                           "format is per session (set_format op on the session branch).",
        "rules": RULES,
        "drift": {"subject": "ava", "category": "transition",
                  "context": {"footage_type": "talking_head", "prev_edit": "jump_cut"},
                  "from_action": "crossfade", "to_action": "whip_pan", "drift_session_no": DRIFT_SESSION,
                  "drift_session_id": drift_sess["session_id"], "drift_date": drift_sess["started_at"],
                  "ramp_sessions": 3,
                  "must_not_drift": [{"context": {"footage_type": "broll"}, "action": "crossfade",
                                      "why": "same old action in a different context stays valid"}],
                  "supporting_statement_session_no": 21},
        "conflicts": [{
            "id": "conflict.ben_vs_team.captions_vertical", "category": "caption_style", "context": {"format": "9:16"},
            "private": {"subject": "ben", "action": "beast_mode", "rule": "ben.cap.vertical.private"},
            "team": {"subject": "team_northwind", "action": "stacked", "rule": "team.cap.vertical"},
            "expected_behaviour": "Do not silently pick. Private project -> beast_mode (private > team). Team/brand "
                                  "project -> stacked, surfaced as a conflict citing source events from both sides."},
            {"id": "conflict.ben_font_vs_brand", "category": "font", "private": {"subject": "ben", "action": "Bebas Neue"},
             "brand": {"brand_id": "northwind", "allowed": BRAND["hard_constraints"]["fonts"]["allowed"]},
             "expected_behaviour": "Brand hard constraint wins in brand projects: never suggest Bebas Neue there."}],
        "brand_violations_planted": violations,
        "explicit_statements": [{"subject": s, "session_no": n, "actor": a, "text": t, "refers_to_rule": r}
                                for s, n, a, t, r in STATEMENTS],
        "noise": NOISE,
        "flagship": {"project_id": flagship.PROJECT_ID, "sessions": f_sids, "split": "flagship",
                     "note": "Scripted #10 demo. Its categorised user ops become user_edit memory events; they are "
                             "not generated from the planted rules and are excluded from train/held-out."},
        "heldout": {"rule": f"last {HELDOUT_LAST_N} generated sessions of each subject", "session_ids": heldout,
                    "files": ["splits/events_train.jsonl", "splits/events_heldout.jsonl",
                              "splits/events_flagship.jsonl", "splits/decisions_heldout.jsonl"],
                    "label": "decisions_heldout.final_action (null = user undid without a replacement)"},
        "baseline_definition": "most-frequent final_action per category over the subject's training events, ignoring "
                               "context. Compute it; no numbers are claimed here.",
    }
    write_json(DATA / "memory" / "ground_truth.json", gt)
    n_ops = sum(p["n_ops"] for p in projects_index)
    n_ver = sum(p["n_versions"] for p in projects_index)
    print(f"[world] projects={len(projects_index)} sessions={len(sessions)} versions={n_ver} ops={n_ops} "
          f"chat={len(chat)} decisions={len(labels)} brand_violations={len(violations)}")


def rebuild(store):
    """Replay a project's ops from its root to regenerate snapshots/version ids
    after patching lineage.created_by_op on add_clip ops."""
    from timeline_ops import apply_op
    new = ProjectStore(store.project_id)
    new.n_ops = store.n_ops
    vmap, ops_by_id = {}, {o["op_id"]: o for o in store.ops}
    for vid, v in store.versions.items():   # insertion order == creation order
        parents = [vmap[p] for p in v["parents"]]
        tl = store.timeline(vid) if not parents else new.timeline(parents[0])
        for oid in v["op_ids"]:
            op = ops_by_id[oid]
            op["parent_version"] = parents[0]
            tl = apply_op(tl, op)
        nv = new.commit(tl, parents, v["branch"], v["author"], v["timestamp"], v["message"], v["op_ids"],
                        v["session_id"])
        vmap[vid] = nv
        for oid in v["op_ids"]:
            ops_by_id[oid]["result_version"] = nv
    new.refs = {b: {**r, "head": vmap[r["head"]], "created_from": vmap.get(r["created_from"])}
                for b, r in store.refs.items()}
    new.ops = store.ops
    return new


if __name__ == "__main__":
    main()
