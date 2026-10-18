/**
 * Semantic diff between any two timelines (structural, keyed by stable ids +
 * clip lineage). Each change is a structured item plus a sentence an editor
 * would say.
 */
import { r3 } from "./canonical.js";
import { clipEnd } from "./ops.js";
import type { Clip, Effect, TextLayer, Timeline, Transition } from "./types.js";

export interface DiffItem {
  kind: string;               // clip_trimmed, clip_moved, clip_split, clip_deleted, clip_added, ...
  entity: string;             // clip:clp_0003, transition:tr_01, text:txt_02, effect:fx_02, track:V3, settings
  track_id?: string;
  range?: [number, number];   // timeline range the change affects (for highlighting)
  sentence: string;
  before?: any;
  after?: any;
}

const ASSET_NAMES: Record<string, string> = {
  a_cam: "A-cam", b_cam: "B-cam", broll_city: "city B-roll", broll_desk: "desk B-roll",
  broll_product: "product B-roll", broll_screen: "screen recording", title_card: "title card",
  music_upbeat: "upbeat music bed", music_calm: "calm music bed", sfx_whoosh: "whoosh SFX", sfx_click: "click SFX",
};
const LOOKS: Record<string, string> = { warm_film: "warm film", teal_orange: "teal/orange", neutral_clean: "neutral",
  high_contrast: "high-contrast", pastel: "pastel" };
const TRANSITIONS: Record<string, string> = { crossfade: "crossfade", whip_pan: "whip pan", dip_to_black: "dip to black",
  slide: "slide", zoom_punch: "zoom punch", hard_cut: "hard cut", glitch: "glitch" };
const ROLES: Record<string, string> = { title: "title", lower_third: "lower-third", captions: "captions" };

export const tc = (s: number) => {
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
};
const secs = (d: number) => `${r3(Math.abs(d)).toFixed(1).replace(/\.0$/, ".0")}s`;
const pct = (v: number) => `${Math.round(v * 100)}%`;
const name = (c: Clip) => ASSET_NAMES[c.asset_label ?? ""] ?? c.asset_label ?? c.id;
const look = (l: string) => LOOKS[l] ?? l.replace(/_/g, " ");
const trn = (t: string) => TRANSITIONS[t] ?? t.replace(/_/g, " ");
const article = (w: string) => (/^[aeiou]/i.test(w) ? "an" : "a");
const words = (s: string) => s.replace(/_/g, " ");
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function clipsById(tl: Timeline) {
  const m = new Map<string, { clip: Clip; track: string }>();
  for (const t of tl.tracks) for (const c of t.clips) m.set(c.id, { clip: c, track: t.id });
  return m;
}
const short = (c: Clip) => (c.music_mood ? `the ${words(c.music_mood)} music` : `the ${name(c)} clip`);
const desc = (c: Clip) => (c.music_mood ? `the ${words(c.music_mood)} music` : `the ${name(c)} clip at ${tc(c.timeline_start)}`);
function cutTime(tl: Timeline, x: Transition): number | null {
  const m = clipsById(tl).get(x.from_clip);
  return m ? clipEnd(m.clip) : null;
}
function labelText(x: TextLayer) {
  return ROLES[x.role ?? "title"] ?? "text";
}

export function diffTimelines(a: Timeline, b: Timeline): DiffItem[] {
  const out: DiffItem[] = [];
  // settings
  if ((a.settings.aspect ?? "16:9") !== (b.settings.aspect ?? "16:9"))
    out.push({ kind: "format_changed", entity: "settings", sentence: `Reformatted the project from ${a.settings.aspect ?? "16:9"} to ${b.settings.aspect}`,
      before: a.settings.aspect, after: b.settings.aspect });
  // tracks
  const ta = new Map(a.tracks.map((t) => [t.id, t]));
  const tb = new Map(b.tracks.map((t) => [t.id, t]));
  for (const t of b.tracks) if (!ta.has(t.id))
    out.push({ kind: "track_added", entity: `track:${t.id}`, track_id: t.id, sentence: `Added a new ${t.kind} track ${t.id} (“${t.name}”)` });
  for (const t of a.tracks) if (!tb.has(t.id))
    out.push({ kind: "track_removed", entity: `track:${t.id}`, track_id: t.id, sentence: `Removed the ${t.kind} track ${t.id} (“${t.name}”)` });

  // clips
  const ca = clipsById(a);
  const cb = clipsById(b);
  const splitChildren = new Map<string, Clip[]>();
  for (const [id, { clip }] of cb) {
    const par = clip.lineage?.parent;
    if (!ca.has(id) && par && ca.has(par) && !cb.has(par)) {
      if (!splitChildren.has(par)) splitChildren.set(par, []);
      splitChildren.get(par)!.push(clip);
    }
  }
  for (const [id, { clip: c, track }] of ca) {
    if (cb.has(id)) continue;
    const kids = splitChildren.get(id);
    if (kids) {
      kids.sort((x, y) => x.timeline_start - y.timeline_start);
      const at = kids.length > 1 ? kids[1].timeline_start : (kids[0].timeline_start > c.timeline_start + 1e-6 ? kids[0].timeline_start : clipEnd(kids[0]));
      let s = `Split ${short(c)} (${tc(c.timeline_start)}–${tc(clipEnd(c))}) at ${tc(at)}`;
      if (kids.length === 1) {
        const kept = kids[0];
        const removed = r3(c.src_out - c.src_in - (kept.src_out - kept.src_in));
        const tail = kept.timeline_start <= c.timeline_start + 1e-6;
        s += ` and dropped the ${secs(removed)} ${tail ? "tail" : "head"}, so it now ${tail ? "ends at " + tc(clipEnd(kept)) : "starts at " + tc(kept.timeline_start)}`;
      } else s += ` into ${kids.length} parts`;
      out.push({ kind: "clip_split", entity: `clip:${id}`, track_id: track, range: [c.timeline_start, clipEnd(c)], sentence: s,
        before: { id }, after: { parts: kids.map((k) => k.id) } });
    } else {
      out.push({ kind: "clip_deleted", entity: `clip:${id}`, track_id: track, range: [c.timeline_start, clipEnd(c)],
        sentence: `Removed ${desc(c)} (${tc(c.timeline_start)}–${tc(clipEnd(c))}) from ${track}` });
    }
  }
  const splitKidIds = new Set([...splitChildren.values()].flat().map((k) => k.id));
  for (const [id, { clip: c, track }] of cb) {
    if (ca.has(id) || splitKidIds.has(id)) continue;
    const s = c.music_mood
      ? `Added ${article(words(c.music_mood))} ${words(c.music_mood)} music bed under ${tc(c.timeline_start)}–${tc(clipEnd(c))}`
      : `Added the ${name(c)} on ${track} at ${tc(c.timeline_start)} (${secs(clipEnd(c) - c.timeline_start)})`;
    out.push({ kind: "clip_added", entity: `clip:${id}`, track_id: track, range: [c.timeline_start, clipEnd(c)], sentence: s });
  }
  for (const [id, { clip: x, track: trA }] of ca) {
    const yb = cb.get(id);
    if (!yb) continue;
    const y = yb.clip;
    const trB = yb.track;
    const durA = r3(clipEnd(x) - x.timeline_start), durB = r3(clipEnd(y) - y.timeline_start);
    const rng: [number, number] = [Math.min(x.timeline_start, y.timeline_start), Math.max(clipEnd(x), clipEnd(y))];
    if (trA !== trB) out.push({ kind: "clip_moved", entity: `clip:${id}`, track_id: trB, range: rng,
      sentence: `Moved ${desc(x)} from ${trA} to ${trB}${y.timeline_start !== x.timeline_start ? ` at ${tc(y.timeline_start)}` : ""}` });
    const outDelta = r3(y.src_out - x.src_out);
    const inDelta = r3(y.src_in - x.src_in);
    const startDelta = r3(y.timeline_start - x.timeline_start);
    if (inDelta !== 0) {
      out.push({ kind: "clip_trimmed", entity: `clip:${id}`, track_id: trB, range: rng, before: { src_in: x.src_in }, after: { src_in: y.src_in },
        sentence: inDelta > 0
          ? `Trimmed ${secs(inDelta)} off the start of ${desc(x)} (now ${secs(durB)}, starts at ${tc(y.timeline_start)})`
          : `Extended the start of ${desc(x)} by ${secs(inDelta)} (now ${secs(durB)})` });
    } else if (startDelta !== 0 && trA === trB) {
      out.push({ kind: "clip_moved", entity: `clip:${id}`, track_id: trB, range: rng, before: { timeline_start: x.timeline_start }, after: { timeline_start: y.timeline_start },
        sentence: `Moved ${short(x)} ${secs(startDelta)} ${startDelta > 0 ? "later" : "earlier"} (${tc(x.timeline_start)} → ${tc(y.timeline_start)}) on ${trB}` });
    }
    if (outDelta !== 0) {
      out.push({ kind: "clip_trimmed", entity: `clip:${id}`, track_id: trB, range: rng, before: { src_out: x.src_out, duration: durA }, after: { src_out: y.src_out, duration: durB },
        sentence: outDelta < 0
          ? `Trimmed ${secs(outDelta)} off the end of ${desc(x)} (now ${secs(durB)}, ends at ${tc(clipEnd(y))})`
          : `Extended the end of ${desc(x)} by ${secs(outDelta)} (now ${secs(durB)}, ends at ${tc(clipEnd(y))})` });
    }
    if (x.volume !== y.volume) out.push({ kind: "volume_changed", entity: `clip:${id}`, track_id: trB, range: rng, before: x.volume, after: y.volume,
      sentence: `${y.volume < x.volume ? "Lowered" : "Raised"} the volume of ${desc(x)} from ${pct(x.volume)} to ${pct(y.volume)}` });
    if (x.speed !== y.speed) out.push({ kind: "speed_changed", entity: `clip:${id}`, track_id: trB, range: rng,
      sentence: `Changed the speed of ${desc(x)} from ${x.speed}x to ${y.speed}x` });
    if (x.asset_hash !== y.asset_hash || x.music_mood !== y.music_mood) {
      out.push(x.music_mood || y.music_mood
        ? { kind: "music_replaced", entity: `clip:${id}`, track_id: trB, range: rng, before: x.music_mood, after: y.music_mood,
            sentence: `Swapped the music under ${tc(y.timeline_start)}–${tc(clipEnd(y))} from ${words(x.music_mood ?? name(x))} to ${words(y.music_mood ?? name(y))}` }
        : { kind: "media_replaced", entity: `clip:${id}`, track_id: trB, range: rng, sentence: `Replaced the media in ${desc(x)} with ${name(y)}` });
    }
  }

  // transitions
  const xa = new Map(a.transitions.map((t) => [t.id, t]));
  const xb = new Map(b.transitions.map((t) => [t.id, t]));
  const between = (tl: Timeline, x: Transition) => {
    const m = clipsById(tl);
    const f = m.get(x.from_clip)?.clip, t = m.get(x.to_clip)?.clip;
    return f && t ? `${name(f)} → ${name(t)}` : `${x.from_clip} → ${x.to_clip}`;
  };
  const trange = (tl: Timeline, x: Transition): [number, number] | undefined => {
    const c = cutTime(tl, x);
    return c === null ? undefined : [r3(c - x.duration / 2), r3(c + x.duration / 2)];
  };
  for (const [id, x] of xb) {
    const old = xa.get(id);
    const at = cutTime(b, x);
    const where = at !== null ? ` at ${tc(at)}` : "";
    if (!old) out.push({ kind: "transition_added", entity: `transition:${id}`, track_id: x.track_id, range: trange(b, x), after: x.type,
      sentence: `Added ${article(trn(x.type))} ${trn(x.type)} transition${where} (${between(b, x)}, ${secs(x.duration)})` });
    else if (old.type !== x.type || old.duration !== x.duration) {
      const dur = old.duration !== x.duration ? ` (${secs(old.duration)} → ${secs(x.duration)})` : "";
      out.push({ kind: "transition_changed", entity: `transition:${id}`, track_id: x.track_id, range: trange(b, x), before: old.type, after: x.type,
        sentence: old.type !== x.type ? `Swapped the ${trn(old.type)}${where} for ${article(trn(x.type))} ${trn(x.type)}${dur}`
          : `Changed the ${trn(x.type)}${where} length${dur}` });
    }
  }
  for (const [id, x] of xa) if (!xb.has(id)) {
    const at = cutTime(a, x);
    out.push({ kind: "transition_removed", entity: `transition:${id}`, track_id: x.track_id, range: trange(a, x),
      sentence: `Removed the ${trn(x.type)}${at !== null ? ` at ${tc(at)}` : ""} (now a straight cut)` });
  }

  // text
  const sa = new Map(a.text_layers.map((t) => [t.id, t]));
  const sb = new Map(b.text_layers.map((t) => [t.id, t]));
  for (const [id, x] of sb) {
    const old = sa.get(id);
    const rng: [number, number] = [x.start, r3(x.start + x.duration)];
    if (!old) {
      out.push({ kind: "text_added", entity: `text:${id}`, track_id: x.track_id, range: rng,
        sentence: x.role === "captions"
          ? `Added ${words(x.style.preset ?? "default")} captions from ${tc(x.start)}`
          : `Added a ${labelText(x)} “${x.text}” at ${tc(x.start)} for ${secs(x.duration)} (${x.style.font})` });
      continue;
    }
    if (old.text !== x.text) out.push({ kind: "text_edited", entity: `text:${id}`, track_id: x.track_id, range: rng, before: old.text, after: x.text,
      sentence: `Changed the ${labelText(x)} text from “${old.text}” to “${x.text}”` });
    if (old.start !== x.start || old.duration !== x.duration) out.push({ kind: "text_retimed", entity: `text:${id}`, track_id: x.track_id, range: rng,
      sentence: `Retimed the ${labelText(x)} “${x.text}” to ${tc(x.start)}–${tc(x.start + x.duration)}` });
    const styleKeys = new Set([...Object.keys(old.style), ...Object.keys(x.style)]);
    for (const k of styleKeys) {
      const o = (old.style as any)[k], n = (x.style as any)[k];
      if (o === n) continue;
      const s = k === "preset" ? `Changed the caption style from ${words(String(o))} to ${words(String(n))}`
        : k === "font" ? `Changed the ${labelText(x)} font from ${o} to ${n}`
        : `Changed the ${labelText(x)} ${k} from ${o} to ${n}`;
      out.push({ kind: "text_style_changed", entity: `text:${id}`, track_id: x.track_id, range: rng, before: { [k]: o }, after: { [k]: n }, sentence: s });
    }
  }
  for (const [id, x] of sa) if (!sb.has(id)) out.push({ kind: "text_removed", entity: `text:${id}`, track_id: x.track_id,
    range: [x.start, r3(x.start + x.duration)], sentence: `Removed the ${labelText(x)} “${x.text}”` });

  // effects
  const ea = new Map(a.effects.map((e) => [e.id, e]));
  const eb = new Map(b.effects.map((e) => [e.id, e]));
  const target = (tl: Timeline, e: Effect) => {
    const m = clipsById(tl).get(e.target_id);
    return m ? { s: desc(m.clip), track: m.track, range: [m.clip.timeline_start, clipEnd(m.clip)] as [number, number] } : { s: e.target_id, track: undefined, range: undefined };
  };
  const fxName = (e: Effect) => (e.type === "color_grade" ? `${look(e.params.look)} grade` : words(e.type));
  for (const [id, e] of eb) {
    const old = ea.get(id);
    const tg = target(b, e);
    if (!old) {
      out.push({ kind: "effect_added", entity: `effect:${id}`, track_id: tg.track, range: tg.range,
        sentence: `Added ${article(fxName(e))} ${fxName(e)} to ${tg.s}${e.params.intensity !== undefined ? ` (intensity ${e.params.intensity})` : ""}` });
      continue;
    }
    if (old.params.look !== e.params.look && e.type === "color_grade")
      out.push({ kind: "effect_updated", entity: `effect:${id}`, track_id: tg.track, range: tg.range, before: old.params.look, after: e.params.look,
        sentence: `Changed the grade on ${tg.s} from ${look(old.params.look)} to ${look(e.params.look)}` });
    for (const k of Object.keys(e.params)) {
      if (k === "look" || old.params[k] === e.params[k]) continue;
      const o = old.params[k], n = e.params[k];
      const verb = typeof o === "number" && typeof n === "number" ? (n > o ? "Raised" : "Lowered") : "Changed";
      out.push({ kind: "effect_updated", entity: `effect:${id}`, track_id: tg.track, range: tg.range, before: { [k]: o }, after: { [k]: n },
        sentence: `${verb} the ${k} of the ${fxName(e)} on ${tg.s} from ${o} to ${n}` });
    }
    if (old.target_id !== e.target_id && !(e.target_id.startsWith("clp_") && !clipsById(a).has(e.target_id)))
      out.push({ kind: "effect_moved", entity: `effect:${id}`, sentence: `Moved the ${fxName(e)} to ${tg.s}` });
  }
  for (const [id, e] of ea) if (!eb.has(id)) {
    const tg = target(a, e);
    out.push({ kind: "effect_removed", entity: `effect:${id}`, track_id: tg.track, range: tg.range, sentence: `Removed the ${fxName(e)} from ${tg.s}` });
  }
  return out.map((d) => ({ ...d, sentence: cap(d.sentence) }));
}
