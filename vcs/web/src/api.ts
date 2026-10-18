export interface Clip { id: string; asset_label?: string; src_in: number; src_out: number; timeline_start: number; speed: number;
  volume: number; music_mood?: string; lineage: { origin: string; parent: string | null } }
export interface Track { id: string; kind: string; name: string; clips: Clip[] }
export interface Timeline { name: string; settings: any; tracks: Track[]; transitions: any[]; text_layers: any[]; effects: any[] }
export interface Version { version_id: string; branch: string; parents: string[]; author: string; timestamp: string; message: string;
  op_ids: string[]; timeline_hash: string; pulled_from?: { branch: string; version: string; op_ids: string[] } }
export interface Ref { head: string; created_from: string | null; created_by: string }
export interface DiffItem { kind: string; entity: string; track_id?: string; range?: [number, number]; sentence: string }
export interface OpView { op_id: string; short: string; type: string; author: string; branch: string; sentences: string[];
  touches: { entities: string[]; ranges: { track_id: string; start: number; end: number }[] }; status?: "auto" | "conflict" }
export interface Reason { kind: string; entities?: string[]; track_id?: string; overlap?: [number, number]; clips?: string[] }
export interface PlanView { into: string; from: string; into_head: string; from_head: string; common_ancestor: string | null;
  expected: "clean" | "conflict"; skipped_already_pulled: string[]; theirs: OpView[];
  conflicts: { id: string; short: string; reasons: Reason[]; theirs: OpView; ours: OpView[] }[];
  timelines: { base: Timeline | null; ours: Timeline; theirs: Timeline; preview: Timeline } }

async function j<T>(r: Promise<Response>): Promise<T> {
  const res = await r;
  const body = await res.json();
  if (!res.ok) throw new Error(body.error ?? res.statusText);
  return body as T;
}
const enc = encodeURIComponent;
export const api = {
  projects: () => j<any[]>(fetch("/api/projects")),
  state: (p: string) => j<{ refs: Record<string, Ref>; versions: Version[]; flagship: boolean }>(fetch(`/api/p/${p}/state`)),
  version: (p: string, ref: string) => j<{ version: Version; timeline: Timeline; duration: number; ops: OpView[] }>(fetch(`/api/p/${p}/version/${enc(ref)}`)),
  diff: (p: string, from: string, to: string) => j<{ from: string; to: string; items: DiffItem[]; fromTimeline: Timeline; toTimeline: Timeline }>(
    fetch(`/api/p/${p}/diff?from=${enc(from)}&to=${enc(to)}`)),
  plan: (p: string, into: string, from: string) => j<PlanView>(fetch(`/api/p/${p}/pull/plan?into=${enc(into)}&from=${enc(from)}`)),
  pull: (p: string, body: any) => j<{ status: string; version?: Version; diff?: DiffItem[]; plan?: PlanView }>(
    fetch(`/api/p/${p}/pull`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })),
  render: (p: string, ref: string) => j<{ url: string; duration: number }>(
    fetch(`/api/p/${p}/render`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ref }) })),
  reset: (p: string) => j<any>(fetch(`/api/p/${p}/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" })),
};

export const tc = (s: number) => {
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};
export const BRANCH_COLORS = ["#8b95a7", "#4f8cff", "#f5a524", "#ef5da8", "#2ec4b6", "#a78bfa", "#84cc16", "#f97316"];
