/** Port of generators/vcs_store.py touches() and ops_conflict(). */
import { canonical, r3 } from "./canonical.js";
import { clipEnd, findClip } from "./ops.js";
import type { Op, Timeline, Touches } from "./types.js";

export function touches(tl: Timeline, op: Pick<Op, "type" | "payload">): Touches {
  const p = op.payload;
  const t = op.type;
  if (t === "revert") return touches(tl, { type: p.inverse.type, payload: p.inverse.payload });
  const ents: string[] = [];
  const ranges: Touches["ranges"] = [];
  const clipRange = (track_id: string, c: any) => ranges.push({ track_id, start: c.timeline_start, end: clipEnd(c) });
  if (["trim_clip", "set_volume", "set_speed", "delete_clip", "move_clip", "split_clip", "replace_music"].includes(t)) {
    const [tr, c] = findClip(tl, p.clip_id);
    ents.push(`clip:${c.id}`, `lineage:${c.lineage.origin}`);
    if (t === "trim_clip") {
      const after = { ...c, ...p.after };
      const [a, b] = p.edge === "out" ? [clipEnd(c), clipEnd(after)] : [c.timeline_start, after.timeline_start];
      ranges.push({ track_id: tr.id, start: Math.min(a, b), end: Math.max(a, b) });
    } else clipRange(tr.id, c);
    if (["set_volume", "set_speed", "move_clip"].includes(t)) {
      const { track_id, ...rest } = p.after;
      clipRange(track_id ?? tr.id, { ...c, ...rest });
    }
  } else if (t === "add_clip") {
    ents.push(`clip:${p.clip.id}`, `lineage:${p.clip.lineage.origin}`);
    clipRange(p.track_id, p.clip);
  } else if (["change_transition", "remove_transition", "add_transition"].includes(t)) {
    const x = t === "add_transition" ? p.transition : tl.transitions.find((y) => y.id === p.transition_id)!;
    ents.push(`transition:${x.id}`);
    const [, a] = findClip(tl, x.from_clip);
    const cut = clipEnd(a);
    const d = Math.max(x.duration, p.after?.duration ?? 0);
    ranges.push({ track_id: x.track_id, start: r3(cut - d / 2), end: r3(cut + d / 2) });
  } else if (["add_text", "edit_text", "remove_text", "set_text_style"].includes(t)) {
    const x = t === "add_text" ? p.text_layer : tl.text_layers.find((y) => y.id === p.text_layer_id)!;
    ents.push(`text:${x.id}`);
    const after = t === "edit_text" ? { ...x, ...p.after } : x;
    for (const y of [x, after]) ranges.push({ track_id: y.track_id, start: y.start, end: r3(y.start + y.duration) });
  } else if (["add_effect", "update_effect", "remove_effect"].includes(t)) {
    const x = t === "add_effect" ? p.effect : tl.effects.find((y) => y.id === p.effect_id)!;
    ents.push(`effect:${x.id}`);
    try {
      const [tr, c] = findClip(tl, x.target_id);
      ents.push(`lineage:${c.lineage.origin}`);
      clipRange(tr.id, c);
    } catch {}
  } else if (t === "add_track") ents.push(`track:${p.track.id}`);
  else if (t === "set_format") ents.push("settings:aspect");
  const uniq: Touches["ranges"] = [];
  for (const r of ranges) if (!uniq.some((u) => JSON.stringify(u) === JSON.stringify(r))) uniq.push(r);
  return { entities: [...new Set(ents)].sort(), ranges: uniq };
}

export interface ConflictReason {
  kind: "same_entity" | "overlapping_time_range" | "overlap_after_apply";
  entities?: string[]; track_id?: string; overlap?: [number, number]; clips?: string[];
}

export function opsConflict(a: Op, b: Op): ConflictReason[] {
  const reasons: ConflictReason[] = [];
  const eb = new Set(b.touches.entities);
  const shared = a.touches.entities.filter((e) => eb.has(e)).sort();
  if (shared.length && canonical(a.payload.after ?? null) !== canonical(b.payload.after ?? null))
    reasons.push({ kind: "same_entity", entities: shared });
  for (const ra of a.touches.ranges)
    for (const rb of b.touches.ranges)
      if (ra.track_id === rb.track_id) {
        const lo = Math.max(ra.start, rb.start);
        const hi = Math.min(ra.end, rb.end);
        const r: ConflictReason = { kind: "overlapping_time_range", track_id: ra.track_id, overlap: [r3(lo), r3(hi)] };
        if (hi - lo > 1e-6 && !reasons.some((x) => JSON.stringify(x) === JSON.stringify(r))) reasons.push(r);
      }
  return reasons;
}
