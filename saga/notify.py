"""Discord 通知：用頻道 Webhook（secret DISCORD_WEBHOOK）直接貼訊息到 #東石影音。沒設定就安靜略過。

python -m saga.notify "文字"
python -m saga.notify --report report.md      # 把上架報告濃縮成一則通知
python -m saga.notify --schedule              # 公告目前待執行的排程
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

RUN = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
       f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")


def send(text: str) -> None:
    url = (os.environ.get("DISCORD_WEBHOOK") or "").strip()
    if not url:
        print("（沒有 DISCORD_WEBHOOK，略過 Discord 通知）\n" + text)
        return
    r = requests.post(url, json={"content": text[:1990], "username": "東石影音",
                                 "allowed_mentions": {"parse": []}}, timeout=30)
    print("Discord 通知" + ("已送出" if r.ok else f"失敗：HTTP {r.status_code} {r.text[:200]}"))


def from_report(path: str) -> str | None:
    text = Path(path).read_text(encoding="utf-8")
    title = re.search(r"《[^》]+》", text)
    lines = []
    for plat, icon in (("YouTube", "▶️"), ("Instagram Reels", "📸")):
        m = re.search(rf"\*\*{re.escape(plat)}\*\*：([^\n]+)", text)
        if not m:
            continue
        ok = re.search(r"成功 (\d+) 集", m.group(1))
        if ok and int(ok.group(1)) == 0 and "失敗" not in m.group(1):
            continue
        lines.append(f"{icon} {plat}：{m.group(1)}")
    pl = re.search(r"播放清單：(\S+)", text)
    if pl:
        lines.append(f"🎞 播放清單 {pl.group(1)}")
    if "失敗" in text:
        lines.append(f"⚠️ 有失敗項目，詳情：{RUN}")
    if not lines:
        return None
    return f"📤 **{title.group(0) if title else ''} 上架完成**\n" + "\n".join(lines)


def schedule_text() -> str | None:
    p = Path("schedule/publish.json")
    items = [i for i in json.loads(p.read_text(encoding="utf-8")) if not i.get("done")] if p.exists() else []
    if not items:
        return None
    plat = {"instagram": "Instagram Reels", "youtube": "YouTube", "all": "YouTube＋Instagram"}
    rows = [f"• {datetime.fromisoformat(i['at']).strftime('%m/%d %H:%M')}　{plat.get(i['to'], i['to'])}　{i.get('note', i['slug'] + ' ' + i['eps'])}"
            for i in sorted(items, key=lambda x: x["at"])]
    return "🗓 **排程上架已設定**\n" + "\n".join(rows) + "\n（GitHub 定時執行可能晚 5～15 分鐘）"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--report"]:
        msg = from_report(args[1])
    elif args[:1] == ["--schedule"]:
        msg = schedule_text()
    else:
        msg = " ".join(args)
    if msg:
        send(msg)
