"""指令入口。

  python -m saga.cli plan     --slug s1 --idea "..."          建立劇本聖經＋全劇大綱
  python -m saga.cli plan     --slug s1 --issue-file body.md  從 GitHub Issue 表單建立
  python -m saga.cli produce  --slug s1 --eps 1-5 [--video]   劇本→配音→算圖→剪輯
  python -m saga.cli summary  --slug s1                       輸出 Markdown 摘要（給 Issue 留言用）

任何指令加 --mock：不呼叫 Gemini／Kaggle／TTS，用假資料跑通流程。
"""
from __future__ import annotations

import argparse
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
    from .assemble import assemble_episode
    from .render import render
    from .voice import voice_episode
    from .writer import write_episode

    bible = paths.read_json(paths.bible_path(a.slug))
    eps = paths.parse_eps(a.eps, bible["episode_count"])
    if not eps:
        sys.exit("集數範圍不正確")
    for ep in eps:
        if a.rewrite:
            shutil.rmtree(paths.build_ep_dir(a.slug, ep), ignore_errors=True)
        write_episode(a.slug, ep, mock=a.mock, force=a.rewrite)
        voice_episode(a.slug, ep, mock=a.mock)
    manifest = render(a.slug, eps, mock=a.mock, video=a.video)
    done, failed = [], []
    for ep in eps:
        try:
            done.append(assemble_episode(a.slug, ep))
        except FileNotFoundError as e:
            failed.append(f"第 {ep} 集：{e}")
    print("\n".join(["", "═" * 40, f"完成 {len(done)} 集", *map(str, done), *failed]))
    if manifest.get("partial") or failed:
        sys.exit(3)  # 讓 workflow 知道需要再跑一次


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
**下一步**：在這個 Issue 留言 `/produce 1-3` 開始生產第 1～3 集（加上 `--video` 會把關鍵鏡頭做成真影片，較耗 GPU 時數）。
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
    pr.add_argument("--eps", required=True)
    pr.add_argument("--video", action="store_true", help="關鍵鏡頭做成真影片（實驗性）")
    pr.add_argument("--rewrite", action="store_true", help="重寫已存在的分鏡劇本")
    pr.add_argument("--mock", action="store_true")
    pr.set_defaults(fn=cmd_produce)

    ps = sub.add_parser("summary")
    ps.add_argument("--slug", required=True)
    ps.set_defaults(fn=lambda a: print(summary(a.slug)))

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
