import { useEffect, useState } from "react";
import { api, type PlanView } from "./api";

export function PullDialog({ pid, into, branches, colors, onClose, onDone, onConflict }: {
  pid: string; into: string; branches: string[]; colors: Record<string, string>; onClose: () => void;
  onDone: (vid: string) => void; onConflict: (p: PlanView, opIds: string[]) => void;
}) {
  const others = branches.filter((b) => b !== into);
  const [from, setFrom] = useState(others[0]);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setPlan(null);
    api.plan(pid, into, from).then((p) => { setPlan(p); setSel(new Set(p.theirs.map((o) => o.op_id))); }).catch((e) => setErr(e.message));
  }, [pid, into, from]);
  const go = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      const opIds = [...sel];
      const r = await api.pull(pid, { into, from, opIds });
      if (r.status === "applied" && r.version) onDone(r.version.version_id);
      else if (r.status === "conflict" && r.plan) onConflict(r.plan, opIds);
      else setErr("nothing to pull");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Pull changes into <span style={{ color: colors[into] }}>{into}</span></h2>
        <p className="muted small">Pick changes from another branch. Non-overlapping changes apply automatically; edits to the same clip or overlapping time on a track become conflicts you resolve. Nothing is merged into a main branch: the result is a new version on {into}.</p>
        <label className="row">From branch&nbsp;
          <select value={from} onChange={(e) => setFrom(e.target.value)}>
            {others.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        {!plan && !err && <p className="muted">Planning…</p>}
        {plan && (
          <div className="ops-list">
            {plan.theirs.length === 0 && <p className="muted">Nothing new on {from} since the common ancestor{plan.skipped_already_pulled.length ? " (already pulled)" : ""}.</p>}
            {plan.theirs.map((o) => (
              <label key={o.op_id} className={`op-row ${o.status}`}>
                <input type="checkbox" checked={sel.has(o.op_id)} onChange={(e) => {
                  const n = new Set(sel); e.target.checked ? n.add(o.op_id) : n.delete(o.op_id); setSel(n);
                }} />
                <span className={`badge ${o.status === "conflict" ? "warn" : "ok"}`}>{o.status === "conflict" ? "conflict" : "auto"}</span>
                <span>{o.sentences.join("; ")}</span>
                <span className="muted small">{o.author} · {o.short}</span>
              </label>
            ))}
          </div>
        )}
        {err && <p className="error">{err}</p>}
        <div className="row end">
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button disabled={!plan || !sel.size || busy} onClick={go}>
            {plan && plan.theirs.some((o) => sel.has(o.op_id) && o.status === "conflict") ? "Pull & resolve conflicts…" : `Pull ${sel.size} change${sel.size !== 1 ? "s" : ""}`}
          </button>
        </div>
      </div>
    </div>
  );
}
