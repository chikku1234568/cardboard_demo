export type Seconds = number;

export interface Lineage { origin: string; parent: string | null; created_by_op: string }
export interface Clip {
  id: string; asset_hash: string; asset_label?: string;
  src_in: Seconds; src_out: Seconds; timeline_start: Seconds;
  speed: number; volume: number; lineage: Lineage; music_mood?: string;
}
export interface Track { id: string; kind: "video" | "audio" | "text"; name: string; clips: Clip[] }
export interface Transition { id: string; track_id: string; from_clip: string; to_clip: string; type: string; duration: Seconds }
export interface TextLayer {
  id: string; track_id: string; role?: "title" | "lower_third" | "captions"; text: string; start: Seconds; duration: Seconds;
  style: { font: string; size: number; color: string; position: string; animation?: string; preset?: string };
}
export interface Effect { id: string; target_id: string; type: string; params: Record<string, any> }
export interface Timeline {
  schema_version: string; project_id: string; name: string;
  settings: { fps: number; width: number; height: number; sample_rate: number; time_unit: string; aspect?: string };
  tracks: Track[]; transitions: Transition[]; text_layers: TextLayer[]; effects: Effect[];
}

export interface Version {
  version_id: string; project_id: string; timeline_hash: string; parents: string[]; branch: string;
  author: string; timestamp: string; message: string; op_ids: string[]; session_id: string | null;
  pulled_from?: { branch: string; version: string; op_ids: string[] };
}
export interface Ref { head: string; created_from: string | null; created_by: string; created_at: string }
export interface Touches { entities: string[]; ranges: { track_id: string; start: number; end: number }[] }
export interface Op {
  op_id: string; project_id: string; session_id: string | null; branch: string;
  parent_version: string; result_version: string | null; author: string; on_behalf_of: string | null;
  timestamp: string; type: string; decision_id: string | null;
  context: Record<string, string | null>;
  replaces_op: string | null; reverts_op: string | null; accepts_op: string | null;
  payload: any; touches: Touches; pulled_from_op?: string;
}
