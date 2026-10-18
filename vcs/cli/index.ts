#!/usr/bin/env tsx
/**
 * vcs: command-line front end for the timeline VCS.
 * Works on a workspace copy of a project (vcs/.workspace), never on data/.
 *
 *   vcs projects
 *   vcs branches                      [-p project]
 *   vcs log <branch|version>          [-p project]
 *   vcs diff <from> <to>              [--json]
 *   vcs branch <name> --from <ref>    [--author who]
 *   vcs checkout <ref>                [-o timeline.json]
 *   vcs pull --into <branch> --from <branch> [--ops op_0003,op_0004] [--dry-run] [--author who]
 *   vcs resolve --into <branch> --from <branch> --pick op_0006=theirs[,op_x=ours]
 *   vcs render <ref> [-o out.mp4]
 *   vcs reset                         (re-copy the project from data/)
 * Refs: branch name, branch~N, or a version id prefix (6+ chars).
 */
process.stdout.on("error", (e: any) => { if (e.code === "EPIPE") process.exit(0); throw e; });
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { diffTimelines, FLAGSHIP, listProjects, planPull, pull, render, RENDERS, Store, tc, timelineDuration,
  type PullPlan, type Resolution } from "../core/index.js";

const argv = process.argv.slice(2);
const flags: Record<string, string | true> = {};
const pos: string[] = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a.startsWith("--") || a === "-p" || a === "-o") {
    const k = a.replace(/^-+/, "");
    const nxt = argv[i + 1];
    if (nxt !== undefined && !nxt.startsWith("-")) { flags[k] = nxt; i++; } else flags[k] = true;
  } else pos.push(a);
}
const cmd = pos.shift();
const pid = (flags.p ?? flags.project ?? FLAGSHIP) as string;
const short = (id: string) => id.split("/").pop()!;
const vshort = (v: string) => v.slice(0, 10);
const store = () => Store.openWorkspace(pid);

function printPlan(plan: PullPlan) {
  console.log(`pull ${plan.from} -> ${plan.into}   (common ancestor ${plan.common_ancestor ? vshort(plan.common_ancestor) : "none"})`);
  if (plan.skipped_already_pulled.length) console.log(`  already pulled, skipped: ${plan.skipped_already_pulled.map(short).join(", ")}`);
  for (const o of plan.auto) console.log(`  auto   ${short(o.op_id)}  ${o.type}`);
  for (const c of plan.conflicts) {
    const why = c.reasons.map((r) => r.kind === "same_entity" ? `same ${r.entities!.filter((e) => !e.startsWith("lineage")).join(", ")}`
      : r.kind === "overlapping_time_range" ? `overlap ${r.track_id} ${tc(r.overlap![0])}–${tc(r.overlap![1])}`
      : `clips would overlap on ${r.track_id}`).join("; ");
    console.log(`  CONFLICT theirs ${short(c.theirs.op_id)} (${c.theirs.type}) vs ours ${c.ours.map((o) => `${short(o.op_id)} (${o.type})`).join(", ") || "-"}: ${why}`);
  }
  console.log(`  => ${plan.expected}`);
}

function main() {
  switch (cmd) {
    case "projects":
      for (const p of listProjects()) console.log(`${p.project_id.padEnd(26)} ${p.owner_subject.padEnd(15)} ${p.project_scope.padEnd(8)} ${p.n_versions} versions${p.flagship ? "  <- flagship" : ""}`);
      break;
    case "branches": {
      const s = store();
      for (const [b, r] of Object.entries(s.refs)) {
        const v = s.versions.get(r.head)!;
        console.log(`${b.padEnd(22)} head ${vshort(r.head)}  from ${r.created_from ? vshort(r.created_from) : "(root)    "}  ${v.author.padEnd(8)} ${v.message}`);
      }
      console.log("(no main: every branch is a peer)");
      break;
    }
    case "log": {
      const s = store();
      for (const v of s.log(pos[0] ?? "rough-cut")) {
        console.log(`${vshort(v.version_id)}  ${v.timestamp}  ${v.branch.padEnd(20)} ${v.author.padEnd(8)} ${v.message}`);
        for (const id of v.op_ids) console.log(`             ${short(id)} ${s.opsById.get(id)?.type ?? "?"}`);
      }
      break;
    }
    case "diff": {
      const s = store();
      const [a, b] = pos;
      if (!a || !b) throw new Error("usage: vcs diff <from> <to>");
      const d = diffTimelines(s.checkout(a), s.checkout(b));
      if (flags.json) console.log(JSON.stringify(d, null, 2));
      else {
        console.log(`${a} (${vshort(s.resolve(a))}) -> ${b} (${vshort(s.resolve(b))}): ${d.length} change(s)`);
        for (const x of d) console.log(`  • ${x.sentence}`);
      }
      break;
    }
    case "branch": {
      const s = store();
      const r = s.branch(pos[0], (flags.from as string) ?? "rough-cut", (flags.author as string) ?? "ava");
      console.log(`created ${pos[0]} at ${vshort(r.head)}`);
      break;
    }
    case "checkout": {
      const s = store();
      const tl = s.checkout(pos[0]);
      if (flags.o) { writeFileSync(flags.o as string, JSON.stringify(tl, null, 2)); console.log(`wrote ${flags.o}`); }
      else console.log(JSON.stringify(tl, null, 2));
      break;
    }
    case "pull":
    case "resolve": {
      const s = store();
      const into = flags.into as string, from = flags.from as string;
      if (!into || !from) throw new Error(`usage: vcs ${cmd} --into <branch> --from <branch>`);
      const opIds = typeof flags.ops === "string" ? flags.ops.split(",") : undefined;
      const resolutions: Record<string, Resolution> = {};
      if (typeof flags.pick === "string") for (const kv of flags.pick.split(",")) {
        const [k, v] = kv.split("=");
        if (v !== "ours" && v !== "theirs") throw new Error(`--pick ${kv}: use ours|theirs`);
        resolutions[k] = v;
      }
      const plan = planPull(s, into, from, opIds);
      printPlan(plan);
      if (flags["dry-run"]) break;
      const r = pull(s, into, from, { opIds, resolutions, author: (flags.author as string) ?? "ava" });
      if (r.status === "conflict") { console.log(`\nnot applied: resolve with  vcs resolve --into ${into} --from ${from} --pick ${r.plan.conflicts.map((c) => `${short(c.id)}=theirs`).join(",")}`); process.exitCode = 2; break; }
      if (r.status === "nothing") { console.log("nothing to pull"); break; }
      console.log(`\nnew version ${vshort(r.version!.version_id)} on ${into}: ${r.version!.message}`);
      for (const x of r.diff!) console.log(`  • ${x.sentence}`);
      break;
    }
    case "render": {
      const s = store();
      const ref = pos[0] ?? "rough-cut";
      const v = s.version(ref);
      const out = (flags.o as string) ?? join(RENDERS, "cache", `${v.timeline_hash}.mp4`);
      const tl = s.checkout(ref);
      const r = render(tl, out);
      console.log(`rendered ${ref} (${vshort(v.version_id)}) -> ${r.file}  ${r.duration}s (timeline ${timelineDuration(tl)}s)`);
      break;
    }
    case "reset":
      Store.openWorkspace(pid, { reset: true });
      console.log(`workspace for ${pid} reset from data/`);
      break;
    default:
      console.log(readUsage());
  }
}
function readUsage() {
  return `usage: vcs <projects|branches|log|diff|branch|checkout|pull|resolve|render|reset> [-p project]`;
}
try { main(); } catch (e: any) { console.error(`error: ${e.message}`); process.exitCode = 1; }
