"""營利進度：每天抓 YouTube／Instagram／Threads 數據，對照各平台營利門檻，輸出 stats/monetization.json 給 A.R.T.H.U.R. 畫進度條。

TikTok 不開放個人帳號的觀看數據 → 讀 stats/manual.json（之後由 Discord 指令手動更新）。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent / "stats"
TZ = timezone(timedelta(hours=8))

# 門檻：同一 group 的條件是「擇一」，不同 group 之間是「都要」
TIERS = {
    "youtube": [
        {"name": "影片營利（YPP 初階）", "reqs": [
            ("subscribers", "訂閱", 500, "a"),
            ("uploads_90d", "90 天內公開上傳", 3, "b"),
            ("watch_hours_365d", "365 天長片觀看時數", 3000, "c"),
            ("shorts_views_90d", "90 天 Shorts 觀看", 3_000_000, "c")]},
        {"name": "廣告營利（YPP 完整）", "reqs": [
            ("subscribers", "訂閱", 1000, "a"),
            ("watch_hours_365d", "365 天長片觀看時數", 4000, "c"),
            ("shorts_views_90d", "90 天 Shorts 觀看", 10_000_000, "c")]},
    ],
    "instagram": [
        {"name": "Reels 禮物", "reqs": [("followers", "粉絲", 500, "a")]},
        {"name": "聯盟計畫", "reqs": [("followers", "粉絲", 1000, "a")]},
        {"name": "訂閱", "reqs": [("followers", "粉絲", 10000, "a")]},
    ],
    "tiktok": [
        {"name": "影片禮物", "reqs": [("followers", "粉絲", 10000, "a")]},
        {"name": "訂閱（約 2 美元／人）", "reqs": [
            ("followers", "粉絲", 10000, "a"),
            ("views_last_month", "上個月有效觀看", 100_000, "b")]},
    ],
}


# ── 各平台抓數據 ──────────────────────────────────────────

def youtube() -> dict:
    if not all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
        return {"error": "缺少 YouTube 金鑰"}
    t = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30)
    if not t.ok:
        return {"error": f"授權失敗：{t.text[:200]}"}
    h = {"Authorization": f"Bearer {t.json()['access_token']}"}
    api = "https://www.googleapis.com/youtube/v3"
    ch = requests.get(f"{api}/channels", headers=h, params={"part": "snippet,statistics,contentDetails", "mine": "true"},
                      timeout=30).json().get("items", [])
    if not ch:
        return {"error": "這個帳號底下找不到頻道"}
    ch = ch[0]
    st = ch["statistics"]
    out = {"channel": ch["snippet"]["title"], "subscribers": int(st.get("subscriberCount", 0)),
           "videos": int(st.get("videoCount", 0)), "views_total": int(st.get("viewCount", 0))}
    # 90 天內公開上傳數
    since = datetime.now(timezone.utc) - timedelta(days=90)
    uploads, token = 0, None
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    for _ in range(10):
        r = requests.get(f"{api}/playlistItems", headers=h, params={
            "part": "contentDetails,status", "playlistId": pl, "maxResults": 50, **({"pageToken": token} if token else {})},
            timeout=30).json()
        items = r.get("items", [])
        for it in items:
            pub = it["contentDetails"].get("videoPublishedAt")
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) >= since and \
                    it.get("status", {}).get("privacyStatus") == "public":
                uploads += 1
        token = r.get("nextPageToken")
        if not token or (items and items[-1]["contentDetails"].get("videoPublishedAt", "9") < since.isoformat()):
            break
    out["uploads_90d"] = uploads
    # 數據分析：長片觀看時數（365 天）、Shorts 觀看（90 天）
    def report(days: int) -> dict:
        end = date.today()
        r = requests.get("https://youtubeanalytics.googleapis.com/v2/reports", headers=h, params={
            "ids": "channel==MINE", "startDate": (end - timedelta(days=days)).isoformat(), "endDate": end.isoformat(),
            "metrics": "views,estimatedMinutesWatched", "dimensions": "creatorContentType"}, timeout=60)
        if not r.ok:
            raise RuntimeError(r.text[:200])
        return {row[0]: {"views": row[1], "minutes": row[2]} for row in r.json().get("rows", [])}
    try:
        y = report(365)
        out["watch_hours_365d"] = round(sum(v["minutes"] for k, v in y.items() if k != "SHORTS") / 60, 1)
        out["shorts_views_90d"] = int(report(90).get("SHORTS", {}).get("views", 0))
    except Exception as e:  # noqa: BLE001
        out["analytics_error"] = f"數據分析讀取失敗（授權時是否勾了 yt-analytics.readonly？）：{e}"
    return out


def instagram() -> dict:
    tok = (os.environ.get("IG_TOKEN") or "").strip().strip("\"'")
    if not tok:
        return {"error": "缺少 IG_TOKEN"}
    r = requests.get("https://graph.instagram.com/v23.0/me",
                     params={"fields": "username,followers_count,media_count", "access_token": tok}, timeout=30)
    if not r.ok:
        return {"error": f"權杖無效或過期（Instagram 權杖通常以 IGAA 開頭；目前開頭是 {tok[:4]}…，長度 {len(tok)}）：{r.text[:120]}"}
    d = r.json()
    return {"account": d.get("username"), "followers": int(d.get("followers_count", 0)), "posts": d.get("media_count")}


def threads() -> dict:
    tok = (os.environ.get("THREADS_TOKEN") or "").strip().strip("\"'")
    if not tok:
        return {"error": "缺少 THREADS_TOKEN"}
    base = "https://graph.threads.net/v1.0"
    r = requests.get(f"{base}/me", params={"fields": "id,username", "access_token": tok}, timeout=30)
    if not r.ok:
        return {"error": f"權杖無效或過期（Threads 權杖通常以 TH 開頭；目前開頭是 {tok[:4]}…，長度 {len(tok)}）：{r.text[:120]}"}
    out = {"account": r.json().get("username")}
    ins = requests.get(f"{base}/me/threads_insights", params={"metric": "followers_count", "access_token": tok}, timeout=30)
    if ins.ok:
        for m in ins.json().get("data", []):
            if m.get("name") == "followers_count":
                out["followers"] = int((m.get("total_value") or {}).get("value", 0))
    else:
        out["insights_error"] = "讀不到粉絲數（權杖需要 threads_manage_insights 權限）"
    return out


def manual() -> dict:
    p = ROOT / "manual.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ── 進度計算 ─────────────────────────────────────────────

def _history() -> list[dict]:
    p = ROOT / "history.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _rate(hist: list[dict], plat: str, key: str) -> float | None:
    """最近 7 天的每日成長量"""
    pts = [(h["date"], h.get(plat, {}).get(key)) for h in hist[-8:] if isinstance(h.get(plat, {}).get(key), (int, float))]
    if len(pts) < 2:
        return None
    d0, v0 = pts[0]
    d1, v1 = pts[-1]
    days = (date.fromisoformat(d1) - date.fromisoformat(d0)).days
    return (v1 - v0) / days if days > 0 else None


def progress(data: dict, hist: list[dict]) -> dict:
    out = {}
    for plat, tiers in TIERS.items():
        m = data.get(plat, {})
        ptiers = []
        for t in tiers:
            groups: dict[str, list] = {}
            for key, label, target, g in t["reqs"]:
                cur = m.get(key)
                pct = None if cur is None else round(min(100.0, cur / target * 100), 1)
                rate = _rate(hist, plat, key)
                eta = None
                if cur is not None and cur < target and rate and rate > 0:
                    eta = (date.today() + timedelta(days=int((target - cur) / rate) + 1)).isoformat()
                groups.setdefault(g, []).append({"key": key, "label": label, "current": cur, "target": target,
                                                 "pct": pct, "per_day": None if rate is None else round(rate, 1),
                                                 "eta": eta})
            reqs = []
            for g, items in groups.items():
                best = max(items, key=lambda x: -1 if x["pct"] is None else x["pct"])
                for it in items:
                    it["either"] = len(items) > 1          # 擇一條件
                    it["closest"] = len(items) > 1 and it is best
                    reqs.append(it)
            known = [max((x["pct"] or 0) for x in items) for items in groups.values()]
            missing = any(all(x["pct"] is None for x in items) for items in groups.values())
            overall = round(min(known), 1) if known else 0.0
            ptiers.append({"name": t["name"], "pct": overall, "achieved": overall >= 100 and not missing,
                           "complete_data": not missing, "requirements": reqs})
        out[plat] = {"tiers": ptiers, **({"error": m["error"]} if m.get("error") else {})}
    return out


def run() -> dict:
    ROOT.mkdir(exist_ok=True)
    data = {"youtube": youtube(), "instagram": instagram(), "threads": threads()}
    data["tiktok"] = manual().get("tiktok", {"error": "等待手動回報（TikTok 不開放個人帳號數據）"})
    today = datetime.now(TZ).date().isoformat()
    hist = [h for h in _history() if h["date"] != today]
    hist.append({"date": today, **{p: {k: v for k, v in d.items() if isinstance(v, (int, float))} for p, d in data.items()}})
    hist = hist[-400:]
    (ROOT / "history.jsonl").write_text("\n".join(json.dumps(h, ensure_ascii=False) for h in hist) + "\n", encoding="utf-8")
    doc = {"updated": datetime.now(TZ).isoformat(timespec="minutes"), "raw": data, "progress": progress(data, hist)}
    (ROOT / "monetization.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def summary(doc: dict) -> str:
    names = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
    lines = ["## 💰 營利進度", f"更新：{doc['updated']}", ""]
    for p, d in doc["raw"].items():
        errs = [v for k, v in d.items() if k.endswith("error")]
        lines.append(f"- **{p}**：" + ("；".join(errs) if errs else
                     "、".join(f"{k}={v}" for k, v in d.items() if isinstance(v, (int, float)))))
    lines.append("")
    for p, pr in doc["progress"].items():
        for t in pr["tiers"]:
            bar = "█" * int(t["pct"] // 10) + "░" * (10 - int(t["pct"] // 10))
            lines.append(f"{names[p]}｜{t['name']}　{bar} {t['pct']}%" + ("　✅" if t["achieved"] else ""))
    return "\n".join(lines)
