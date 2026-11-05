"""剪輯部：運鏡＋故障轉場＋配音時間軸＋字幕＋角色名牌＋配樂 → 成片（純 FFmpeg，不用 GPU）。

畫面比例跟著素材走：橫圖 → 1920×1080；直圖 → 1080×1920。
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from PIL import Image

from . import paths
from .voice import audio_duration, spoken_text

FPS = 30
LEAD, GAP, TAIL = 0.1, 0.2, 0.25
MIN_SHOT = 1.6
GLITCH_S = 0.18


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗：{r.stderr[-1500:]}")


def _motion_filter(motion: str, frames: int, W: int, H: int) -> str:
    n = max(frames, 1)
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    z, x, y = {
        "zoom_in": (f"1+0.10*on/{n}", cx, cy),
        "zoom_out": (f"1.10-0.10*on/{n}", cx, cy),
        "pan_left": ("1.10", f"(iw-iw/zoom)*(1-on/{n})", cy),
        "pan_right": ("1.10", f"(iw-iw/zoom)*on/{n}", cy),
    }.get(motion, (f"1+0.03*on/{n}", cx, cy))
    return (f"scale={int(W * 1.25)}:{int(H * 1.25)}:force_original_aspect_ratio=increase,"
            f"crop={int(W * 1.25)}:{int(H * 1.25)},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS}")


def _glitch() -> str:
    """鏡頭開頭 0.18 秒的故障風轉場（色版錯位＋雜訊）"""
    on = f"enable='lt(t,{GLITCH_S})'"
    return f",rgbashift=rh=-18:bh=18:{on},noise=alls=45:allf=t:{on}"


def _render_shot(src: Path, motion: str, dur: float, out: Path, W: int, H: int, glitch: bool) -> None:
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    fx = _glitch() if glitch else ""
    if src.suffix == ".mp4":
        clip = audio_duration(src) or dur
        slow = min(max(dur / clip, 1.0), 1.6)
        vf = (f"setpts={slow:.3f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.2f}{fx},format=yuv420p")
        _ff("-i", str(src), "-vf", vf, "-t", f"{dur:.3f}", *enc, str(out))
    else:
        frames = round(dur * FPS)
        _ff("-i", str(src), "-vf", _motion_filter(motion, frames, W, H) + fx + ",format=yuv420p",
            "-frames:v", str(frames), *enc, str(out))


# ── ASS 字幕（台詞、角色名牌、集數） ──────────────────────────

def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_header(W: int, H: int) -> str:
    land = W > H
    sub = 46 if land else 58
    wrap_margin = int(W * 0.12)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Line,Noto Sans CJK TC,{sub},&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,1,0,1,2.2,1.5,2,{wrap_margin},{wrap_margin},{int(H * 0.07)},1
Style: Name,Noto Serif CJK TC,{int(sub * 2.1)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,6,0,1,2,3,4,{int(W * 0.06)},0,0,1
Style: Tag,Noto Sans CJK TC,{int(sub * 0.62)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,0,0,0,0,100,100,2,0,3,6,0,4,{int(W * 0.06)},0,0,1
Style: Ep,Noto Sans CJK TC,{int(sub * 0.7)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,1.5,1,7,{int(W * 0.03)},0,{int(H * 0.04)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _first_appearances(slug: str, ep: int) -> set[str]:
    """在這一集才第一次登場的角色（前面集數都沒出現過）→ 顯示名牌"""
    seen: set[str] = set()
    for e in range(1, ep):
        p = paths.episode_path(slug, e)
        if p.exists():
            for s in paths.read_json(p)["shots"]:
                seen.update(s.get("characters", []))
    now = {c for s in paths.read_json(paths.episode_path(slug, ep))["shots"] for c in s.get("characters", [])}
    return now - seen


def _shot_size(path: Path) -> tuple[int, int]:
    if path.suffix == ".png":
        return Image.open(path).size
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    w, h = (int(x) for x in out.split(","))
    return w, h


def assemble_episode(slug: str, ep: int) -> Path:
    ep_dir = paths.build_ep_dir(slug, ep)
    timing = paths.read_json(ep_dir / "audio" / "timing.json")
    bible = paths.read_json(paths.bible_path(slug))
    chars = {c["id"]: c for c in bible["characters"]}
    shot_dir, tmp = ep_dir / "shots", ep_dir / "_clips"
    tmp.mkdir(parents=True, exist_ok=True)
    print(f"▶ 剪輯：第 {ep} 集", flush=True)

    def src_of(s):
        for suf in (".mp4", ".png"):
            p = shot_dir / f"{s['id']}{suf}"
            if p.exists():
                return p
        raise FileNotFoundError(f"缺少鏡頭素材：{shot_dir / s['id']}")

    sw, sh = _shot_size(src_of(timing["shots"][0]))
    W, H = (1920, 1080) if sw > sh else (1080, 1920)

    debut = _first_appearances(slug, ep)
    events = [f"Dialogue: 2,{_ass_time(0)},{_ass_time(2.8)},Ep,,0,0,0,,第{ep}集　{timing['title']}"]
    clips, audio_inputs = [], []
    t, prev_loc = 0.0, None
    for idx, s in enumerate(timing["shots"]):
        cursor = t + LEAD
        for i, ln in enumerate(s["lines"]):
            audio_inputs.append((ep_dir / "audio" / ln["audio"], cursor))
            if not ln.get("sfx"):
                text = (spoken_text(ln["text"]) or ln["text"]).replace("\n", " ")
                events.append(f"Dialogue: 1,{_ass_time(cursor)},{_ass_time(cursor + ln['duration'])},Line,,0,0,0,,{text}")
            cursor += ln["duration"] + (GAP if i < len(s["lines"]) - 1 else 0)
        dur = max(MIN_SHOT, cursor - t + TAIL)
        # 角色名牌：首次登場的主角色
        who = next((c for c in s.get("characters", []) if c in debut and c in chars), None)
        if who:
            debut.discard(who)
            c = chars[who]
            tag = c.get("tagline") or c.get("role", "")
            x, y = int(W * 0.06), int(H * 0.52)
            a, b = _ass_time(t + 0.2), _ass_time(t + min(dur, 2.8))
            events.append(f"Dialogue: 3,{a},{b},Name,,0,0,0,,{{\\pos({x},{y})\\fad(200,250)}}{c['name']}")
            events.append(f"Dialogue: 3,{a},{b},Tag,,0,0,0,,{{\\pos({x + 6},{y + int(H * 0.085)})\\fad(200,250)}} {tag} ")
        # 換場景或高潮鏡頭 → 故障轉場
        glitch = idx > 0 and (s.get("key") or s.get("location") != prev_loc)
        prev_loc = s.get("location")
        clip = tmp / f"{s['id']}.mp4"
        _render_shot(src_of(s), s.get("motion", "zoom_in"), dur, clip, W, H, bool(glitch))
        clips.append(clip)
        t += dur
    total = t

    concat_list = tmp / "list.txt"
    concat_list.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    video = tmp / "video.mp4"
    _ff("-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video))

    ass = ep_dir / f"ep{ep:02d}.ass"
    ass.write_text(_ass_header(W, H) + "\n".join(events) + "\n", encoding="utf-8")

    # 配音時間軸＋配樂（配樂在有人說話時自動壓低）
    args = ["-i", str(video)]
    filters, voices = [], []
    for i, (path, start) in enumerate(audio_inputs, start=1):
        args += ["-i", str(path)]
        ms = int(start * 1000)
        filters.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
        voices.append(f"[a{i}]")
    n_in = len(audio_inputs)
    if voices:
        filters.append(f"{''.join(voices)}amix=inputs={len(voices)}:normalize=0:duration=longest,"
                       f"apad=whole_dur={total:.3f}[vox]")
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        n_in += 1
        filters.append(f"[{n_in}:a]anull[vox]")
    bgms = sorted((paths.ROOT / "assets" / "bgm").glob("*.mp3"))
    if bgms:
        pick = bgms[int(hashlib.md5(f"{slug}{ep}".encode()).hexdigest(), 16) % len(bgms)]
        args += ["-stream_loop", "-1", "-i", str(pick)]
        bi = n_in + 1
        filters.append("[vox]asplit=2[vox1][vkey]")
        filters.append(f"[{bi}:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.35[bgm0]")
        filters.append("[bgm0][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[bgm]")
        filters.append(f"[vox1][bgm]amix=inputs=2:normalize=0:duration=first,atrim=0:{total:.3f},"
                       f"afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8[aout]")
    else:
        filters.append(f"[vox]atrim=0:{total:.3f},afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8[aout]")
    sub_path = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"[0:v]ass='{sub_path}'[vout]")

    out = paths.final_video_path(slug, ep)
    _ff(*args, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))
    _ff("-ss", f"{min(1.2, total / 2):.2f}", "-i", str(out), "-frames:v", "1",
        "-vf", "scale=360:-2", "-q:v", "4", str(out.with_suffix(".jpg")))
    print(f"  ✔ {out.name}（{total:.0f} 秒，{W}×{H}）", flush=True)
    return out
