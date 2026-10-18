import { describe, expect, it } from "vitest";
import { applyOp, contentHash, listProjects, Store } from "../core/index.js";
import { freshStore } from "./helpers.js";

describe("store format compatibility (Python generators <-> TS core)", () => {
  it("re-serialises every stored object and version to the same hash, in all projects", () => {
    let objects = 0;
    for (const p of listProjects()) {
      const s = Store.openData(p.project_id);
      expect(s.verify(), p.project_id).toEqual([]);
      objects += s.versions.size;
    }
    expect(objects).toBeGreaterThan(900);
  });

  it("replays every op log with the TS op engine and reproduces every snapshot hash", () => {
    let replayed = 0;
    for (const p of listProjects()) {
      const s = Store.openData(p.project_id);
      for (const v of s.versions.values()) {
        if (!v.parents.length) continue;
        let tl = s.checkout(v.parents[0]);
        for (const id of v.op_ids) tl = applyOp(tl, s.opsById.get(id)!);
        expect(contentHash(tl), `${p.project_id} ${v.version_id}`).toBe(v.timeline_hash);
        replayed++;
      }
    }
    expect(replayed).toBeGreaterThan(900);
  });

  it("has no main branch and every ref resolves", () => {
    for (const p of listProjects()) {
      const s = Store.openData(p.project_id);
      expect(Object.keys(s.refs)).not.toContain("main");
      for (const b of Object.keys(s.refs)) expect(s.versions.has(s.resolve(b))).toBe(true);
    }
  });

  it("branches from any version, commits, logs and checks out", () => {
    const s = freshStore();
    const a1 = s.resolve("ava/tighter-cut~1");            // A's first commit (the trim), not a head
    s.branch("sam/from-a1", a1, "ben");
    const tl = s.checkout("sam/from-a1");
    tl.text_layers[0].text = "Launch Week (draft)";
    const v = s.commitOnBranch("sam/from-a1", tl, { author: "ben", message: "draft title" });
    expect(v.parents).toEqual([a1]);
    expect(s.log("sam/from-a1").map((x) => x.version_id)).toEqual([v.version_id, ...s.chain(a1)]);
    const reopened = new Store(s.dir);
    expect(reopened.checkout("sam/from-a1").text_layers[0].text).toBe("Launch Week (draft)");
    expect(reopened.verify()).toEqual([]);
    expect(() => s.branch("sam/from-a1", a1, "ben")).toThrow();
    expect(() => Store.openData("proj_launch_week_recap").commitOnBranch("rough-cut", tl, { author: "x", message: "y" })).toThrow();
  });

  it("keeps stable clip ids with lineage through a split", () => {
    const s = Store.openData("proj_launch_week_recap");
    const tl = s.checkout("ava/alt-ending");
    const kid = tl.tracks.find((t) => t.id === "V1")!.clips.find((c) => c.id === "clp_0011")!;
    expect(kid.lineage).toMatchObject({ origin: "clp_0006", parent: "clp_0006" });
    expect(tl.tracks.flatMap((t) => t.clips).some((c) => c.id === "clp_0003")).toBe(true);
  });
});
