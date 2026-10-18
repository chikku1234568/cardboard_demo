import { useCallback, useEffect, useMemo, useState } from "react";
import { api, BRANCH_COLORS, tc, type DiffItem, type OpView, type PlanView, type Ref, type Timeline, type Version } from "./api";
import { ConflictView } from "./ConflictView";
import { Graph } from "./Graph";
import { PullDialog } from "./PullDialog";
import { duration, Strip } from "./Strip";

const q = new URLSearchParams(location.search);
const ICON: Record<string, string> = { clip_trimmed: "✂", clip_moved: "↔", clip_split: "⫽", clip_deleted: "✕", clip_added: "＋",
  transition_added: "⤫", transition_changed: "⤫", transition_removed: "⤫", text_added: "T", text_edited: "T", text_style_changed: "T",
  text_retimed: "T", text_removed: "T", effect_added: "◐", effect_updated: "◐", effect_removed: "◐", volume_changed: "🔉",
  music_replaced: "♪", track_added: "≡", format_changed: "▭", speed_changed: "⏩" };

export function App() {
  const [projects, setProjects] = useState<any[]>([]);
  const [pid, setPid] = useState(q.get("p") ?? "proj_launch_week_recap");
  const [refs, setRefs] = useState<Record<string, Ref>>({});
  const [versions, setVersions] = useState<Version[]>([]);
  const [sel, setSel] = useState<string>("");
  const [cur, setCur] = useState<{ version: Version; timeline: Timeline; duration: number; ops: OpView[] } | null>(null);
  const [fromRef, setFromRef] = useState<string>("");
  const [diff, setDiff] = useState<{ items: DiffItem[]; fromTimeline: Timeline; toTimeline: Timeline } | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [pullOpen, setPullOpen] = useState(false);
  const [conflict, setConflict] = useState<{ plan: PlanView; opIds: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [video, setVideo] = useState<{ url: string; duration: number; vid: string } | null>(null);
  const [rendering, setRendering] = useState(false);
  const [author, setAuthor] = useState(q.get("as") ?? "ava");
  const [toast, setToast] = useState("");

  const colors = useMemo(() => {
    const m: Record<string, string> = {};
    Object.keys(refs).forEach((b, i) => (m[b] = BRANCH_COLORS[i % BRANCH_COLORS.length]));
    return m;
  }, [refs]);
  const branchOfHead = useMemo(() => Object.entries(refs).find(([, r]) => r.head === sel)?.[0], [refs, sel]);

  const load = useCallback(async (selectRef?: string) => {
    const st = await api.state(pid);
    setRefs(st.refs);
    setVersions(st.versions);
    const target = selectRef ?? q.get("v") ?? Object.keys(st.refs).find((b) => b !== "rough-cut") ?? "rough-cut";
    const v = await api.version(pid, target);
    setSel(v.version.version_id);
    return st;
  }, [pid]);

  useEffect(() => { api.projects().then(setProjects); }, []);
  useEffect(() => {
    setConflict(null); setVideo(null); setFromRef(q.get("from") ?? "");
    load().then(async () => {
      if (q.get("view") === "conflict" && q.get("into") && q.get("src")) {
        const plan = await api.plan(pid, q.get("into")!, q.get("src")!);
        if (plan.conflicts.length) setConflict({ plan, opIds: plan.theirs.map((o) => o.op_id) });
      }
    });
  }, [pid, load]);
  useEffect(() => {
    if (!sel) return;
    api.version(pid, sel).then((v) => {
      setCur(v);
      if (!fromRef || fromRef === sel) setFromRef(v.version.parents[0] ?? sel);
    });
    setVideo(null);
  }, [pid, sel]);
  useEffect(() => {
    if (!sel || !fromRef) return;
    api.diff(pid, fromRef, sel).then(setDiff);
  }, [pid, sel, fromRef]);

  const select = (vid: string) => { setFromRef(""); setSel(vid); };
  const renderNow = async () => {
    setRendering(true);
    try { const r = await api.render(pid, sel); setVideo({ ...r, vid: sel }); } catch (e: any) { setToast(e.message); } finally { setRendering(false); }
  };
  const afterPull = async (vid: string, msg: string) => {
    setPullOpen(false); setConflict(null);
    const prevHead = branchOfHead ? refs[branchOfHead].head : sel;
    await load(vid);
    setFromRef(prevHead);
    setToast(msg);
  };
  const resolve = async (res: Record<string, "ours" | "theirs">) => {
    if (!conflict) return;
    setBusy(true);
    try {
      const r = await api.pull(pid, { into: conflict.plan.into, from: conflict.plan.from, opIds: conflict.opIds, resolutions: res, author });
      if (r.version) {
        const prev = conflict.plan.into_head;
        setConflict(null);
        await load(r.version.version_id);
        setFromRef(prev);
        setToast(`New version ${r.version.version_id.slice(0, 10)} on ${r.version.branch}`);
      }
    } catch (e: any) { setToast(e.message); } finally { setBusy(false); }
  };

  const total = cur ? Math.ceil(Math.max(cur.duration, diff ? duration(diff.fromTimeline) : 0)) : 0;
  const hlItems = diff ? (hover !== null ? [diff.items[hover]] : diff.items) : [];
  const hl = { entities: new Set(hlItems.map((d) => d.entity)), ranges: hlItems.filter((d) => d.range).map((d) => ({ track_id: d.track_id, start: d.range![0], end: d.range![1], tone: "theirs" as const })) };
  const vById = new Map(versions.map((v) => [v.version_id, v]));
  const label = (vid: string) => { const v = vById.get(vid); return v ? `${v.branch} · ${v.message}` : vid.slice(0, 10); };

  return (
    <div className="app">
      <header>
        <div className="brand"><span className="logo">◧</span> Timeline VCS <span className="muted">· project-level versioning, no main branch</span></div>
        <div className="row">
          <select value={pid} onChange={(e) => { history.replaceState(null, "", "?p=" + e.target.value); setPid(e.target.value); }}>
            {projects.map((p) => <option key={p.project_id} value={p.project_id}>{p.flagship ? "★ " : ""}{p.project_id}</option>)}
          </select>
          <label className="muted small">acting as&nbsp;
            <select value={author} onChange={(e) => setAuthor(e.target.value)}>{["ava", "ben", "cara"].map((a) => <option key={a}>{a}</option>)}</select>
          </label>
          <button className="ghost" onClick={async () => { await api.reset(pid); setConflict(null); await load(); setToast("Workspace reset from data/"); }}>Reset demo</button>
        </div>
      </header>
      <div className="layout">
        <aside>
          <h3>Branches <span className="muted small">(peers)</span></h3>
          {Object.entries(refs).map(([b, r]) => (
            <div key={b} className={`branch ${r.head === sel ? "active" : ""}`} onClick={() => select(r.head)}>
              <span className="dot" style={{ background: colors[b] }} />
              <div>
                <div className="bname">{b}</div>
                <div className="muted small">{vById.get(r.head)?.message}</div>
                <div className="muted tiny">{r.created_from ? `branched from ${label(r.created_from).split(" · ")[0]} @ ${r.created_from.slice(0, 7)}` : "root import"}</div>
              </div>
            </div>
          ))}
          <h3>History</h3>
          <Graph versions={versions} refs={refs} colors={colors} selected={sel} onSelect={select} />
          <p className="muted tiny">Filled = branch head · dashed = selective pull source</p>
        </aside>
        <main>
          {conflict ? (
            <ConflictView plan={conflict.plan} colors={colors} busy={busy} onCancel={() => setConflict(null)} onResolve={resolve} />
          ) : cur && (
            <>
              <section className="vhead">
                <div>
                  <div className="row">
                    <span className="chip" style={{ background: colors[cur.version.branch] }}>{cur.version.branch}</span>
                    {branchOfHead && <span className="badge ok">head</span>}
                    <span className="mono muted">{cur.version.version_id.slice(0, 12)}</span>
                  </div>
                  <h2>{cur.version.message}</h2>
                  <div className="muted small">{cur.version.author} · {cur.version.timestamp.replace("T", " ").replace("+05:30", " IST")} · {tc(cur.duration)} long
                    {cur.version.pulled_from && <> · pulled from <b>{cur.version.pulled_from.branch}</b></>}</div>
                </div>
                <div className="row">
                  {branchOfHead && <button onClick={() => setPullOpen(true)}>Pull from another branch…</button>}
                  <button className="ghost" onClick={renderNow} disabled={rendering}>{rendering ? "Rendering…" : "▶ Render & play"}</button>
                </div>
              </section>
              <section className="panel">
                <div className="panel-h">
                  <h3>What changed</h3>
                  <label className="muted small">compared with&nbsp;
                    <select value={fromRef} onChange={(e) => setFromRef(e.target.value)}>
                      {versions.filter((v) => v.version_id !== sel).map((v) => <option key={v.version_id} value={v.version_id}>{v.version_id.slice(0, 7)} · {v.branch} · {v.message}</option>)}
                    </select>
                  </label>
                </div>
                {diff && diff.items.length === 0 && <p className="muted">No differences.</p>}
                <ul className="sentences">
                  {diff?.items.map((d, i) => (
                    <li key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} className={hover === i ? "hover" : ""}>
                      <span className="icon">{ICON[d.kind] ?? "•"}</span>{d.sentence}<span className="kind">{d.kind.replace(/_/g, " ")}</span>
                    </li>
                  ))}
                </ul>
                {diff && (
                  <div className="two">
                    <Strip tl={diff.fromTimeline} label={`Before · ${label(fromRef)}`} accent={colors[vById.get(fromRef)?.branch ?? ""]} total={total} highlight={hl} />
                    <Strip tl={diff.toTimeline} label={`After · ${label(sel)}`} accent={colors[cur.version.branch]} total={total} highlight={hl} />
                  </div>
                )}
              </section>
              {video && video.vid === sel && (
                <section className="panel">
                  <div className="panel-h"><h3>Render</h3><span className="muted small">{video.duration}s · ffmpeg · 640×360</span></div>
                  <video src={video.url} controls autoPlay className="player" />
                </section>
              )}
            </>
          )}
        </main>
      </div>
      {pullOpen && branchOfHead && (
        <PullDialog pid={pid} into={branchOfHead} branches={Object.keys(refs)} colors={colors} onClose={() => setPullOpen(false)}
          onDone={(vid) => afterPull(vid, "Pulled cleanly: new version created")}
          onConflict={(plan, opIds) => { setPullOpen(false); setConflict({ plan, opIds }); }} />
      )}
      {toast && <div className="toast" onClick={() => setToast("")}>{toast}</div>}
    </div>
  );
}
