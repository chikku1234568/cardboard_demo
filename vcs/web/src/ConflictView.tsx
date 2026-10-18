import { useMemo, useState } from "react";
import type { PlanView } from "./api";
import { tc } from "./api";
import { duration, Strip } from "./Strip";

/** Side-by-side base / ours / theirs per conflict; click a side to pick it; commit a new version. */
export function ConflictView({ plan, colors, onCancel, onResolve, busy }: {
  plan: PlanView; colors: Record<string, string>; onCancel: () => void;
  onResolve: (res: Record<string, "ours" | "theirs">) => void; busy: boolean;
}) {
  const [picks, setPicks] = useState<Record<string, "ours" | "theirs">>({});
  const auto = plan.theirs.filter((o) => o.status === "auto");
  const total = useMemo(() => Math.ceil(Math.max(duration(plan.timelines.ours), duration(plan.timelines.theirs))), [plan]);
  const done = plan.conflicts.every((c) => picks[c.id]);
  return (
    <div className="conflict-view">
      <div className="cv-head">
        <div>
          <h2>Resolve pull: <span style={{ color: colors[plan.from] }}>{plan.from}</span> → <span style={{ color: colors[plan.into] }}>{plan.into}</span></h2>
          <p className="muted">{plan.conflicts.length} conflict{plan.conflicts.length > 1 ? "s" : ""} · {auto.length} change{auto.length !== 1 ? "s" : ""} apply automatically · common ancestor {plan.common_ancestor?.slice(0, 10)}</p>
        </div>
        <div className="row">
          <button className="ghost" onClick={onCancel}>Cancel</button>
          <button disabled={!done || busy} onClick={() => onResolve(picks)}>{busy ? "Creating…" : "Create resolved version"}</button>
        </div>
      </div>
      {plan.conflicts.map((c, n) => {
        const ranges = c.reasons.filter((r) => r.overlap);
        const tracks = [...new Set([...c.reasons.map((r) => r.track_id).filter(Boolean) as string[], ...c.theirs.touches.ranges.map((r) => r.track_id)])];
        const lo = Math.min(...c.theirs.touches.ranges.map((r) => r.start), ...c.ours.flatMap((o) => o.touches.ranges.map((r) => r.start)));
        const hi = Math.max(...c.theirs.touches.ranges.map((r) => r.end), ...c.ours.flatMap((o) => o.touches.ranges.map((r) => r.end)));
        const view: [number, number] = [Math.max(0, lo - 4), Math.min(total, hi + 4)];
        const ents = new Set(c.reasons.flatMap((r) => r.entities ?? []));
        const hlRanges = ranges.map((r) => ({ track_id: r.track_id, start: r.overlap![0], end: r.overlap![1], tone: "warn" as const }));
        const why = c.reasons.map((r) => r.kind === "same_entity" ? `both edited ${(r.entities ?? []).filter((e) => !e.startsWith("lineage")).join(", ").replace("clip:", "clip ")}`
          : r.kind === "overlapping_time_range" ? `overlapping edits on ${r.track_id} ${tc(r.overlap![0])}–${tc(r.overlap![1])}`
          : `clips would overlap on ${r.track_id}`).join(" · ");
        return (
          <div className="conflict" key={c.id}>
            <div className="conflict-title"><span className="badge warn">Conflict {n + 1}</span> {why}</div>
            <div className="three">
              {plan.timelines.base && <Strip tl={plan.timelines.base} label="Base" accent="#8b95a7" view={view} tracks={tracks} highlight={{ entities: ents, ranges: hlRanges }} />}
              <Strip tl={plan.timelines.ours} label={`Ours · ${plan.into}`} accent={colors[plan.into]} view={view} tracks={tracks} highlight={{ entities: ents, ranges: hlRanges }} />
              <Strip tl={plan.timelines.theirs} label={`Theirs · ${plan.from}`} accent={colors[plan.from]} view={view} tracks={tracks} highlight={{ entities: ents, ranges: hlRanges }} />
            </div>
            <div className="picks">
              <button className={`pick ${picks[c.id] === "ours" ? "chosen" : ""}`} style={{ borderColor: colors[plan.into] }} onClick={() => setPicks({ ...picks, [c.id]: "ours" })}>
                <div className="pick-h">Keep ours <span className="muted">({c.ours.map((o) => `${o.author} · ${o.short}`).join(", ") || plan.into})</span></div>
                {(c.ours.length ? c.ours.flatMap((o) => o.sentences) : ["Keep the current timeline"]).map((s, i) => <div key={i}>{s}</div>)}
              </button>
              <button className={`pick ${picks[c.id] === "theirs" ? "chosen" : ""}`} style={{ borderColor: colors[plan.from] }} onClick={() => setPicks({ ...picks, [c.id]: "theirs" })}>
                <div className="pick-h">Take theirs <span className="muted">({c.theirs.author} · {c.theirs.short})</span></div>
                {c.theirs.sentences.map((s, i) => <div key={i}>{s}</div>)}
              </button>
            </div>
          </div>
        );
      })}
      {auto.length > 0 && (
        <div className="auto-list">
          <div className="muted small">Applied automatically (different clips / tracks / time ranges):</div>
          {auto.map((o) => <div key={o.op_id} className="sentence"><span className="badge ok">auto</span> {o.sentences.join("; ")} <span className="muted">· {o.author} {o.short}</span></div>)}
        </div>
      )}
    </div>
  );
}
