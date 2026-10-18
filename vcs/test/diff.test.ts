import { describe, expect, it } from "vitest";
import { diffTimelines, Store, FLAGSHIP } from "../core/index.js";

const s = Store.openData(FLAGSHIP);
const sentences = (a: string, b: string) => diffTimelines(s.checkout(a), s.checkout(b)).map((d) => d.sentence);
const kinds = (a: string, b: string) => diffTimelines(s.checkout(a), s.checkout(b)).map((d) => d.kind);

describe("semantic diff reads like an editor", () => {
  it("A vs base: trim with delta and edge, transition swap", () => {
    expect(sentences("rough-cut", "ava/tighter-cut")).toEqual([
      "Trimmed 1.2s off the end of the B-cam clip at 0:09.0 (now 3.8s, ends at 0:12.8)",
      "Swapped the crossfade at 0:03.0 for a whip pan (0.5s → 0.3s)",
    ]);
  });
  it("B vs base: move on another track, text add + edit", () => {
    expect(sentences("rough-cut", "cara/titles-broll")).toEqual([
      "Moved the product B-roll clip 1.5s later (0:15.5 → 0:17.0) on V2",
      "Changed the title text from “Launch Week Recap” to “Launch Week: Recap”",
      "Added a lower-third “Shipping in 3 days” at 0:15.6 for 2.5s (Inter)",
    ]);
  });
  it("C vs base: trim + volume", () => {
    expect(sentences("rough-cut", "ben/alt-pacing")).toEqual([
      "Trimmed 0.6s off the end of the B-cam clip at 0:09.0 (now 4.4s, ends at 0:13.4)",
      "Lowered the volume of the upbeat music from 80% to 50%",
    ]);
  });
  it("D vs A: split (lineage), add clip, transition add, effect add/update", () => {
    const got = sentences("ava/tighter-cut", "ava/alt-ending");
    expect(got).toContain("Split the B-cam clip (0:25.0–0:29.0) at 0:27.0 and dropped the 2.0s tail, so it now ends at 0:27.0");
    expect(got).toContain("Added the desk B-roll on V2 at 0:22.0 (3.0s)");
    expect(got).toContain("Added a slide transition at 0:19.0 (A-cam → screen recording, 0.4s)");
    expect(got).toContain("Raised the intensity of the warm film grade on the A-cam clip at 0:03.0 from 0.6 to 0.8");
    expect(got).toContain("Added a teal/orange grade to the desk B-roll clip at 0:22.0 (intensity 0.5)");
  });
  it("covers 5+ edit types across the flagship and is empty for identical versions", () => {
    const all = new Set([...kinds("rough-cut", "ava/tighter-cut"), ...kinds("rough-cut", "cara/titles-broll"),
      ...kinds("rough-cut", "ben/alt-pacing"), ...kinds("ava/tighter-cut", "ava/alt-ending")]);
    expect(all.size).toBeGreaterThanOrEqual(8);
    expect(diffTimelines(s.checkout("rough-cut"), s.checkout("rough-cut"))).toEqual([]);
  });
  it("diffs any two versions, not just parent/child (B vs C)", () => {
    const got = sentences("cara/titles-broll", "ben/alt-pacing");
    expect(got.length).toBeGreaterThan(3);
  });
  it("describes music swaps, caption style, font and format changes from generated sessions", () => {
    const kindsSeen = new Set<string>();
    for (const pid of ["proj_ava_07", "proj_ben_05", "proj_team_northwind_03"]) {
      const w = Store.openData(pid);
      for (const b of Object.keys(w.refs)) for (const d of diffTimelines(w.checkout("rough-cut"), w.checkout(b))) kindsSeen.add(d.kind);
    }
    for (const k of ["format_changed", "clip_added", "clip_trimmed", "transition_added", "text_added", "effect_added"]) expect(kindsSeen).toContain(k);
  });
});
