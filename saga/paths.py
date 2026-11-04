"""集中管理所有檔案路徑。

series/<slug>/          ← 進版控（劇本、設定、角色定妝圖）
    bible.json          世界觀、角色、場景、畫風
    outline.json        全劇分集大綱
    episodes/ep01.json  單集分鏡劇本
    refs/<char>.jpg     角色定妝參考圖（確保 60 集臉一致）
build/<slug>/           ← 不進版控（音檔、圖、影片）
    ep01/audio/*.mp3
    ep01/shots/*.png|mp4
    ep01.mp4            成片
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "series"
BUILD_DIR = ROOT / "build"


def series_dir(slug: str) -> Path:
    return SERIES_DIR / slug


def bible_path(slug: str) -> Path:
    return series_dir(slug) / "bible.json"


def outline_path(slug: str) -> Path:
    return series_dir(slug) / "outline.json"


def episode_path(slug: str, ep: int) -> Path:
    return series_dir(slug) / "episodes" / f"ep{ep:02d}.json"


def refs_dir(slug: str) -> Path:
    return series_dir(slug) / "refs"


def build_ep_dir(slug: str, ep: int) -> Path:
    return BUILD_DIR / slug / f"ep{ep:02d}"


def final_video_path(slug: str, ep: int) -> Path:
    return BUILD_DIR / slug / f"{slug}-ep{ep:02d}.mp4"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_eps(spec: str, total: int | None = None) -> list[int]:
    """'1-5' / '3' / '1,4,7-9'"""
    spec = spec.strip()
    out: list[int] = []
    for part in re.split(r"[,\s]+", spec):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    if total:
        out = [e for e in out if 1 <= e <= total]
    return sorted(set(out))
