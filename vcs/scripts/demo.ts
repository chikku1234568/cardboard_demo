/**
 * End-to-end demo script on a fresh workspace copy of the flagship project:
 *   1. render base, A, B
 *   2. pull B into A (clean, auto-applied)        -> render A+B
 *   3. pull C into A (conflict on clp_0003 trim)  -> resolve "theirs" -> render
 * Writes vcs/renders/demo/*.mp4 and renders.json (file, version, duration).
 */
import { statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { diffTimelines, FLAGSHIP, planPull, pull, render, RENDERS, Store, WORKSPACE } from "../core/index.js";

const s = Store.openWorkspace(FLAGSHIP, { reset: true, base: join(WORKSPACE, "demo-script") });
const out = join(RENDERS, "demo");
const rows: any[] = [];
const go = (label: string, file: string, ref: string) => {
  const v = s.version(ref);
  const r = render(s.checkout(ref), join(out, file));
  rows.push({ label, file: `renders/demo/${file}`, branch: v.branch, version_id: v.version_id, timeline_hash: v.timeline_hash,
    duration_s: r.duration, bytes: statSync(r.file).size });
  console.log(`${label.padEnd(28)} ${file.padEnd(26)} ${r.duration}s  ${v.version_id.slice(0, 10)}`);
};
go("base (rough-cut)", "01_base.mp4", "rough-cut");
go("A ava/tighter-cut", "02_A_tighter-cut.mp4", "ava/tighter-cut");
go("B cara/titles-broll", "03_B_titles-broll.mp4", "cara/titles-broll");

const ab = pull(s, "ava/tighter-cut", "cara/titles-broll", { author: "ava", timestamp: "2026-10-17T10:00:00+05:30" });
console.log(`\npull B -> A: ${ab.status}`);
for (const d of ab.diff ?? []) console.log(`  • ${d.sentence}`);
go("A + B (selective pull)", "04_A+B_pulled.mp4", "ava/tighter-cut");

const plan = planPull(s, "ava/tighter-cut", "ben/alt-pacing");
console.log(`\npull C -> A: ${plan.expected}; conflicts: ${plan.conflicts.map((c) => `${c.theirs.op_id.split("/").pop()} vs ${c.ours.map((o) => o.op_id.split("/").pop())}`)}`);
const res = pull(s, "ava/tighter-cut", "ben/alt-pacing", { author: "ava", resolutions: { op_0006: "theirs" }, timestamp: "2026-10-17T10:05:00+05:30" });
for (const d of res.diff ?? []) console.log(`  • ${d.sentence}`);
go("A + B + C (conflict resolved)", "05_A+B+C_resolved.mp4", "ava/tighter-cut");

console.log("\nbase -> final:");
for (const d of diffTimelines(s.checkout("rough-cut"), s.checkout("ava/tighter-cut"))) console.log(`  • ${d.sentence}`);
writeFileSync(join(out, "renders.json"), JSON.stringify({ project_id: FLAGSHIP, renders: rows }, null, 2) + "\n");
