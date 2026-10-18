import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { contentHash, planPull, pull, Store, DATA_PROJECTS, FLAGSHIP, findClip } from "../core/index.js";
import { freshStore } from "./helpers.js";

const scenario = JSON.parse(readFileSync(join(DATA_PROJECTS, FLAGSHIP, "scenario.json"), "utf8"));
const expectation = (from: string, into: string) =>
  scenario.selective_pull_expectations.find((p: any) => p.from === from && p.into === into);
const short = (id: string) => id.split("/").pop();

describe("selective pull reproduces the flagship expectations", () => {
  const cases: [string, string, string][] = [
    ["cara/titles-broll", "ava/tighter-cut", "clean"],     // B -> A
    ["ben/alt-pacing", "ava/tighter-cut", "conflict"],     // C -> A
    ["cara/titles-broll", "ava/alt-ending", "clean"],      // B -> D
    ["ben/alt-pacing", "cara/titles-broll", "clean"],      // C -> B
  ];
  for (const [from, into, want] of cases) {
    it(`${from} -> ${into} is ${want}`, () => {
      const s = Store.openData(FLAGSHIP);
      const plan = planPull(s, into, from);
      const exp = expectation(from, into);
      expect(plan.expected).toBe(want);
      expect(plan.expected).toBe(exp.expected);
      expect(plan.common_ancestor).toBe(exp.common_ancestor);
      expect(plan.ours_ops.map((o) => o.op_id)).toEqual(exp.ours_ops);
      expect(plan.theirs_ops.map((o) => o.op_id)).toEqual(exp.theirs_ops);
      const pairs = plan.conflicts.flatMap((c) => c.pairs);
      expect(pairs).toEqual(exp.conflicts);
      if (want === "clean") {
        const expected = JSON.parse(readFileSync(join(DATA_PROJECTS, FLAGSHIP, exp.result_file), "utf8"));
        expect(plan.preview).toEqual(expected);
        expect(contentHash(plan.preview)).toBe(exp.result_timeline_hash);
      }
    });
  }

  it("C -> A conflicts on op_0001 (ours) vs op_0006 (theirs): same clip clp_0003, overlapping 13.4-14.0s on V1", () => {
    const plan = planPull(Store.openData(FLAGSHIP), "ava/tighter-cut", "ben/alt-pacing");
    expect(plan.conflicts).toHaveLength(1);
    const c = plan.conflicts[0];
    expect(short(c.pairs[0].ours)).toBe("op_0001");
    expect(short(c.pairs[0].theirs)).toBe("op_0006");
    expect(c.reasons).toEqual([
      { kind: "same_entity", entities: ["clip:clp_0003", "lineage:clp_0003"] },
      { kind: "overlapping_time_range", track_id: "V1", overlap: [13.4, 14.0] },
    ]);
    expect(plan.auto.map((o) => short(o.op_id))).toEqual(["op_0007"]);   // the music duck auto-applies
  });

  it("a clean pull writes a new version on the target branch with the expected timeline", () => {
    const s = freshStore();
    const head = s.resolve("ava/tighter-cut");
    const r = pull(s, "ava/tighter-cut", "cara/titles-broll", { author: "ava", timestamp: "2026-10-17T10:00:00+05:30" });
    expect(r.status).toBe("applied");
    expect(r.version!.parents).toEqual([head]);
    expect(s.resolve("ava/tighter-cut")).toBe(r.version!.version_id);
    expect(r.version!.timeline_hash).toBe(expectation("cara/titles-broll", "ava/tighter-cut").result_timeline_hash);
    expect(r.new_ops!.map((o) => short(o.pulled_from_op!))).toEqual(["op_0004", "op_0005", "op_0003"].sort());
    expect(new Store(s.dir).verify()).toEqual([]);
    // pulling the same branch again is a no-op (already pulled ops are skipped)
    const again = planPull(s, "ava/tighter-cut", "cara/titles-broll");
    expect(again.selected).toHaveLength(0);
    expect(again.skipped_already_pulled).toHaveLength(3);
  });

  it("a conflicting pull stops without resolutions, and resolves to a new version", () => {
    const s = freshStore();
    const stopped = pull(s, "ava/tighter-cut", "ben/alt-pacing");
    expect(stopped.status).toBe("conflict");
    const headA = s.resolve("ava/tighter-cut");

    const theirs = pull(s, "ava/tighter-cut", "ben/alt-pacing", { resolutions: { op_0006: "theirs" }, author: "ava" });
    expect(theirs.status).toBe("applied");
    const tl = s.checkout(theirs.version!.version_id);
    expect(findClip(tl, "clp_0003")[1].src_out).toBe(6.4);          // ben's trim wins
    expect(findClip(tl, "clp_0009")[1].volume).toBe(0.5);           // non-conflicting duck auto-applied
    expect(tl.transitions.find((t) => t.id === "tr_01")!.type).toBe("whip_pan"); // ours kept
    expect(theirs.version!.parents).toEqual([headA]);
  });

  it("keeping ours skips their conflicting op but still applies the rest", () => {
    const s = freshStore();
    const r = pull(s, "ava/tighter-cut", "ben/alt-pacing", { resolutions: { op_0006: "ours" } });
    const tl = s.checkout(r.version!.version_id);
    expect(findClip(tl, "clp_0003")[1].src_out).toBe(5.8);
    expect(findClip(tl, "clp_0009")[1].volume).toBe(0.5);
  });

  it("cherry-picks a single op", () => {
    const s = freshStore();
    const r = pull(s, "ava/tighter-cut", "cara/titles-broll", { opIds: ["op_0003"] });
    expect(r.status).toBe("applied");
    expect(r.new_ops).toHaveLength(1);
    expect(s.checkout("ava/tighter-cut").text_layers.map((t) => t.id)).toEqual(["txt_01", "txt_02"]);
  });

  it("different clips whose results overlap on the same track conflict", () => {
    const s = freshStore();
    s.branch("x/ours", "rough-cut", "ava");
    s.branch("x/theirs", "rough-cut", "ben");
    const mk = (branch: string, clip: string, from: number, to: number) => {
      const base = s.checkout(branch);
      const op: any = { op_id: s.nextOpId(), project_id: s.projectId, session_id: null, branch, parent_version: s.resolve(branch),
        result_version: null, author: "ava", on_behalf_of: null, timestamp: "2026-10-17T12:00:00+05:30", type: "move_clip",
        decision_id: null, context: {}, replaces_op: null, reverts_op: null, accepts_op: null,
        payload: { clip_id: clip, before: { track_id: "V2", timeline_start: from }, after: { track_id: "V2", timeline_start: to } } };
      return import("../core/index.js").then(({ applyOp, touches }) => {
        op.touches = touches(base, op);
        const v = s.commitOnBranch(branch, applyOp(base, op), { author: "ava", message: "move", op_ids: [op.op_id] });
        op.result_version = v.version_id;
        s.appendOps([op]);
      });
    };
    return mk("x/ours", "clp_0007", 5.0, 12.0).then(() => mk("x/theirs", "clp_0008", 15.5, 13.0)).then(() => {
      const plan = planPull(s, "x/ours", "x/theirs");
      expect(plan.expected).toBe("conflict");
      expect(plan.conflicts[0].reasons.map((r) => r.kind)).toContain("overlapping_time_range");
      expect(plan.conflicts[0].reasons.some((r) => r.kind === "same_entity")).toBe(false);
    });
  });
});
