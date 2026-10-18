"""The flagship #10 demo scenario, as one team project inside the world.

base (rough-cut, ava) -> A ava/tighter-cut: trim + swap transition
                      -> B cara/titles-broll: add text + move clip on another track
                      -> C ben/alt-pacing: overlapping trim of the same clip as A (conflict)
A head               -> D ava/alt-ending: split, delete, add b-roll, grade, transition
Expected selective-pull outcomes are computed with vcs_store.ops_conflict.
"""
import shutil

from common import content_hash, r3, write_json
from timeline_ops import apply_op
from vcs_store import ProjectStore, ops_conflict

PROJECT_ID = "proj_launch_week_recap"
TZ = "+05:30"


def ctx(ft=None, prev=None, section=None, fmt="16:9"):
    return {"footage_type": ft, "prev_edit": prev, "format": fmt, "section": section}


def base_timeline(H):
    def clip(cid, asset_id, src_in, src_out, start, **kw):
        c = {"id": cid, "asset_hash": H(asset_id), "asset_label": asset_id, "src_in": r3(src_in),
             "src_out": r3(src_out), "timeline_start": r3(start), "speed": 1.0, "volume": kw.pop("volume", 1.0),
             "lineage": {"origin": cid, "parent": None, "created_by_op": "import"}}
        c.update(kw)
        return c

    return {
        "schema_version": "1.0", "project_id": PROJECT_ID, "name": "Launch Week Recap (60s cut)",
        "settings": {"fps": 30, "width": 640, "height": 360, "sample_rate": 48000, "time_unit": "seconds",
                     "aspect": "16:9"},
        "tracks": [
            {"id": "V1", "kind": "video", "name": "Main (A/B cam)", "clips": [
                clip("clp_0001", "title_card", 0.0, 3.0, 0.0), clip("clp_0002", "a_cam", 0.5, 6.5, 3.0),
                clip("clp_0003", "b_cam", 2.0, 7.0, 9.0), clip("clp_0004", "a_cam", 6.5, 11.5, 14.0),
                clip("clp_0005", "broll_screen", 0.0, 6.0, 19.0), clip("clp_0006", "b_cam", 7.0, 11.0, 25.0)]},
            {"id": "V2", "kind": "video", "name": "B-roll overlay", "clips": [
                clip("clp_0007", "broll_city", 0.5, 3.5, 5.0), clip("clp_0008", "broll_product", 1.0, 4.0, 15.5)]},
            {"id": "T1", "kind": "text", "name": "Titles & captions", "clips": []},
            {"id": "A1", "kind": "audio", "name": "Music", "clips": [
                clip("clp_0009", "music_upbeat", 0.0, 29.0, 0.0, volume=0.8, music_mood="upbeat")]},
            {"id": "A2", "kind": "audio", "name": "SFX", "clips": [clip("clp_0010", "sfx_whoosh", 0.0, 0.8, 2.6)]},
        ],
        "transitions": [
            {"id": "tr_01", "track_id": "V1", "from_clip": "clp_0001", "to_clip": "clp_0002", "type": "crossfade", "duration": 0.5},
            {"id": "tr_02", "track_id": "V1", "from_clip": "clp_0003", "to_clip": "clp_0004", "type": "crossfade", "duration": 0.4},
            {"id": "tr_03", "track_id": "V1", "from_clip": "clp_0005", "to_clip": "clp_0006", "type": "dip_to_black", "duration": 0.5},
        ],
        "text_layers": [
            {"id": "txt_01", "track_id": "T1", "role": "title", "text": "Launch Week Recap", "start": 0.3, "duration": 2.4,
             "style": {"font": "Inter", "size": 48, "color": "#FFFFFF", "position": "center", "animation": "fade_up"}}],
        "effects": [{"id": "fx_01", "target_id": "clp_0002", "type": "color_grade",
                     "params": {"look": "warm_film", "intensity": 0.6}}],
    }


def build(H, session_ids, out_dir):
    """session_ids: 4 team session ids (base+A, B, C, D). Returns (store, sessions, scenario)."""
    sA, sB, sC, sD = session_ids
    s = ProjectStore(PROJECT_ID)
    base = base_timeline(H)
    t0 = f"2026-10-16T10:00:00{TZ}"
    v0 = s.commit(base, [], "rough-cut", "ava", t0, "Rough assembly from imported footage", [], sA)
    s.refs["rough-cut"] = {"head": v0, "created_from": None, "created_by": "ava", "created_at": t0}

    s.new_branch("ava/tighter-cut", v0, "ava", f"2026-10-16T11:00:00{TZ}")
    s.run_commit("ava/tighter-cut", "ava", f"2026-10-16T11:12:00{TZ}", "Tighten B-cam answer", sA, [
        dict(type="trim_clip", context=ctx("talking_head", "jump_cut", "body"),
             payload={"clip_id": "clp_0003", "edge": "out",
                      "before": {"src_in": 2.0, "src_out": 7.0, "timeline_start": 9.0}, "after": {"src_out": 5.8}})])
    vA = s.run_commit("ava/tighter-cut", "ava", f"2026-10-16T11:20:00{TZ}",
                      "Punchier open: whip pan out of the title", sA, [
        dict(type="change_transition", context=ctx("talking_head", "scene_change", "hook"),
             payload={"transition_id": "tr_01", "before": {"type": "crossfade", "duration": 0.5},
                      "after": {"type": "whip_pan", "duration": 0.3}})])[0]

    s.new_branch("cara/titles-broll", v0, "cara", f"2026-10-16T11:30:00{TZ}")
    s.run_commit("cara/titles-broll", "cara", f"2026-10-16T11:45:00{TZ}", "Lower-third for the shipping beat", sB, [
        dict(type="add_text", context=ctx("product", "none", "body"),
             payload={"text_layer": {"id": "txt_02", "track_id": "T1", "role": "lower_third",
                                     "text": "Shipping in 3 days", "start": 15.6, "duration": 2.5,
                                     "style": {"font": "Inter", "size": 32, "color": "#FFD400",
                                               "position": "lower_third", "animation": "slide_in"}}})])
    s.run_commit("cara/titles-broll", "cara", f"2026-10-16T11:58:00{TZ}",
                 "Land product b-roll on the 'ship' line; title copy", sB, [
        dict(type="move_clip", context=ctx("product", "broll_insert", "body"),
             payload={"clip_id": "clp_0008", "before": {"track_id": "V2", "timeline_start": 15.5},
                      "after": {"track_id": "V2", "timeline_start": 17.0}}),
        dict(type="edit_text", context=ctx(section="hook"),
             payload={"text_layer_id": "txt_01", "before": {"text": "Launch Week Recap"},
                      "after": {"text": "Launch Week: Recap"}})])

    s.new_branch("ben/alt-pacing", v0, "ben", f"2026-10-16T12:10:00{TZ}")
    s.run_commit("ben/alt-pacing", "ben", f"2026-10-16T12:25:00{TZ}", "Keep more of the B-cam answer, duck music", sC, [
        dict(type="trim_clip", context=ctx("talking_head", "jump_cut", "body"),
             payload={"clip_id": "clp_0003", "edge": "out",
                      "before": {"src_in": 2.0, "src_out": 7.0, "timeline_start": 9.0}, "after": {"src_out": 6.4}}),
        dict(type="set_volume", context=ctx(),
             payload={"clip_id": "clp_0009", "before": {"volume": 0.8}, "after": {"volume": 0.5}})])

    s.new_branch("ava/alt-ending", vA, "ava", f"2026-10-17T09:30:00{TZ}")
    s.run_commit("ava/alt-ending", "ava", f"2026-10-17T09:40:00{TZ}", "Shorter sign-off: split and drop the tail", sD, [
        dict(type="split_clip", context=ctx("talking_head", "scene_change", "outro"),
             payload={"clip_id": "clp_0006", "at": 27.0, "new_ids": ["clp_0011", "clp_0012"]}),
        dict(type="delete_clip", context=ctx("talking_head", "scene_change", "outro"),
             payload={"clip_id": "clp_0012", "before": {"src_in": 9.0, "src_out": 11.0, "timeline_start": 27.0}})])
    next_op = f"{PROJECT_ID}/op_{s.n_ops + 1:04d}"
    desk = {"id": "clp_0013", "asset_hash": H("broll_desk"), "asset_label": "broll_desk", "src_in": 0.0,
            "src_out": 3.0, "timeline_start": 22.0, "speed": 1.0, "volume": 1.0,
            "lineage": {"origin": "clp_0013", "parent": None, "created_by_op": next_op}}
    s.run_commit("ava/alt-ending", "ava", f"2026-10-17T09:55:00{TZ}", "Desk b-roll over the demo, graded teal/orange", sD, [
        dict(type="add_clip", context=ctx("broll", "broll_insert", "body"), payload={"track_id": "V2", "clip": desk}),
        dict(type="add_effect", context=ctx("broll", "none", "body"),
             payload={"effect": {"id": "fx_02", "target_id": "clp_0013", "type": "color_grade",
                                 "params": {"look": "teal_orange", "intensity": 0.5}}}),
        dict(type="update_effect", context=ctx("talking_head", "none", "hook"),
             payload={"effect_id": "fx_01", "before": {"params": {"intensity": 0.6}},
                      "after": {"params": {"intensity": 0.8}}}),
        dict(type="add_transition", context=ctx("screen_recording", "scene_change", "body"),
             payload={"transition": {"id": "tr_04", "track_id": "V1", "from_clip": "clp_0004",
                                     "to_clip": "clp_0005", "type": "slide", "duration": 0.4}})])

    # ---------------------------------------------------- expected selective pulls
    def chain(vid):
        out = []
        while vid:
            out.append(vid)
            ps = s.versions[vid]["parents"]
            vid = ps[0] if ps else None
        return out

    ops_by_id = {o["op_id"]: o for o in s.ops}

    def branch_ops(vids):
        return [ops_by_id[i] for v in reversed(vids) for i in s.versions[v]["op_ids"]]

    exp_dir = out_dir / "expected"
    if exp_dir.exists():
        shutil.rmtree(exp_dir)

    def pull(into, frm):
        ci, cf = chain(s.refs[into]["head"]), chain(s.refs[frm]["head"])
        anc = next(v for v in ci if v in set(cf))
        anc_ops = {o["op_id"] for o in branch_ops(chain(anc))}
        ours = [o for o in branch_ops(ci) if o["op_id"] not in anc_ops]
        theirs = [o for o in branch_ops(cf) if o["op_id"] not in anc_ops]
        conflicts = [{"ours": o["op_id"], "theirs": t["op_id"], "reasons": r}
                     for t in theirs for o in ours for r in [ops_conflict(o, t)] if r]
        res = {"into": into, "from": frm, "common_ancestor": anc, "ours_ops": [o["op_id"] for o in ours],
               "theirs_ops": [o["op_id"] for o in theirs], "expected": "conflict" if conflicts else "clean",
               "conflicts": conflicts}
        if not conflicts:
            tl = s.timeline(s.refs[into]["head"])
            for t in theirs:
                tl = apply_op(tl, t)
            res["result_timeline_hash"] = content_hash(tl)
            res["result_file"] = f"expected/pull__{frm.replace('/', '_')}__into__{into.replace('/', '_')}.json"
            write_json(out_dir / res["result_file"], tl)
        return res

    pulls = [pull("ava/tighter-cut", "cara/titles-broll"), pull("ava/tighter-cut", "ben/alt-pacing"),
             pull("ava/alt-ending", "cara/titles-broll"), pull("cara/titles-broll", "ben/alt-pacing")]

    heads = out_dir / "heads"
    if heads.exists():
        shutil.rmtree(heads)
    for b, r in s.refs.items():
        write_json(heads / f"{b.replace('/', '_')}.json", s.timeline(r["head"]))
    write_json(out_dir / "base_timeline.json", base)

    scenario = {
        "project_id": PROJECT_ID,
        "collaborators": {"ava": "lead editor (A, D)", "cara": "titles/motion (B)", "ben": "second editor (C)"},
        "sessions": {"base+A": sA, "B": sB, "C": sC, "D": sD},
        "story": [
            "ava imports footage -> base version on branch rough-cut",
            "A ava/tighter-cut: trims the B-cam answer clp_0003 (out edge 14.0s -> 12.8s) and swaps the opening crossfade for a whip pan",
            "B cara/titles-broll (from base): adds a lower-third, moves product b-roll clp_0008 on V2 15.5s -> 17.0s, edits title copy",
            "C ben/alt-pacing (from base): trims the SAME clip clp_0003 differently (out edge -> 13.4s) and ducks the music",
            "D ava/alt-ending (from A's head): splits clp_0006 at 27.0s, deletes the tail half, adds desk b-roll with a teal/orange grade, bumps the warm grade, adds a slide transition",
        ],
        "edit_types_used": sorted({o["type"] for o in s.ops}),
        "selective_pull_expectations": pulls,
        "conflict_rule": "Two ops conflict if they touch the same entity (clip id or clip lineage origin, transition, "
                         "text layer, effect) with different results, or overlapping time ranges on the same track "
                         "(generators/vcs_store.py::ops_conflict).",
    }
    write_json(out_dir / "scenario.json", scenario)
    sessions = [
        {"session_id": sA, "actor": "ava", "branch": "ava/tighter-cut", "started_at": t0},
        {"session_id": sB, "actor": "cara", "branch": "cara/titles-broll", "started_at": f"2026-10-16T11:30:00{TZ}"},
        {"session_id": sC, "actor": "ben", "branch": "ben/alt-pacing", "started_at": f"2026-10-16T12:10:00{TZ}"},
        {"session_id": sD, "actor": "ava", "branch": "ava/alt-ending", "started_at": f"2026-10-17T09:30:00{TZ}"},
    ]
    return s, sessions, scenario
