/**
 * Selective pull (instead of merge-into-main): take some or all of another
 * branch's ops since the common ancestor and replay them onto your branch.
 *  - auto-apply when ops touch different clips / tracks / time ranges
 *  - conflict when the same entity was edited differently, the touched time
 *    ranges overlap on a track, the op no longer applies (precondition), or the
 *    result would put two clips on top of each other on one track.
 * The result is a new version on the target branch.
 */
import { applyOp, findClip, OpError, overlaps } from "./ops.js";
import { Store } from "./store.js";
import { opsConflict, touches, type ConflictReason } from "./touches.js";
import type { Op, Timeline, Version } from "./types.js";
import { diffTimelines, type DiffItem } from "./diff.js";

export interface PullConflict {
  id: string;                 // = theirs op id (one decision per incoming op)
  theirs: Op;
  ours: Op[];                 // our ops it collides with (may be empty for overlap/precondition)
  pairs: { ours: string; theirs: string; reasons: ConflictReason[] }[];
  reasons: ConflictReason[];
}
export interface PullPlan {
  project_id: string; into: string; from: string; into_head: string; from_head: string;
  common_ancestor: string | null;
  ours_ops: Op[]; theirs_ops: Op[]; selected: Op[]; skipped_already_pulled: string[];
  auto: Op[]; conflicts: PullConflict[];
  expected: "clean" | "conflict";
  preview: Timeline;          // into-head + auto ops (conflicts left out)
}
export type Resolution = "ours" | "theirs";

const shortId = (id: string) => id.split("/").pop()!;
const matches = (op: Op, want: string) => op.op_id === want || shortId(op.op_id) === want;

export function planPull(store: Store, into: string, from: string, opIds?: string[]): PullPlan {
  const intoHead = store.resolve(into);
  const fromHead = store.resolve(from);
  const anc = store.commonAncestor(intoHead, fromHead);
  const ours = store.opsSince(intoHead, anc);
  const theirsAll = store.opsSince(fromHead, anc);
  const already = new Set<string>();
  for (const o of ours) {
    already.add(o.op_id);
    if (o.pulled_from_op) already.add(o.pulled_from_op);
  }
  const skipped = theirsAll.filter((t) => already.has(t.op_id) || (t.pulled_from_op && already.has(t.pulled_from_op))).map((t) => t.op_id);
  const theirs = theirsAll.filter((t) => !skipped.includes(t.op_id));
  const selected = opIds && opIds.length ? theirs.filter((t) => opIds.some((w) => matches(t, w))) : theirs;
  if (opIds && opIds.length && selected.length !== opIds.length)
    throw new Error(`some ops are not pullable from ${from}: ${opIds.filter((w) => !theirs.some((t) => matches(t, w))).join(", ")}`);

  const conflicts: PullConflict[] = [];
  const auto: Op[] = [];
  let tl = store.checkout(intoHead);
  for (const t of selected) {
    const pairs = ours.map((o) => ({ ours: o.op_id, theirs: t.op_id, reasons: opsConflict(o, t) })).filter((p) => p.reasons.length);
    if (pairs.length) {
      conflicts.push({ id: t.op_id, theirs: t, ours: pairs.map((p) => store.opsById.get(p.ours)!), pairs,
        reasons: pairs.flatMap((p) => p.reasons) });
      continue;
    }
    let next: Timeline;
    try {
      next = applyOp(tl, t);
    } catch (e) {
      if (!(e instanceof OpError)) throw e;
      conflicts.push({ id: t.op_id, theirs: t, ours: [], pairs: [], reasons: [{ kind: "same_entity", entities: t.touches.entities }] });
      continue;
    }
    const before = new Set(overlaps(tl).map((x) => `${x.track_id}:${x.a}:${x.b}`));
    const fresh = overlaps(next).filter((x) => !before.has(`${x.track_id}:${x.a}:${x.b}`));
    if (fresh.length) {
      conflicts.push({ id: t.op_id, theirs: t, ours: [], pairs: [], reasons: fresh.map((x) => ({ kind: "overlap_after_apply",
        track_id: x.track_id, overlap: x.overlap, clips: [x.a, x.b] })) });
      continue;
    }
    tl = next;
    auto.push(t);
  }
  return { project_id: store.projectId, into, from, into_head: intoHead, from_head: fromHead, common_ancestor: anc,
    ours_ops: ours, theirs_ops: theirs, selected, skipped_already_pulled: skipped, auto, conflicts,
    expected: conflicts.length ? "conflict" : "clean", preview: tl };
}

/** Rewrite an op's `before` to the values currently in `tl` (used when taking "theirs"). */
export function rebaseOp(tl: Timeline, op: Op): Op {
  const p = structuredClone(op.payload);
  if (!p.before) return { ...op, payload: p };
  let cur: any = null;
  try {
    if (p.clip_id) {
      const [tr, c] = findClip(tl, p.clip_id);
      cur = op.type === "move_clip" ? { ...c, track_id: tr.id } : c;
    } else if (p.transition_id) cur = tl.transitions.find((x) => x.id === p.transition_id);
    else if (p.text_layer_id) {
      const x = tl.text_layers.find((y) => y.id === p.text_layer_id);
      cur = op.type === "set_text_style" ? x?.style : x;
    } else if (p.effect_id) {
      const x = tl.effects.find((y) => y.id === p.effect_id);
      cur = x ? { params: x.params } : null;
    } else if (op.type === "set_format") cur = tl.settings;
  } catch {
    cur = null;
  }
  if (cur) {
    if (op.type === "update_effect") {
      for (const k of Object.keys(p.before.params ?? {})) p.before.params[k] = cur.params[k];
    } else for (const k of Object.keys(p.before)) p.before[k] = structuredClone(cur[k]);
  }
  return { ...op, payload: p };
}

export interface PullResult {
  status: "applied" | "conflict" | "nothing";
  plan: PullPlan;
  version?: Version;
  new_ops?: Op[];
  diff?: DiffItem[];
}

/**
 * Execute a pull. Without resolutions, conflicts stop the pull (status "conflict").
 * With a resolution for every conflict ("ours" = skip their op, "theirs" = apply
 * their op over ours), the result is committed as a new version on `into`.
 */
export function pull(store: Store, into: string, from: string, opts: { opIds?: string[]; resolutions?: Record<string, Resolution>;
  author?: string; message?: string; timestamp?: string } = {}): PullResult {
  const plan = planPull(store, into, from, opts.opIds);
  const res: Record<string, Resolution> = {};
  for (const [k, v] of Object.entries(opts.resolutions ?? {})) {
    const c = plan.conflicts.find((x) => matches(x.theirs, k));
    if (!c) throw new Error(`no conflict for ${k}`);
    res[c.id] = v;
  }
  if (plan.conflicts.some((c) => !res[c.id])) return { status: "conflict", plan };
  if (!plan.selected.length) return { status: "nothing", plan };

  const author = opts.author ?? "ava";
  let tl = store.checkout(plan.into_head);
  const start = tl;
  const newOps: Op[] = [];
  const applied: string[] = [];
  let nextN = Number(store.nextOpId().match(/op_(\d+)$/)![1]);
  for (const t of plan.selected) {
    const conflict = plan.conflicts.find((c) => c.id === t.op_id);
    if (conflict && res[t.op_id] === "ours") continue;
    const op = conflict ? rebaseOp(tl, t) : t;
    const id = `${store.projectId}/op_${String(nextN++).padStart(4, "0")}`;
    const tch = touches(tl, op);
    const next = applyOp(tl, { ...op, op_id: id });
    const before = new Set(overlaps(tl).map((x) => `${x.track_id}:${x.a}:${x.b}`));
    const fresh = overlaps(next).filter((x) => !before.has(`${x.track_id}:${x.a}:${x.b}`));
    if (fresh.length) throw new Error(`taking ${t.op_id} would overlap ${fresh.map((f) => `${f.a}/${f.b} on ${f.track_id}`).join(", ")}`);
    tl = next;
    applied.push(t.op_id);
    newOps.push({ ...op, op_id: id, branch: into, parent_version: plan.into_head, result_version: null, author,
      on_behalf_of: null, timestamp: opts.timestamp ?? t.timestamp, touches: tch, pulled_from_op: t.op_id, session_id: null });
  }
  const short = (id: string) => id.split("/").pop();
  const kept = Object.entries(res).filter(([, v]) => v === "ours").map(([k]) => short(k)).join(", ");
  const took = Object.entries(res).filter(([, v]) => v === "theirs").map(([k]) => short(k)).join(", ");
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const picks = [took ? `took theirs on ${took}` : "", kept ? `kept ours on ${kept}` : ""].filter(Boolean).join(", ");
  const msg = opts.message ?? (plan.conflicts.length
    ? `Pulled ${plural(applied.length, "change")} from ${from} (${plural(plan.conflicts.length, "conflict")} resolved: ${picks})`
    : `Pulled ${plural(applied.length, "change")} from ${from}`);
  if (!newOps.length && !plan.conflicts.length) return { status: "nothing", plan };
  const v = store.commitOnBranch(into, tl, { author, message: msg, op_ids: newOps.map((o) => o.op_id), timestamp: opts.timestamp,
    pulled_from: { branch: from, version: plan.from_head, op_ids: applied } });
  for (const o of newOps) o.result_version = v.version_id;
  store.appendOps(newOps);
  return { status: "applied", plan, version: v, new_ops: newOps, diff: diffTimelines(start, tl) };
}
