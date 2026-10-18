"""Generate tiny, labelled test media with ffmpeg and write data/media/manifest.json.

Each asset is a real file so renders work later. The manifest stores the
sha256 of each file; timelines reference media by that hash only.

ffmpeg output is made as reproducible as practical (-threads 1, bitexact
flags, metadata stripped). Byte-identical output across different ffmpeg
builds or fonts is NOT guaranteed, so the committed files + manifest are the
reference copy. Re-running on the same machine reproduces the same hashes.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from common import DATA, sha256_file, write_json

OUT = DATA / "media"
FILES = OUT / "files"
W, H, FPS = 640, 360, 30

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

# id, kind, role, filename, duration, video source, audio
ASSETS = [
    dict(id="a_cam", kind="video", role="talking_head_a_cam", file="a_cam_talking_head.mp4",
         duration=12.0, src="testsrc2", tint="0x1f4e79", label="A-CAM  talking head", tone=220),
    dict(id="b_cam", kind="video", role="talking_head_b_cam", file="b_cam_talking_head.mp4",
         duration=12.0, src="testsrc2", tint="0x7a1f4e", label="B-CAM  talking head", tone=220),
    dict(id="broll_city", kind="video", role="broll", file="broll_city.mp4",
         duration=6.0, src="smptebars", tint=None, label="B-ROLL  city", tone=None),
    dict(id="broll_product", kind="video", role="broll", file="broll_product.mp4",
         duration=6.0, src="color", tint="0x2e7d32", label="B-ROLL  product closeup", tone=None),
    dict(id="broll_desk", kind="video", role="broll", file="broll_desk.mp4",
         duration=5.0, src="color", tint="0x6d4c41", label="B-ROLL  desk setup", tone=None),
    dict(id="broll_screen", kind="video", role="screen_recording", file="broll_screen_recording.mp4",
         duration=8.0, src="testsrc", tint=None, label="SCREEN  product demo", tone=None),
    dict(id="title_card", kind="video", role="graphic", file="title_card.mp4",
         duration=3.0, src="color", tint="0x111111", label="TITLE CARD", tone=None),
    dict(id="music_upbeat", kind="audio", role="music", file="music_upbeat.m4a",
         duration=30.0, chord=[261.63, 329.63, 392.0], beat=True),
    dict(id="music_calm", kind="audio", role="music", file="music_calm.m4a",
         duration=30.0, chord=[196.0, 246.94, 293.66], beat=False),
    dict(id="sfx_whoosh", kind="audio", role="sfx", file="sfx_whoosh.wav",
         duration=0.8, sweep=True),
    dict(id="sfx_click", kind="audio", role="sfx", file="sfx_click.wav",
         duration=0.15, sweep=False),
]

COMMON = ["-hide_banner", "-loglevel", "error", "-y", "-threads", "1"]
BITEXACT = ["-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]


def font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    sys.exit("No usable TTF font found for drawtext; edit FONT_CANDIDATES.")


def esc(s):
    return s.replace(":", r"\:").replace("'", r"\'")


def video_cmd(a, out, fontfile):
    d = a["duration"]
    size = f"{W}x{H}"
    if a["src"] == "color":
        vsrc = f"color=c={a['tint']}:s={size}:r={FPS}:d={d}"
    else:
        vsrc = f"{a['src']}=s={size}:r={FPS}:d={d}"
    draw = (
        f"drawtext=fontfile='{fontfile}':text='{esc(a['label'])}':fontsize=34:fontcolor=white:"
        f"box=1:boxcolor=black@0.6:boxborderw=10:x=(w-tw)/2:y=40,"
        f"drawtext=fontfile='{fontfile}':text='{esc(a['id'])}  %{{pts\\:hms}}':fontsize=24:fontcolor=yellow:"
        f"box=1:boxcolor=black@0.6:boxborderw=6:x=(w-tw)/2:y=h-70"
    )
    if a.get("tint") and a["src"] != "color":
        draw = f"drawbox=x=0:y=0:w=iw:h=ih:color={a['tint']}@0.35:t=fill," + draw
    cmd = ["ffmpeg", *COMMON, "-f", "lavfi", "-i", vsrc]
    if a.get("tone"):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={a['tone']}:sample_rate=48000:duration={d}"]
    cmd += ["-vf", draw, "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
            "-pix_fmt", "yuv420p", "-g", str(FPS), "-x264-params", "threads=1"]
    if a.get("tone"):
        cmd += ["-c:a", "aac", "-b:a", "48k", "-ac", "1"]
    cmd += ["-t", str(d), *BITEXACT, "-movflags", "+faststart", str(out)]
    return cmd


def audio_cmd(a, out):
    d = a["duration"]
    if a["role"] == "music":
        ins, n = [], len(a["chord"])
        for f in a["chord"]:
            ins += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={d}"]
        filt = f"amix=inputs={n}:normalize=1"
        if a["beat"]:
            filt += ",volume='if(lt(mod(t,0.5),0.12),1.0,0.45)':eval=frame"
        filt += f",afade=t=in:d=0.5,afade=t=out:st={d-1}:d=1"
        return ["ffmpeg", *COMMON, *ins, "-filter_complex", filt, "-c:a", "aac", "-b:a", "48k",
                "-ac", "1", "-t", str(d), *BITEXACT, str(out)]
    if a["sweep"]:
        src = f"aevalsrc='0.6*sin(2*PI*(300+1500*t)*t)':s=48000:d={d}"
        filt = f"afade=t=in:d=0.2,afade=t=out:st={d-0.3}:d=0.3"
    else:
        src = f"sine=frequency=2000:sample_rate=48000:duration={d}"
        filt = f"afade=t=out:st=0.02:d={d-0.02}"
    return ["ffmpeg", *COMMON, "-f", "lavfi", "-i", src, "-af", filt, "-c:a", "pcm_s16le",
            "-ac", "1", "-t", str(d), *BITEXACT, str(out)]


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return round(float(r.stdout.strip()), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-encode even if files exist")
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")
    FILES.mkdir(parents=True, exist_ok=True)
    fontfile = font()
    manifest = {"note": "Synthetic test media generated by generators/gen_media.py. "
                        "Timelines reference assets by sha256 only.",
                "settings": {"width": W, "height": H, "fps": FPS, "sample_rate": 48000},
                "assets": []}
    for a in ASSETS:
        out = FILES / a["file"]
        if args.force or not out.exists():
            cmd = video_cmd(a, out, fontfile) if a["kind"] == "video" else audio_cmd(a, out)
            subprocess.run(cmd, check=True)
        manifest["assets"].append({
            "asset_id": a["id"],
            "kind": a["kind"],
            "role": a["role"],
            "path": f"files/{a['file']}",
            "sha256": sha256_file(out),
            "bytes": out.stat().st_size,
            "nominal_duration": a["duration"],
            "probed_duration": probe_duration(out),
            "has_audio": a["kind"] == "audio" or bool(a.get("tone")),
            "label": a.get("label", a["id"]),
        })
    write_json(OUT / "manifest.json", manifest)
    total = sum(x["bytes"] for x in manifest["assets"])
    print(f"[media] {len(manifest['assets'])} assets, {total/1e6:.2f} MB total")
    for x in manifest["assets"]:
        print(f"  {x['asset_id']:<14} {x['kind']:<5} {x['probed_duration']:>6}s {x['bytes']/1e3:>7.1f} KB  {x['sha256'][:12]}")


if __name__ == "__main__":
    main()
