"""直式包裝：把 16:9 成片轉成 9:16（TikTok／Reels／Shorts），不重新算圖、不花 GPU。

版面（1080×1920）：
  上 420px：劇名＋集數標題（模糊背景）
  中 1080×1080：原片置中裁切（原尺寸，字幕大小不變）
  下 420px：追更提示＋AI 生成標示（避開 TikTok 底部文案區）
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import paths

W, H, BAND = 1080, 1920, 1080
TOP = (H - BAND) // 2


def _esc(s: str) -> str:
    return str(s).replace("\\", "").replace("{", "（").replace("}", "）").replace("\n", " ")


def _ass(title: str, ep: int, ep_title: str, total: int) -> str:
    t_size = 92 if len(title) <= 10 else max(60, int(92 * 10 / len(title)))
    e_text = f"第 {ep} 集｜{ep_title}" if ep_title else f"第 {ep} 集"
    e_size = 50 if len(e_text) <= 16 else max(36, int(50 * 16 / len(e_text)))
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,Noto Serif CJK TC,{t_size},&H0037C8F5,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,4,0,1,4,3,8,30,30,200,1
Style: E,Noto Sans CJK TC,{e_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,1,0,0,0,100,100,2,0,1,2.5,2,8,30,30,330,1
Style: B,Noto Sans CJK TC,40,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,0,0,0,0,100,100,2,0,1,2,2,8,30,30,{TOP + BAND + 50},1
Style: A,Noto Sans CJK TC,28,&H00C8C8C8,&H00FFFFFF,&H00101010,&H96000000,0,0,0,0,100,100,1,0,1,1.5,1,8,30,30,{TOP + BAND + 115},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:00:00.00,T,,0,0,0,,《{_esc(title)}》
Dialogue: 0,0:00:00.00,9:00:00.00,E,,0,0,0,,{_esc(e_text)}
Dialogue: 0,0:00:00.00,9:00:00.00,B,,0,0,0,,追更看主頁｜全 {total} 集
Dialogue: 0,0:00:00.00,9:00:00.00,A,,0,0,0,,AI 生成內容
"""


def _assarg(name: str) -> str:
    import os
    fonts = os.path.expanduser("~/.local/share/fonts")
    return f"filename={name}:fontsdir={fonts}" if os.path.isdir(fonts) else name


INTRO_S = 1.5  # 片頭：SAGA.ai 徽章緩緩浮現


def _probe(src: Path) -> tuple[str, int, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,sample_rate,channels",
                          "-of", "json", str(src)], capture_output=True, text=True, check=True).stdout
    import json
    st = json.loads(out)["streams"]
    v = next(x for x in st if x["codec_type"] == "video")
    a = next((x for x in st if x["codec_type"] == "audio"), {"sample_rate": "44100", "channels": 2})
    return v["r_frame_rate"], int(a["sample_rate"]), "mono" if int(a.get("channels", 2)) == 1 else "stereo"


def wrap(src: Path, out: Path, *, title: str, ep: int, ep_title: str, total: int, intro: bool = True) -> Path:
    from .poster import brand_mark

    ass = out.with_suffix(".ass")
    ass.write_text(_ass(title, ep, ep_title, total), encoding="utf-8")
    fps, sr, cl = _probe(src)
    main = (f"[0:v]split[a][b];"
            f"[a]crop=ih:ih,scale={W}:{H},boxblur=30:3,eq=brightness=-0.25[bg];"
            f"[b]crop=ih:ih:(iw-ih)/2:0,scale={BAND}:{BAND}[fg];"
            f"[bg][fg]overlay=0:{TOP},ass={_assarg(ass.name)},setsar=1,format=yuv420p")
    inputs = ["-i", str(src.resolve())]
    if intro:
        mark = out.with_name(out.stem + "_mark.png")
        brand_mark(720).save(mark)
        inputs += ["-f", "lavfi", "-i", f"color=c=0x001227:s={W}x{H}:r={fps}:d={INTRO_S}",
                   "-loop", "1", "-framerate", fps, "-t", str(INTRO_S), "-i", mark.name,
                   "-f", "lavfi", "-t", str(INTRO_S), "-i", f"anullsrc=r={sr}:cl={cl}"]
        fc = (main + "[mv];"
              f"[2:v]format=rgba,fade=in:st=0.1:d=0.8:alpha=1[l];"
              f"[1:v][l]overlay=(W-w)/2:(H-h)/2-40:shortest=1,fade=out:st={INTRO_S - 0.3}:d=0.3,setsar=1,format=yuv420p[iv];"
              f"[0:a]aformat=sample_rates={sr}:channel_layouts={cl}[ma];"
              f"[3:a]aformat=sample_rates={sr}:channel_layouts={cl}[ia];"
              f"[iv][ia][mv][ma]concat=n=2:v=1:a=1[v][aout]")
        maps = ["-map", "[v]", "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    else:
        fc, maps = main, ["-c:a", "copy"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", fc, *maps,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(out.resolve())],
                   check=True, cwd=ass.parent)
    ass.unlink(missing_ok=True)
    if intro:
        mark.unlink(missing_ok=True)
    return out


def wrap_episode(slug: str, ep: int, src: Path, out_dir: Path) -> Path:
    bible = paths.read_json(paths.bible_path(slug))
    outline = {int(e["ep"]): e for e in paths.read_json(paths.outline_path(slug))["episodes"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}-ep{ep:02d}-v.mp4"
    return wrap(src, out, title=bible["title"], ep=ep, ep_title=outline.get(ep, {}).get("title", ""),
                total=bible["episode_count"])
