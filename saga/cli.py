"""指令入口。

  python -m saga.cli plan     --slug s1 --idea "..."          建立劇本聖經＋全劇大綱
  python -m saga.cli plan     --slug s1 --issue-file body.md  從 GitHub Issue 表單建立
  python -m saga.cli produce  --slug s1 --eps 1-5 [--video]   劇本→配音→算圖→剪輯
  python -m saga.cli summary  --slug s1                       輸出 Markdown 摘要（給 Issue 留言用）

任何指令加 --mock：不呼叫 Gemini／Kaggle／TTS，用假資料跑通流程。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

from . import paths


def parse_issue_form(body: str) -> dict:
    """解析 GitHub Issue 表單（### 標題 + 內容）"""
    fields: dict[str, str] = {}
    for m in re.finditer(r"^###\s+(.+?)\s*$\n(.*?)(?=^###\s|\Z)", body, re.S | re.M):
        val = m.group(2).strip()
        fields[m.group(1).strip()] = "" if val == "_No response_" else val
    def num(key, default):
        try:
            return int(re.sub(r"\D", "", fields.get(key, "")) or default)
        except ValueError:
            return default
    return {
        "idea": fields.get("靈感") or body.strip(),
        "genre": fields.get("類型", ""),
        "style": fields.get("畫風", ""),
        "episodes": max(1, min(num("集數", 60), 120)),
        "seconds": max(30, min(num("每集長度（秒）", 90), 300)),
    }


def cmd_plan(a) -> None:
    from .writer import plan_series

    if a.issue_file:
        opts = parse_issue_form(open(a.issue_file, encoding="utf-8").read())
    else:
        opts = {"idea": a.idea, "genre": a.genre, "style": a.style, "episodes": a.episodes, "seconds": a.seconds}
    if not opts["idea"]:
        sys.exit("需要 --idea 或 --issue-file")
    plan_series(a.slug, mock=a.mock, **opts)
    print(summary(a.slug))


def cmd_produce(a) -> None:
    """一批的完整生產：分鏡（平行）→ GPU 算圖 ∥ 配音（同時）→ 剪輯（平行）"""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from .assemble import assemble_episode
    from .render import render
    from .voice import voice_episode
    from .writer import write_episodes

    t0 = time.time()
    bible = paths.read_json(paths.bible_path(a.slug))
    wanted = paths.parse_eps(a.eps if a.eps != "all" else f"1-{bible['episode_count']}", bible["episode_count"])
    skip = set(paths.parse_eps(a.skip)) if a.skip else set()
    todo = [e for e in wanted if e not in skip]
    batch = todo[: a.batch]
    if not batch:
        print("▶ 指定範圍內的集數都已完成")
        _write_status(a, batch=[], done=[], remaining=[], partial=False)
        return
    print(f"▶ 本批：第 {batch[0]}～{batch[-1]} 集（共 {len(batch)} 集；之後還剩 {len(todo) - len(batch)} 集）", flush=True)

    if a.rewrite:
        for ep in batch:
            shutil.rmtree(paths.build_ep_dir(a.slug, ep), ignore_errors=True)
    failed_scripts = write_episodes(a.slug, batch, mock=a.mock, force=a.rewrite)
    batch = [e for e in batch if e not in failed_scripts]
    stamp = {"scripts": time.time() - t0}

    # GPU 算圖在背景跑，同時在前景錄配音
    result: dict = {}

    def gpu():
        try:
            result["manifest"] = render(a.slug, batch, mock=a.mock, video=a.video)
        except Exception as e:  # 交給主執行緒處理
            result["error"] = e
    th = threading.Thread(target=gpu)
    th.start()
    for ep in batch:
        voice_episode(a.slug, ep, mock=a.mock)
    stamp["voice"] = time.time() - t0
    th.join()
    stamp["render"] = time.time() - t0
    if "error" in result:
        raise result["error"]
    manifest = result["manifest"]

    def assemble(ep):
        try:
            return ep, assemble_episode(a.slug, ep), None
        except FileNotFoundError as e:
            return ep, None, str(e)
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as pool:
        outcomes = list(pool.map(assemble, batch))
    stamp["assemble"] = time.time() - t0

    done = [ep for ep, out, err in outcomes if out]
    missing = [f"第 {ep} 集：{err}" for ep, out, err in outcomes if err]
    remaining = [e for e in todo if e not in done]
    print("\n".join(["", "═" * 40, f"完成 {len(done)} 集：{done}", *missing,
                     "⏱ 分鏡 {scripts:.0f}s｜配音 {voice:.0f}s｜算圖 {render:.0f}s｜剪輯 {assemble:.0f}s".format(**stamp)]))
    partial = bool(manifest.get("partial") or missing or failed_scripts)
    _write_status(a, batch=batch, done=done, remaining=remaining, partial=partial, timing=stamp)


def _write_status(a, **data) -> None:
    import json
    if a.status_out:
        with open(a.status_out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)


def cmd_scripts(a) -> None:
    from .writer import write_episodes

    bible = paths.read_json(paths.bible_path(a.slug))
    eps = paths.parse_eps(a.eps if a.eps != "all" else f"1-{bible['episode_count']}", bible["episode_count"])
    failed = write_episodes(a.slug, eps, mock=a.mock, workers=a.workers)
    print(f"▶ 分鏡完成 {len(eps) - len(failed)}／{len(eps)} 集" + (f"，失敗：{failed}" if failed else ""))


def summary(slug: str) -> str:
    b = paths.read_json(paths.bible_path(slug))
    o = paths.read_json(paths.outline_path(slug))
    chars = "\n".join(f"| {c['name']} | {c['role']} | {c['personality']} | {c['voice']} |" for c in b["characters"])
    eps = "\n".join(f"| {e['ep']} | {e['title']} | {e['summary']} |" for e in o["episodes"][:10])
    return f"""## 🎬《{b['title']}》
> {b['logline']}

**類型** {b['genre']}　**基調** {b['tone']}　**集數** {b['episode_count']} 集 × 約 {b['episode_seconds']} 秒

### 角色
| 角色 | 定位 | 性格 | 聲線 |
|---|---|---|---|
{chars}

### 前 10 集大綱
| 集 | 標題 | 劇情 |
|---|---|---|
{eps}

完整大綱：`series/{slug}/outline.json`

---
**下一步**：分鏡劇本正在背景寫，寫完後留言 `/produce all` 會自動一批 10 集接力生產到完結；也可以 `/produce 1-10` 指定範圍。
"""


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="saga")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("plan")
    pp.add_argument("--slug", required=True)
    pp.add_argument("--idea", default="")
    pp.add_argument("--issue-file")
    pp.add_argument("--genre", default="")
    pp.add_argument("--style", default="")
    pp.add_argument("--episodes", type=int, default=60)
    pp.add_argument("--seconds", type=int, default=90)
    pp.add_argument("--mock", action="store_true")
    pp.set_defaults(fn=cmd_plan)

    pr = sub.add_parser("produce")
    pr.add_argument("--slug", required=True)
    pr.add_argument("--eps", required=True, help="例如 1-10，或 all")
    pr.add_argument("--skip", default="", help="已完成、要跳過的集數，例如 1-3,5")
    pr.add_argument("--batch", type=int, default=10, help="這次最多做幾集")
    pr.add_argument("--status-out", help="把結果寫成 JSON（給 workflow 接力用）")
    pr.add_argument("--video", action="store_true", help="關鍵鏡頭做成真影片（實驗性）")
    pr.add_argument("--rewrite", action="store_true", help="重寫已存在的分鏡劇本")
    pr.add_argument("--mock", action="store_true")
    pr.set_defaults(fn=cmd_produce)

    pw = sub.add_parser("scripts", help="先把分鏡劇本全部寫好")
    pw.add_argument("--slug", required=True)
    pw.add_argument("--eps", default="all")
    pw.add_argument("--workers", type=int, default=3)
    pw.add_argument("--mock", action="store_true")
    pw.set_defaults(fn=cmd_scripts)

    ps = sub.add_parser("summary")
    ps.add_argument("--slug", required=True)
    ps.set_defaults(fn=lambda a: print(summary(a.slug)))

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
