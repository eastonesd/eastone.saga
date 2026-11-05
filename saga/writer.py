"""編劇部：劇本聖經 → 分集大綱 → 單集分鏡。"""
from __future__ import annotations

import random

from . import paths, prompts
from .llm import generate_json

OUTLINE_BATCH = 10


def _normalize_bible(b: dict, *, slug: str, idea: str, episodes: int, seconds: int, style: str) -> dict:
    voice_keys = list(prompts.VOICES)
    used: set[str] = set()
    for i, c in enumerate(b["characters"]):
        c["id"] = str(c.get("id") or f"char{i + 1}").strip().lower().replace(" ", "_")
        if c.get("voice") not in prompts.VOICES:
            female = str(c.get("gender", "")).lower().startswith("f")
            pool = [k for k in voice_keys if k.startswith("女聲" if female else "男聲")]
            c["voice"] = next((k for k in pool if k not in used), pool[0])
        used.add(c["voice"])
        c.setdefault("seed", random.Random(c["id"]).randint(1, 2**31 - 1))
    for i, loc in enumerate(b["locations"]):
        loc["id"] = str(loc.get("id") or f"loc{i + 1}").strip().lower().replace(" ", "_")
    if b.get("narrator_voice") not in prompts.VOICES:
        b["narrator_voice"] = "旁白-女"
    if not b.get("tts"):
        from .tts import auto_cast, available_voices
        if available_voices():
            b["tts"] = {"engine": "indextts", "cast": auto_cast(b)}
    b.update({"slug": slug, "idea": idea, "episode_count": episodes,
              "episode_seconds": seconds, "style_choice": style})
    return b


def plan_series(slug: str, idea: str, *, genre: str = "", style: str = "", episodes: int = 60,
                seconds: int = 90, mock: bool = False) -> dict:
    print(f"▶ 劇本聖經：{slug}", flush=True)
    bible = generate_json(prompts.bible_prompt(idea, genre, style, episodes, seconds), kind="bible", mock=mock)
    bible = _normalize_bible(bible, slug=slug, idea=idea, episodes=episodes, seconds=seconds, style=style)
    paths.write_json(paths.bible_path(slug), bible)

    eps: list[dict] = []
    for start in range(1, episodes + 1, OUTLINE_BATCH):
        end = min(episodes, start + OUTLINE_BATCH - 1)
        print(f"▶ 大綱：第 {start}～{end} 集", flush=True)
        out = generate_json(prompts.outline_prompt(bible, start, end, episodes, eps), kind="outline",
                            mock=mock, context={"start": start, "end": end})
        got = {int(e["ep"]): e for e in out["episodes"] if start <= int(e.get("ep", 0)) <= end}
        for n in range(start, end + 1):
            e = got.get(n) or {"ep": n, "title": f"第{n}集", "summary": "（待補）", "hook": "", "cliffhanger": ""}
            e["ep"] = n
            eps.append(e)
        paths.write_json(paths.outline_path(slug), {"episodes": eps})
    return bible


def _normalize_episode(ep_data: dict, bible: dict, ep: int) -> dict:
    char_ids = {c["id"] for c in bible["characters"]}
    loc_ids = [l["id"] for l in bible["locations"]]
    shots = []
    for i, s in enumerate(ep_data.get("shots", [])):
        s["id"] = f"s{i + 1:02d}"
        if s.get("location") not in loc_ids:
            s["location"] = loc_ids[0]
        s["characters"] = [c for c in s.get("characters", []) if c in char_ids]
        if s.get("motion") not in {"zoom_in", "zoom_out", "pan_left", "pan_right", "static"}:
            s["motion"] = "zoom_in"
        lines = []
        for ln in s.get("lines", []) or []:
            text = str(ln.get("text", "")).strip()
            if not text:
                continue
            spk = ln.get("speaker")
            emo = ln.get("emo") if ln.get("emo") in ("calm", "angry", "sad", "happy", "afraid", "surprised", "disdain") else None
            lines.append({"speaker": spk if spk in char_ids else "narrator", "text": text, **({"emo": emo} if emo else {})})
        s["lines"] = lines
        s["key"] = bool(s.get("key"))
        shots.append(s)
    if not shots:
        raise ValueError(f"第 {ep} 集沒有任何鏡頭")
    return {"ep": ep, "title": ep_data.get("title") or f"第{ep}集", "shots": shots}


def write_episode(slug: str, ep: int, *, mock: bool = False, force: bool = False) -> dict:
    path = paths.episode_path(slug, ep)
    if path.exists() and not force:
        return paths.read_json(path)
    bible = paths.read_json(paths.bible_path(slug))
    outline = paths.read_json(paths.outline_path(slug))
    print(f"▶ 分鏡劇本：第 {ep} 集", flush=True)
    raw = generate_json(prompts.episode_prompt(bible, outline, ep, bible["episode_seconds"]),
                        kind="episode", mock=mock, context={"ep": ep})
    data = _normalize_episode(raw, bible, ep)
    paths.write_json(path, data)
    return data


def write_episodes(slug: str, eps: list[int], *, mock: bool = False, force: bool = False,
                   workers: int = 2) -> list[int]:
    """平行寫多集分鏡（每集只依賴大綱，彼此獨立）。回傳失敗的集數。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [e for e in eps if force or not paths.episode_path(slug, e).exists()]
    if not todo:
        return []
    print(f"▶ 平行撰寫 {len(todo)} 集分鏡（{workers} 路）", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(write_episode, slug, e, mock=mock, force=force): e for e in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:  # 單集失敗不拖垮整批
                print(f"  ⚠ 第 {futs[f]} 集分鏡失敗：{e}", flush=True)
                failed.append(futs[f])
    return sorted(failed)
