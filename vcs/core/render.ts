/**
 * Render a timeline to mp4 with ffmpeg.
 *  V1: clips laid out in time (gaps = black), joined with concat, or xfade where a
 *      transition sits between two touching clips (clips are padded by d/2 on each
 *      side with cloned frames so the cut stays at the same timeline time).
 *  V2: overlaid full-frame over V1 during each clip's range (cutaway b-roll).
 *  Text: drawtext per text layer (title centre, lower-third, captions bottom).
 *  Effects: colour grades approximated with colorbalance/eq on the clip.
 *  Audio: clip audio (A/B cam tone tracks) + music/SFX, delayed to timeline time, mixed.
 * Output is always 640x360@30; `settings.aspect` is not applied (known gap).
 */
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { clipEnd, timelineDuration } from "./ops.js";
import { MEDIA, RENDERS } from "./paths.js";
import type { Clip, Effect, Timeline } from "./types.js";

const FPS = 30, W = 640, H = 360;
const XFADE: Record<string, string> = { crossfade: "fade", dip_to_black: "fadeblack", whip_pan: "smoothleft",
  slide: "slideleft", zoom_punch: "zoomin", glitch: "pixelize" };

interface Asset { asset_id: string; path: string; sha256: string; has_audio: boolean; kind: string; probed_duration: number }
let manifest: Map<string, Asset> | null = null;
function assets() {
  if (!manifest) {
    const m = JSON.parse(readFileSync(join(MEDIA, "manifest.json"), "utf8"));
    manifest = new Map(m.assets.map((a: Asset) => ["sha256:" + a.sha256, a]));
  }
  return manifest!;
}

const fontCache = new Map<string, string>();
function fontFile(family: string): string {
  if (fontCache.has(family)) return fontCache.get(family)!;
  let src = "";
  try {
    src = execFileSync("fc-match", ["-f", "%{file}", family], { encoding: "utf8" }).trim();
  } catch {}
  if (!src) src = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf";
  const dir = join(RENDERS, ".fonts");
  mkdirSync(dir, { recursive: true });
  const link = join(dir, family.replace(/[^A-Za-z0-9]/g, "_") + ".ttf");   // filtergraph-safe path
  if (!existsSync(link)) symlinkSync(src, link);
  fontCache.set(family, link);
  return link;
}

const f = (x: number) => x.toFixed(3);
const frames = (s: number) => Math.round(s * FPS);

function grade(effects: Effect[]): string {
  const parts: string[] = [];
  for (const e of effects) {
    const i = Number(e.params.intensity ?? 0.6);
    if (e.type === "color_grade") {
      switch (e.params.look) {
        case "warm_film": parts.push(`colorbalance=rm=${f(0.25 * i)}:bm=${f(-0.25 * i)}:rh=${f(0.1 * i)}`, `eq=saturation=${f(1 - 0.15 * i)}`); break;
        case "teal_orange": parts.push(`colorbalance=rs=${f(-0.25 * i)}:bs=${f(0.3 * i)}:rh=${f(0.3 * i)}:bh=${f(-0.25 * i)}`); break;
        case "high_contrast": parts.push(`eq=contrast=${f(1 + 0.6 * i)}:saturation=${f(1 + 0.2 * i)}`); break;
        case "pastel": parts.push(`eq=saturation=${f(1 - 0.4 * i)}:brightness=${f(0.06 * i)}`); break;
        case "neutral_clean": parts.push(`eq=contrast=1.05`); break;
      }
    } else if (e.type === "blur") parts.push(`boxblur=${Math.max(1, Math.round(4 * i))}`);
    else if (e.type === "vignette") parts.push("vignette");
    else if (e.type === "grain") parts.push(`noise=alls=${Math.round(20 * i)}:allf=t`);
  }
  return parts.length ? "," + parts.join(",") : "";
}

export interface RenderResult { file: string; duration: number; args: string[] }

export function buildRender(tl: Timeline, out: string) {
  const A = assets();
  const inputs: string[] = [];
  const graph: string[] = [];
  const total = timelineDuration(tl);
  const totalF = frames(total);
  const fxFor = (id: string) => tl.effects.filter((e) => e.target_id === id);
  const addInput = (c: Clip) => {
    const a = A.get(c.asset_hash);
    if (!a) throw new Error(`asset not in manifest: ${c.asset_hash}`);
    inputs.push("-i", join(MEDIA, a.path));
    return { idx: inputs.length / 2 - 1, asset: a };
  };
  const audio: string[] = [];
  const pushAudio = (idx: number, c: Clip) => {
    const lbl = `a${audio.length}`;
    const ms = Math.round(c.timeline_start * 1000);
    graph.push(`[${idx}:a]atrim=start=${f(c.src_in)}:end=${f(c.src_out)},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=mono,volume=${f(c.volume)},adelay=${ms}|${ms}[${lbl}]`);
    audio.push(`[${lbl}]`);
  };
  const vtrack = (id: string) => tl.tracks.find((t) => t.id === id);

  // ---- V1 sequence
  const v1 = [...(tl.tracks.find((t) => t.kind === "video")?.clips ?? [])].sort((a, b) => a.timeline_start - b.timeline_start);
  const v1Id = tl.tracks.find((t) => t.kind === "video")?.id;
  let seg = 0, cur = "", curF = 0, prevClip: Clip | null = null;
  const gap = (nF: number) => {
    const l = `g${seg++}`;
    graph.push(`color=c=black:s=${W}x${H}:r=${FPS}:d=${f(nF / FPS)},format=yuv420p,setsar=1,settb=1/${FPS}[${l}]`);
    return l;
  };
  const join2 = (a: string, b: string) => {
    const l = `s${seg++}`;
    graph.push(`[${a}][${b}]concat=n=2:v=1:a=0,settb=1/${FPS}[${l}]`);
    return l;
  };
  for (const c of v1) {
    const startF = frames(c.timeline_start), endF = frames(clipEnd(c));
    const { idx, asset } = addInput(c);
    const nF = endF - startF;
    const l = `c${seg++}`;
    graph.push(`[${idx}:v]trim=start=${f(c.src_in)}:end=${f(c.src_in + nF / FPS / (c.speed || 1))},setpts=PTS-STARTPTS,fps=${FPS},scale=${W}:${H},setsar=1,format=yuv420p${grade(fxFor(c.id))},trim=end_frame=${nF},settb=1/${FPS}[${l}]`);
    if (asset.has_audio) pushAudio(idx, c);
    const tr = prevClip ? tl.transitions.find((x) => x.from_clip === prevClip!.id && x.to_clip === c.id) : undefined;
    if (!cur) {
      cur = startF > 0 ? join2(gap(startF), l) : l;
    } else if (startF > curF) {
      cur = join2(join2(cur, gap(startF - curF)), l);
    } else if (tr && XFADE[tr.type] && startF === curF) {
      const dF = Math.max(2, Math.min(frames(tr.duration), nF - 1, curF - 1));
      const half = dF / 2 / FPS;
      const a = `p${seg++}`, b = `q${seg++}`, o = `x${seg++}`;
      graph.push(`[${cur}]tpad=stop_mode=clone:stop_duration=${f(half)}[${a}]`);
      graph.push(`[${l}]tpad=start_mode=clone:start_duration=${f(half)}[${b}]`);
      graph.push(`[${a}][${b}]xfade=transition=${XFADE[tr.type]}:duration=${f(dF / FPS)}:offset=${f(curF / FPS - half)},settb=1/${FPS}[${o}]`);
      cur = o;
    } else {
      cur = join2(cur, l);
    }
    curF = Math.max(curF, endF);
    prevClip = c;
  }
  if (!cur) { cur = gap(totalF); curF = totalF; }
  if (curF < totalF) cur = join2(cur, gap(totalF - curF));

  // ---- other video tracks: overlays
  for (const t of tl.tracks.filter((t) => t.kind === "video" && t.id !== v1Id)) {
    for (const c of t.clips) {
      const { idx, asset } = addInput(c);
      const l = `o${seg++}`, o = `v${seg++}`;
      graph.push(`[${idx}:v]trim=start=${f(c.src_in)}:end=${f(c.src_out)},setpts=PTS-STARTPTS+${f(c.timeline_start)}/TB,fps=${FPS},scale=${W}:${H},setsar=1,format=yuv420p${grade(fxFor(c.id))}[${l}]`);
      graph.push(`[${cur}][${l}]overlay=eof_action=pass:enable='between(t,${f(c.timeline_start)},${f(clipEnd(c))})'[${o}]`);
      cur = o;
      if (asset.has_audio) pushAudio(idx, c);
    }
  }
  // ---- text
  const tmp = join(RENDERS, ".text");
  mkdirSync(tmp, { recursive: true });
  for (const x of tl.text_layers) {
    const file = join(tmp, `${x.id}_${Buffer.from(x.text).toString("hex").slice(0, 24)}.txt`);
    const shown = x.role === "captions" ? `[captions: ${(x.style.preset ?? "default").replace(/_/g, " ")}]` : x.text;
    writeFileSync(file, shown, "utf8");
    const size = Math.round((x.role === "captions" ? 22 : x.role === "lower_third" ? 26 : 44) * 1);
    const pos = x.role === "lower_third" ? "x=30:y=h-th-60" : x.role === "captions" ? "x=(w-tw)/2:y=h-th-24" : "x=(w-tw)/2:y=(h-th)/2";
    const color = /^#[0-9A-Fa-f]{6}$/.test(x.style.color) ? "0x" + x.style.color.slice(1) : "white";
    const o = `t${seg++}`;
    graph.push(`[${cur}]drawtext=fontfile=${fontFile(x.style.font)}:textfile=${file}:fontsize=${size}:fontcolor=${color}:box=1:boxcolor=black@0.45:boxborderw=8:${pos}:enable='between(t,${f(x.start)},${f(x.start + x.duration)})'[${o}]`);
    cur = o;
  }
  graph.push(`[${cur}]trim=end_frame=${totalF},setpts=PTS-STARTPTS,format=yuv420p[vout]`);

  // ---- audio tracks
  for (const t of tl.tracks.filter((t) => t.kind === "audio")) for (const c of t.clips) {
    const { idx } = addInput(c);
    pushAudio(idx, c);
  }
  graph.push(`anullsrc=r=48000:cl=mono,atrim=0:${f(totalF / FPS)}[asil]`);
  graph.push(`[asil]${audio.join("")}amix=inputs=${audio.length + 1}:duration=first:normalize=0,atrim=0:${f(totalF / FPS)}[aout]`);
  void vtrack;
  const script = out.replace(/\.mp4$/, "") + ".filtergraph.txt";
  const args = ["-hide_banner", "-loglevel", "error", "-y", ...inputs, "-filter_complex_script", script,
    "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-r", String(FPS), out];
  return { graph: graph.join(";\n"), script, args, total: totalF / FPS };
}

export function probeDuration(file: string): number {
  const r = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file], { encoding: "utf8" });
  return Number(Number(r.trim()).toFixed(3));
}

export function render(tl: Timeline, out: string, opts: { keepScript?: boolean } = {}): RenderResult {
  mkdirSync(dirname(out), { recursive: true });
  const b = buildRender(tl, out);
  writeFileSync(b.script, b.graph + "\n", "utf8");
  const r = spawnSync("ffmpeg", b.args, { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`ffmpeg failed (${r.status}): ${r.stderr?.slice(-2000)}`);
  if (!opts.keepScript) { try { unlinkSync(b.script); } catch {} }
  return { file: out, duration: probeDuration(out), args: b.args };
}

/** Render cached by timeline hash (used by the web API). */
export function renderCached(tl: Timeline, timelineHash: string): RenderResult {
  const out = join(RENDERS, "cache", `${timelineHash}.mp4`);
  if (existsSync(out)) return { file: out, duration: probeDuration(out), args: [] };
  return render(tl, out);
}
