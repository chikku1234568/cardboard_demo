/**
 * Read/write the content-addressed store format in data/vcs/projects/<pid>/:
 *   store/objects/<sha256>.json   canonical timeline bytes (sha256(file) == name)
 *   store/versions/<sha256>.json  version meta (id = sha256(canonical(meta without version_id)))
 *   store/refs.json               branches -> {head, created_from, ...}; no main
 *   ops.jsonl                     op log
 * The generated data in data/ is never modified: commands work on a copy in
 * vcs/.workspace/projects/<pid> (created on first use, `reset` re-copies).
 */
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { canonical, contentHash, pretty, sha256 } from "./canonical.js";
import { DATA_PROJECTS, WORKSPACE } from "./paths.js";
import type { Op, Ref, Timeline, Version } from "./types.js";

export function listProjects(): { project_id: string; flagship: boolean; owner_subject: string; project_scope: string; n_versions: number }[] {
  return JSON.parse(readFileSync(join(DATA_PROJECTS, "index.json"), "utf8")).projects;
}

export function workspaceDir(pid: string, opts: { reset?: boolean; base?: string } = {}): string {
  const base = opts.base ?? WORKSPACE;
  const dst = join(base, "projects", pid);
  if (opts.reset && existsSync(dst)) rmSync(dst, { recursive: true, force: true });
  if (!existsSync(dst)) {
    const src = join(DATA_PROJECTS, pid);
    if (!existsSync(src)) throw new Error(`unknown project ${pid}`);
    mkdirSync(join(base, "projects"), { recursive: true });
    cpSync(src, dst, { recursive: true });
  }
  return dst;
}

export class Store {
  readonly dir: string;
  readonly projectId: string;
  versions = new Map<string, Version>();
  refs: Record<string, Ref> = {};
  ops: Op[] = [];
  opsById = new Map<string, Op>();
  private objCache = new Map<string, Timeline>();
  private readonly writable: boolean;

  /** Open a project store directory (data dir read-only, or a workspace copy). */
  constructor(dir: string, opts: { writable?: boolean } = {}) {
    this.dir = dir;
    this.writable = opts.writable ?? true;
    const refs = JSON.parse(readFileSync(join(dir, "store", "refs.json"), "utf8"));
    this.projectId = refs.project_id;
    this.refs = refs.branches;
    for (const f of readdirSync(join(dir, "store", "versions"))) {
      const v = JSON.parse(readFileSync(join(dir, "store", "versions", f), "utf8")) as Version;
      this.versions.set(v.version_id, v);
    }
    const opsTxt = existsSync(join(dir, "ops.jsonl")) ? readFileSync(join(dir, "ops.jsonl"), "utf8") : "";
    for (const line of opsTxt.split("\n")) if (line.trim()) this.ops.push(JSON.parse(line));
    for (const o of this.ops) this.opsById.set(o.op_id, o);
  }

  static openWorkspace(pid: string, opts: { reset?: boolean; base?: string } = {}): Store {
    return new Store(workspaceDir(pid, opts));
  }
  static openData(pid: string): Store {
    return new Store(join(DATA_PROJECTS, pid), { writable: false });
  }

  objectPath(hash: string) {
    return join(this.dir, "store", "objects", `${hash}.json`);
  }
  readObjectBytes(hash: string): Buffer {
    return readFileSync(this.objectPath(hash));
  }
  timelineByHash(hash: string): Timeline {
    let tl = this.objCache.get(hash);
    if (!tl) {
      tl = JSON.parse(this.readObjectBytes(hash).toString("utf8")) as Timeline;
      this.objCache.set(hash, tl);
    }
    return structuredClone(tl);
  }
  /** checkout: the timeline of a version (or branch / prefix / branch~N). */
  checkout(ref: string): Timeline {
    return this.timelineByHash(this.version(ref).timeline_hash);
  }
  version(ref: string): Version {
    return this.versions.get(this.resolve(ref))!;
  }

  /** Resolve a branch name, `branch~N`, full version id or unique prefix (>= 6 chars). */
  resolve(ref: string): string {
    const m = ref.match(/^(.*)~(\d+)$/);
    if (m) {
      let v = this.resolve(m[1]);
      for (let i = 0; i < Number(m[2]); i++) {
        const ps = this.versions.get(v)!.parents;
        if (!ps.length) throw new Error(`${ref}: no such ancestor`);
        v = ps[0];
      }
      return v;
    }
    if (this.refs[ref]) return this.refs[ref].head;
    if (this.versions.has(ref)) return ref;
    if (ref.length >= 6) {
      const hits = [...this.versions.keys()].filter((k) => k.startsWith(ref));
      if (hits.length === 1) return hits[0];
      if (hits.length > 1) throw new Error(`ambiguous version prefix ${ref}`);
    }
    throw new Error(`unknown ref ${ref} in ${this.projectId}`);
  }

  /** First-parent chain, newest first. */
  chain(vid: string): string[] {
    const out: string[] = [];
    let v: string | undefined = vid;
    while (v) {
      out.push(v);
      v = this.versions.get(v)!.parents[0];
    }
    return out;
  }
  log(ref: string): Version[] {
    return this.chain(this.resolve(ref)).map((v) => this.versions.get(v)!);
  }
  commonAncestor(a: string, b: string): string | null {
    const cb = new Set(this.chain(this.resolve(b)));
    return this.chain(this.resolve(a)).find((v) => cb.has(v)) ?? null;
  }
  /** Ops on the first-parent chain of `ref` after `ancestor` (oldest first). */
  opsSince(ref: string, ancestor: string | null): Op[] {
    const ch = this.chain(this.resolve(ref));
    const stop = ancestor ? ch.indexOf(ancestor) : ch.length;
    return ch.slice(0, stop < 0 ? ch.length : stop).reverse()
      .flatMap((v) => this.versions.get(v)!.op_ids.map((id) => this.opsById.get(id)!));
  }

  // ------------------------------------------------------------- writes
  private assertWritable() {
    if (!this.writable) throw new Error("this store is the generated data (read-only); use a workspace copy");
  }
  putTimeline(tl: Timeline): string {
    const bytes = canonical(tl);
    const h = sha256(Buffer.from(bytes, "utf8"));
    if (!existsSync(this.objectPath(h))) {
      this.assertWritable();
      writeFileSync(this.objectPath(h), bytes, "utf8");
    }
    return h;
  }
  commit(tl: Timeline, args: { parents: string[]; branch: string; author: string; message: string; op_ids?: string[];
    session_id?: string | null; timestamp?: string; pulled_from?: Version["pulled_from"] }): Version {
    this.assertWritable();
    const th = this.putTimeline(tl);
    const meta: Omit<Version, "version_id"> = {
      project_id: this.projectId, timeline_hash: th, parents: args.parents, branch: args.branch, author: args.author,
      timestamp: args.timestamp ?? nowIST(), message: args.message, op_ids: args.op_ids ?? [],
      session_id: args.session_id ?? null, ...(args.pulled_from ? { pulled_from: args.pulled_from } : {}),
    };
    const vid = contentHash(meta);
    const v = { version_id: vid, ...meta } as Version;
    writeFileSync(join(this.dir, "store", "versions", `${vid}.json`), pretty(v) + "\n", "utf8");
    this.versions.set(vid, v);
    return v;
  }
  /** Commit on a branch: parent = branch head, then advance the ref. */
  commitOnBranch(branch: string, tl: Timeline, args: { author: string; message: string; op_ids?: string[];
    timestamp?: string; pulled_from?: Version["pulled_from"] }): Version {
    const head = this.resolve(branch);
    const v = this.commit(tl, { ...args, parents: [head], branch });
    this.refs[branch].head = v.version_id;
    this.writeRefs();
    return v;
  }
  /** Branch from ANY version (there is no main). */
  branch(name: string, from: string, author: string): Ref {
    this.assertWritable();
    if (this.refs[name]) throw new Error(`branch exists: ${name}`);
    const vid = this.resolve(from);
    this.refs[name] = { head: vid, created_from: vid, created_by: author, created_at: nowIST() };
    this.writeRefs();
    return this.refs[name];
  }
  writeRefs() {
    this.assertWritable();
    writeFileSync(join(this.dir, "store", "refs.json"), pretty({ project_id: this.projectId,
      note: "Branch refs. No main: every branch is a peer.", branches: this.refs }) + "\n", "utf8");
  }
  nextOpId(): string {
    let max = 0;
    for (const o of this.ops) {
      const m = o.op_id.match(/op_(\d+)$/);
      if (m) max = Math.max(max, Number(m[1]));
    }
    return `${this.projectId}/op_${String(max + 1).padStart(4, "0")}`;
  }
  appendOps(ops: Op[]) {
    this.assertWritable();
    for (const o of ops) {
      this.ops.push(o);
      this.opsById.set(o.op_id, o);
    }
    appendFileSync(join(this.dir, "ops.jsonl"), ops.map((o) => JSON.stringify(o)).join("\n") + (ops.length ? "\n" : ""), "utf8");
  }
  /** Verify object/version hashes for the whole store. */
  verify(): string[] {
    const errs: string[] = [];
    for (const f of readdirSync(join(this.dir, "store", "objects"))) {
      const h = f.replace(/\.json$/, "");
      const bytes = this.readObjectBytes(h);
      if (sha256(bytes) !== h) errs.push(`object bytes != name ${h.slice(0, 12)}`);
      if (canonical(JSON.parse(bytes.toString("utf8"))) !== bytes.toString("utf8")) errs.push(`re-serialisation differs ${h.slice(0, 12)}`);
    }
    for (const [vid, v] of this.versions) {
      const { version_id, ...meta } = v;
      if (contentHash(meta) !== vid) errs.push(`version id mismatch ${vid.slice(0, 12)}`);
    }
    return errs;
  }
}

export function nowIST(): string {
  const d = new Date(Date.now() + 5.5 * 3600e3);
  return d.toISOString().replace(/\.\d{3}Z$/, "+05:30");
}
