import type { Timeline } from "./api";
import { tc } from "./api";

const COLORS: Record<string, string> = { a_cam: "#3b6fd8", b_cam: "#b4458a", broll_city: "#3f9b5a", broll_desk: "#6f8f3a",
  broll_product: "#2f8f77", broll_screen: "#2a8aa8", title_card: "#5b6170", music_upbeat: "#7c5cd6", music_calm: "#5e4ab0",
  sfx_whoosh: "#c47a2c", sfx_click: "#c47a2c" };
const NAMES: Record<string, string> = { a_cam: "A-cam", b_cam: "B-cam", broll_city: "city b-roll", broll_desk: "desk b-roll",
  broll_product: "product b-roll", broll_screen: "screen rec", title_card: "title card", music_upbeat: "music", music_calm: "music",
  sfx_whoosh: "whoosh", sfx_click: "click" };
const TR: Record<string, string> = { crossfade: "xfade", whip_pan: "whip", dip_to_black: "dip", slide: "slide", zoom_punch: "zoom", glitch: "glitch" };

export interface Highlight { entities?: Set<string>; ranges?: { track_id?: string; start: number; end: number; tone?: "ours" | "theirs" | "warn" }[] }

const end = (c: any) => c.timeline_start + (c.src_out - c.src_in) / (c.speed || 1);
export function duration(tl: Timeline) {
  let e = 0;
  for (const t of tl.tracks) for (const c of t.clips) e = Math.max(e, end(c));
  for (const x of tl.text_layers) e = Math.max(e, x.start + x.duration);
  return e;
}

export function Strip({ tl, label, accent, view, highlight, tracks, total, onClip }: {
  tl: Timeline; label?: string; accent?: string; view?: [number, number]; highlight?: Highlight; tracks?: string[];
  total?: number; onClip?: (id: string) => void;
}) {
  const T = total ?? Math.ceil(duration(tl));
  const [v0, v1] = view ?? [0, T];
  const span = Math.max(0.5, v1 - v0);
  const x = (t: number) => `${((t - v0) / span) * 100}%`;
  const w = (a: number, b: number) => `${(Math.max(0, Math.min(b, v1) - Math.max(a, v0)) / span) * 100}%`;
  const vis = (a: number, b: number) => b > v0 && a < v1;
  const hl = highlight?.entities ?? new Set<string>();
  const rows = tl.tracks.filter((t) => !tracks || tracks.includes(t.id));
  const tick = span > 20 ? 5 : span > 8 ? 2 : 1;
  const ticks: number[] = [];
  for (let t = Math.ceil(v0 / tick) * tick; t <= v1; t += tick) ticks.push(t);
  const fxOf = (id: string) => tl.effects.filter((e) => e.target_id === id);
  return (
    <div className="strip">
      {label && <div className="strip-label" style={{ borderColor: accent }}>{label}</div>}
      <div className="strip-body">
        <div className="ruler">
          {ticks.map((t) => <span key={t} style={{ left: x(t) }}>{tc(t)}</span>)}
        </div>
        {rows.map((tr) => (
          <div className={`lane lane-${tr.kind}`} key={tr.id}>
            <div className="lane-name">{tr.id}</div>
            <div className="lane-area">
              {(highlight?.ranges ?? []).filter((r) => !r.track_id || r.track_id === tr.id).filter((r) => vis(r.start, r.end)).map((r, i) => (
                <div key={i} className={`hl-range ${r.tone ?? "warn"}`} style={{ left: x(Math.max(r.start, v0)), width: w(r.start, Math.max(r.end, r.start + span / 200)) }} />
              ))}
              {tr.kind !== "text" && tr.clips.filter((c) => vis(c.timeline_start, end(c))).map((c) => {
                const on = hl.has(`clip:${c.id}`);
                return (
                  <div key={c.id} className={`clip ${on ? "on" : ""}`} title={`${c.id} · ${tc(c.timeline_start)}–${tc(end(c))}`}
                    onClick={() => onClip?.(c.id)}
                    style={{ left: x(Math.max(c.timeline_start, v0)), width: w(c.timeline_start, end(c)), background: COLORS[c.asset_label ?? ""] ?? "#555" }}>
                    <span className="clip-name">{c.music_mood ? `♪ ${c.music_mood.replace(/_/g, " ")}` : NAMES[c.asset_label ?? ""] ?? c.id}</span>
                    <span className="clip-id">{c.id.replace("clp_", "#")}{c.lineage.parent ? ` ⟵ ${c.lineage.parent.replace("clp_", "#")}` : ""}</span>
                    {fxOf(c.id).map((e) => <span key={e.id} className={`fx ${hl.has(`effect:${e.id}`) ? "on" : ""}`}>{e.params.look ? e.params.look.replace(/_/g, " ") : e.type}</span>)}
                    {c.volume !== 1 && tr.kind === "audio" && <span className="vol">{Math.round(c.volume * 100)}%</span>}
                  </div>
                );
              })}
              {tr.kind === "text" && tl.text_layers.filter((t) => t.track_id === tr.id && vis(t.start, t.start + t.duration)).map((t) => (
                <div key={t.id} className={`clip text ${hl.has(`text:${t.id}`) ? "on" : ""}`} title={t.text}
                  style={{ left: x(Math.max(t.start, v0)), width: w(t.start, t.start + t.duration) }}>
                  <span className="clip-name">{t.role === "captions" ? `captions · ${t.style.preset}` : `“${t.text}”`}</span>
                  <span className="clip-id">{t.style.font}</span>
                </div>
              ))}
              {tr.kind === "video" && tl.transitions.filter((t) => t.track_id === tr.id).map((t) => {
                const from = tr.clips.find((c) => c.id === t.from_clip);
                if (!from || !vis(end(from) - 0.01, end(from) + 0.01)) return null;
                return <div key={t.id} className={`trans ${hl.has(`transition:${t.id}`) ? "on" : ""}`} style={{ left: x(end(from)) }} title={`${t.type} ${t.duration}s`}>{TR[t.type] ?? t.type}</div>;
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
