/** Op semantics: TypeScript port of generators/timeline_ops.py (must stay byte-compatible). */
import { r3 } from "./canonical.js";
import type { Clip, Op, Timeline, Track } from "./types.js";

export class OpError extends Error {}

const clone = <T>(x: T): T => structuredClone(x);
const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

export function clipEnd(c: Clip): number {
  return r3(c.timeline_start + (c.src_out - c.src_in) / (c.speed ?? 1));
}

export function findClip(tl: Timeline, id: string): [Track, Clip] {
  for (const t of tl.tracks) for (const c of t.clips) if (c.id === id) return [t, c];
  throw new OpError(`clip not found: ${id}`);
}
export function findTrack(tl: Timeline, id: string): Track {
  const t = tl.tracks.find((x) => x.id === id);
  if (!t) throw new OpError(`track not found: ${id}`);
  return t;
}
function byId<T extends { id: string }>(xs: T[], id: string, what: string): T {
  const x = xs.find((y) => y.id === id);
  if (!x) throw new OpError(`${what} not found: ${id}`);
  return x;
}
function sortTrack(t: Track) {
  t.clips.sort((a, b) => a.timeline_start - b.timeline_start || cmpStr(a.id, b.id));
}
function deepEq(a: any, b: any) {
  return JSON.stringify(a) === JSON.stringify(b);
}
function checkBefore(actual: any, expected: Record<string, any> | undefined, ctx: string, force: boolean) {
  if (!expected || force) return;
  for (const [k, v] of Object.entries(expected)) {
    if (!deepEq(actual[k], v)) throw new OpError(`${ctx}: precondition failed on ${k}: have ${JSON.stringify(actual[k])}, op expects ${JSON.stringify(v)}`);
  }
}

/** Apply one op. `force` skips before-preconditions (used when a user picks "theirs" in a conflict). */
export function applyOp(tlIn: Timeline, op: Pick<Op, "type" | "payload" | "op_id">, force = false): Timeline {
  const tl = clone(tlIn);
  const p = op.payload;
  switch (op.type) {
    case "add_track":
      if (tl.tracks.some((x) => x.id === p.track.id)) throw new OpError("track exists");
      tl.tracks.push(clone(p.track));
      break;
    case "add_clip": {
      const tr = findTrack(tl, p.track_id);
      if (tr.clips.some((c) => c.id === p.clip.id)) throw new OpError(`clip exists: ${p.clip.id}`);
      tr.clips.push(clone(p.clip));
      sortTrack(tr);
      break;
    }
    case "delete_clip": {
      const [tr, c] = findClip(tl, p.clip_id);
      checkBefore(c, p.before, op.type, force);
      tr.clips = tr.clips.filter((x) => x.id !== p.clip_id);
      tl.transitions = tl.transitions.filter((x) => x.from_clip !== p.clip_id && x.to_clip !== p.clip_id);
      tl.effects = tl.effects.filter((x) => x.target_id !== p.clip_id);
      break;
    }
    case "trim_clip":
    case "set_volume":
    case "set_speed":
    case "replace_music": {
      const [tr, c] = findClip(tl, p.clip_id);
      checkBefore(c, p.before, op.type, force);
      Object.assign(c, clone(p.after));
      sortTrack(tr);
      break;
    }
    case "move_clip": {
      const [tr, c] = findClip(tl, p.clip_id);
      checkBefore({ ...c, track_id: tr.id }, p.before, op.type, force);
      const nt = findTrack(tl, p.after.track_id ?? tr.id);
      c.timeline_start = p.after.timeline_start;
      if (nt !== tr) {
        tr.clips = tr.clips.filter((x) => x !== c);
        nt.clips.push(c);
      }
      sortTrack(nt);
      break;
    }
    case "split_clip": {
      const [tr, c] = findClip(tl, p.clip_id);
      const at = p.at;
      if (!(c.timeline_start < at && at < clipEnd(c))) throw new OpError("split point outside clip");
      const offset = r3((at - c.timeline_start) * (c.speed ?? 1));
      const [lid, rid] = p.new_ids;
      const left = { ...clone(c), id: lid, src_out: r3(c.src_in + offset) };
      const right = { ...clone(c), id: rid, src_in: r3(c.src_in + offset), timeline_start: r3(at) };
      for (const piece of [left, right]) piece.lineage = { origin: c.lineage.origin, parent: c.id, created_by_op: op.op_id };
      const idx = tr.clips.indexOf(c);
      tr.clips.splice(idx, 1, left, right);
      for (const x of tl.transitions) {
        if (x.to_clip === c.id) x.to_clip = lid;
        if (x.from_clip === c.id) x.from_clip = rid;
      }
      const extra: typeof tl.effects = [];
      for (const fx of tl.effects) {
        if (fx.target_id === c.id) {
          fx.target_id = lid;
          extra.push({ ...clone(fx), id: fx.id + "_r", target_id: rid });
        }
      }
      tl.effects.push(...extra);
      break;
    }
    case "change_transition": {
      const x = byId(tl.transitions, p.transition_id, "transition");
      checkBefore(x, p.before, op.type, force);
      Object.assign(x, clone(p.after));
      break;
    }
    case "add_transition":
      tl.transitions.push(clone(p.transition));
      break;
    case "remove_transition":
      byId(tl.transitions, p.transition_id, "transition");
      tl.transitions = tl.transitions.filter((x) => x.id !== p.transition_id);
      break;
    case "add_text":
      tl.text_layers.push(clone(p.text_layer));
      break;
    case "edit_text": {
      const x = byId(tl.text_layers, p.text_layer_id, "text layer");
      checkBefore(x, p.before, op.type, force);
      Object.assign(x, clone(p.after));
      break;
    }
    case "remove_text":
      byId(tl.text_layers, p.text_layer_id, "text layer");
      tl.text_layers = tl.text_layers.filter((x) => x.id !== p.text_layer_id);
      break;
    case "set_text_style": {
      const x = byId(tl.text_layers, p.text_layer_id, "text layer");
      checkBefore(x.style, p.before, op.type, force);
      Object.assign(x.style, clone(p.after));
      break;
    }
    case "add_effect":
      tl.effects.push(clone(p.effect));
      break;
    case "update_effect": {
      const x = byId(tl.effects, p.effect_id, "effect");
      checkBefore(x.params, p.before?.params, op.type, force);
      Object.assign(x.params, clone(p.after.params));
      break;
    }
    case "remove_effect":
      byId(tl.effects, p.effect_id, "effect");
      tl.effects = tl.effects.filter((x) => x.id !== p.effect_id);
      break;
    case "set_format":
      checkBefore(tl.settings, p.before, op.type, force);
      Object.assign(tl.settings, clone(p.after));
      break;
    case "accept":
      break;
    case "revert":
      return applyOp(tlIn, { op_id: op.op_id, type: p.inverse.type, payload: p.inverse.payload }, force);
    default:
      throw new OpError(`unknown op type ${op.type}`);
  }
  tl.transitions.sort((a, b) => cmpStr(a.id, b.id));
  tl.text_layers.sort((a, b) => cmpStr(a.id, b.id));
  tl.effects.sort((a, b) => cmpStr(a.id, b.id));
  return tl;
}

/** Clips that overlap on the same track (a timeline invariant). */
export function overlaps(tl: Timeline): { track_id: string; a: string; b: string; overlap: [number, number] }[] {
  const out: { track_id: string; a: string; b: string; overlap: [number, number] }[] = [];
  for (const t of tl.tracks) {
    const cs = [...t.clips].sort((a, b) => a.timeline_start - b.timeline_start);
    for (let i = 0; i < cs.length; i++)
      for (let j = i + 1; j < cs.length; j++) {
        const lo = Math.max(cs[i].timeline_start, cs[j].timeline_start);
        const hi = Math.min(clipEnd(cs[i]), clipEnd(cs[j]));
        if (hi - lo > 1e-6) out.push({ track_id: t.id, a: cs[i].id, b: cs[j].id, overlap: [r3(lo), r3(hi)] });
      }
  }
  return out;
}

export function timelineDuration(tl: Timeline): number {
  let end = 0;
  for (const t of tl.tracks) for (const c of t.clips) end = Math.max(end, clipEnd(c));
  for (const x of tl.text_layers) end = Math.max(end, x.start + x.duration);
  return r3(end);
}
