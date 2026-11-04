"""剪輯部：運鏡 + 配音時間軸 + 字幕 + 配樂 → 9:16 成片（純 FFmpeg，不用 GPU）。"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from . import paths
from .voice import audio_duration

W, H, FPS = 1080, 1920, 30
LEAD, GAP, TAIL = 0.15, 0.25, 0.45
MIN_SHOT = 2.2
WRAP = 14
SUB_STYLE = ("FontName=Noto Sans CJK TC,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=1.3,Shadow=0.6,MarginV=42")


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗：{r.stderr[-1500:]}")


def _motion_filter(motion: str, frames: int) -> str:
    n = max(frames, 1)
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    z, x, y = {
        "zoom_in": (f"1+0.12*on/{n}", cx, cy),
        "zoom_out": (f"1.12-0.12*on/{n}", cx, cy),
        "pan_left": ("1.12", f"(iw-iw/zoom)*(1-on/{n})", cy),
        "pan_right": ("1.12", f"(iw-iw/zoom)*on/{n}", cy),
    }.get(motion, (f"1+0.03*on/{n}", cx, cy))
    return (f"scale={int(W * 1.5)}:{int(H * 1.5)}:force_original_aspect_ratio=increase,"
            f"crop={int(W * 1.5)}:{int(H * 1.5)},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},format=yuv420p")


def _render_shot(src: Path, motion: str, dur: float, out: Path) -> None:
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    if src.suffix == ".mp4":
        clip = audio_duration(src) or dur
        slow = min(max(dur / clip, 1.0), 1.6)
        vf = (f"setpts={slow:.3f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.2f},format=yuv420p")
        _ff("-i", str(src), "-vf", vf, "-t", f"{dur:.3f}", *enc, str(out))
    else:
        frames = round(dur * FPS)
        _ff("-i", str(src), "-vf", _motion_filter(motion, frames), "-frames:v", str(frames), *enc, str(out))


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str) -> str:
    return "\n".join(text[i:i + WRAP] for i in range(0, len(text), WRAP))


def assemble_episode(slug: str, ep: int) -> Path:
    ep_dir = paths.build_ep_dir(slug, ep)
    timing = paths.read_json(ep_dir / "audio" / "timing.json")
    shot_dir, tmp = ep_dir / "shots", ep_dir / "_clips"
    tmp.mkdir(parents=True, exist_ok=True)
    print(f"▶ 剪輯：第 {ep} 集", flush=True)

    clips, cues, audio_inputs = [], [], []
    t = 0.0
    cues.append((0.0, 2.6, "{\\an8}" + f"第{ep}集｜{timing['title']}"))
    for s in timing["shots"]:
        cursor = t + LEAD
        for i, ln in enumerate(s["lines"]):
            audio_inputs.append((ep_dir / "audio" / ln["audio"], cursor))
            cues.append((cursor, cursor + ln["duration"], _wrap(ln["text"])))
            cursor += ln["duration"] + (GAP if i < len(s["lines"]) - 1 else 0)
        dur = max(MIN_SHOT, cursor - t + TAIL)
        src = shot_dir / f"{s['id']}.mp4"
        if not src.exists():
            src = shot_dir / f"{s['id']}.png"
        if not src.exists():
            raise FileNotFoundError(f"缺少鏡頭素材：{src}")
        clip = tmp / f"{s['id']}.mp4"
        _render_shot(src, s.get("motion", "zoom_in"), dur, clip)
        clips.append(clip)
        t += dur
    total = t

    # 影像串接
    concat_list = tmp / "list.txt"
    concat_list.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    video = tmp / "video.mp4"
    _ff("-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video))

    # 字幕
    srt = ep_dir / f"ep{ep:02d}.srt"
    srt.write_text("".join(f"{i + 1}\n{_srt_time(a)} --> {_srt_time(b)}\n{txt}\n\n"
                           for i, (a, b, txt) in enumerate(cues)), encoding="utf-8")

    # 配音時間軸 + 配樂
    args = ["-i", str(video)]
    filters, labels = [], []
    for idx, (path, start) in enumerate(audio_inputs, start=1):
        args += ["-i", str(path)]
        ms = int(start * 1000)
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{idx}]")
        labels.append(f"[a{idx}]")
    bgms = sorted((paths.ROOT / "assets" / "bgm").glob("*.mp3"))
    if bgms:
        pick = bgms[int(hashlib.md5(f"{slug}{ep}".encode()).hexdigest(), 16) % len(bgms)]
        args += ["-stream_loop", "-1", "-i", str(pick)]
        bi = len(audio_inputs) + 1
        filters.append(f"[{bi}:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.12[bgm]")
        labels.append("[bgm]")
    if not labels:
        args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        labels.append(f"[{len(audio_inputs) + 1}:a]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                   f"atrim=0:{total:.3f},afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8[aout]")
    sub_path = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"[0:v]subtitles='{sub_path}':force_style='{SUB_STYLE}'[vout]")

    out = paths.final_video_path(slug, ep)
    _ff(*args, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))
    print(f"  ✔ {out.name}（{total:.0f} 秒）", flush=True)
    return out
