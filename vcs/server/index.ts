/** Small Fastify API over the core library + static hosting of the built web app and renders. */
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { applyOp, diffTimelines, FLAGSHIP, listProjects, planPull, pull, renderCached, RENDERS, Store, timelineDuration,
  VCS_ROOT, type Op, type PullPlan } from "../core/index.js";

const PORT = Number(process.env.PORT ?? 4310);
const app = Fastify({ logger: false });
const stores = new Map<string, Store>();
const open = (pid: string, reset = false) => {
  if (reset || !stores.has(pid)) stores.set(pid, Store.openWorkspace(pid, { reset }));
  return stores.get(pid)!;
};

/** Sentences describing one op, computed by diffing just before/after it. */
function describeOp(s: Store, op: Op): string[] {
  try {
    const v = s.versions.get(op.result_version ?? "") ;
    let tl = s.checkout(op.parent_version);
    if (v) for (const id of v.op_ids) { if (id === op.op_id) break; tl = applyOp(tl, s.opsById.get(id)!); }
    const after = applyOp(tl, op);
    const d = diffTimelines(tl, after).map((x) => x.sentence);
    return d.length ? d : [`${op.type.replace(/_/g, " ")}`];
  } catch {
    return [`${op.type.replace(/_/g, " ")}`];
  }
}
const opView = (s: Store, op: Op) => ({ op_id: op.op_id, short: op.op_id.split("/").pop(), type: op.type, author: op.author,
  branch: op.branch, touches: op.touches, sentences: describeOp(s, op) });

function planView(s: Store, plan: PullPlan) {
  return {
    into: plan.into, from: plan.from, into_head: plan.into_head, from_head: plan.from_head, common_ancestor: plan.common_ancestor,
    expected: plan.expected, skipped_already_pulled: plan.skipped_already_pulled,
    theirs: plan.theirs_ops.map((o) => ({ ...opView(s, o), status: plan.conflicts.some((c) => c.id === o.op_id) ? "conflict" : "auto" })),
    conflicts: plan.conflicts.map((c) => ({ id: c.id, short: c.id.split("/").pop(), reasons: c.reasons,
      theirs: opView(s, c.theirs), ours: c.ours.map((o) => opView(s, o)) })),
    timelines: { base: plan.common_ancestor ? s.checkout(plan.common_ancestor) : null, ours: s.checkout(plan.into_head),
      theirs: s.checkout(plan.from_head), preview: plan.preview },
  };
}

app.get("/api/projects", async () => listProjects());
app.get<{ Params: { pid: string } }>("/api/p/:pid/state", async (req) => {
  const s = open(req.params.pid);
  const versions = [...s.versions.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.version_id.localeCompare(b.version_id));
  return { project_id: s.projectId, refs: s.refs, versions, flagship: s.projectId === FLAGSHIP };
});
app.get<{ Params: { pid: string; ref: string } }>("/api/p/:pid/version/:ref", async (req) => {
  const s = open(req.params.pid);
  const v = s.version(decodeURIComponent(req.params.ref));
  const tl = s.checkout(v.version_id);
  return { version: v, timeline: tl, duration: timelineDuration(tl), ops: v.op_ids.map((id) => opView(s, s.opsById.get(id)!)) };
});
app.get<{ Params: { pid: string }; Querystring: { from: string; to: string } }>("/api/p/:pid/diff", async (req) => {
  const s = open(req.params.pid);
  const a = s.checkout(req.query.from), b = s.checkout(req.query.to);
  return { from: s.resolve(req.query.from), to: s.resolve(req.query.to), items: diffTimelines(a, b), fromTimeline: a, toTimeline: b };
});
app.get<{ Params: { pid: string }; Querystring: { into: string; from: string; ops?: string } }>("/api/p/:pid/pull/plan", async (req) => {
  const s = open(req.params.pid);
  return planView(s, planPull(s, req.query.into, req.query.from, req.query.ops ? req.query.ops.split(",") : undefined));
});
app.post<{ Params: { pid: string }; Body: { into: string; from: string; opIds?: string[]; resolutions?: Record<string, "ours" | "theirs">; author?: string } }>(
  "/api/p/:pid/pull", async (req, reply) => {
    const s = open(req.params.pid);
    try {
      const r = pull(s, req.body.into, req.body.from, { opIds: req.body.opIds, resolutions: req.body.resolutions, author: req.body.author });
      return { status: r.status, version: r.version, diff: r.diff, plan: r.status === "conflict" ? planView(s, r.plan) : undefined };
    } catch (e: any) {
      reply.code(400);
      return { error: e.message };
    }
  });
app.post<{ Params: { pid: string }; Body: { ref: string } }>("/api/p/:pid/render", async (req, reply) => {
  const s = open(req.params.pid);
  try {
    const v = s.version(req.body.ref);
    const r = renderCached(s.checkout(v.version_id), v.timeline_hash);
    return { url: `/renders/cache/${v.timeline_hash}.mp4`, duration: r.duration, version_id: v.version_id };
  } catch (e: any) {
    reply.code(500);
    return { error: e.message };
  }
});
app.post<{ Params: { pid: string } }>("/api/p/:pid/reset", async (req) => {
  open(req.params.pid, true);
  return { ok: true };
});
app.get("/api/demo-renders", async () => {
  const f = join(RENDERS, "demo", "renders.json");
  return existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : { renders: [] };
});

await app.register(fastifyStatic, { root: RENDERS, prefix: "/renders/", decorateReply: false });
const dist = join(VCS_ROOT, "web", "dist");
if (existsSync(dist)) {
  await app.register(fastifyStatic, { root: dist, prefix: "/" });
  app.setNotFoundHandler((req, reply) => (req.url.startsWith("/api") ? reply.code(404).send({ error: "not found" }) : reply.sendFile("index.html")));
}
await app.listen({ port: PORT, host: "0.0.0.0" });
console.log(`timeline VCS API + UI on http://localhost:${PORT}`);
