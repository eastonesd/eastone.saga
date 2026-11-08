"""發行部：把成片批次上架到 YouTube 與 Instagram Reels。

已上架的集數記在 series/<slug>/published.json，重跑只補沒上的，不會重複發。

YouTube 需要：YT_CLIENT_ID、YT_CLIENT_SECRET、YT_REFRESH_TOKEN
Instagram 需要：IG_TOKEN（Instagram 專業帳號的長效權杖）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from . import paths

REPO = os.environ.get("GITHUB_REPOSITORY", "eastonesd/saga.ai")
TAGS = ["短劇", "AI短劇", "爽劇", "追劇", "逆襲", "動畫短劇"]


# ── 共用：文案 ─────────────────────────────────────────────

def _meta(slug: str) -> tuple[dict, dict[int, dict]]:
    bible = paths.read_json(paths.bible_path(slug))
    outline = {int(e["ep"]): e for e in paths.read_json(paths.outline_path(slug))["episodes"]}
    return bible, outline


def _keep(xs):
    return [x for x in xs if x is not None]


def _tags(bible: dict) -> list[str]:
    extra = [t.strip("#＃ ") for t in str(bible.get("genre", "")).replace("、", "/").split("/") if t.strip()]
    return list(dict.fromkeys(TAGS + extra))[:10]


def youtube_text(bible: dict, ep: dict, n: int) -> tuple[str, str]:
    total = bible["episode_count"]
    title = f"{bible['title']} 第{n}集｜{ep.get('title', '')}"[:100]
    body = "\n".join(_keep([
        ep.get("hook") or ep.get("summary", ""),
        "",
        f"《{bible['title']}》全 {total} 集",
        bible.get("logline", ""),
        "",
        "⚠️ 本片影像與配音皆為 AI 生成內容",
        " ".join("#" + t for t in _tags(bible)),
    ]))
    return title, body[:4900]


def instagram_caption(bible: dict, ep: dict, n: int) -> str:
    total = bible["episode_count"]
    return "\n".join(_keep([
        f"《{bible['title']}》第 {n}/{total} 集｜{ep.get('title', '')}",
        "",
        ep.get("hook") or ep.get("summary", ""),
        "",
        "👉 追更看主頁，全集依序排好",
        "⚠️ AI 生成內容",
        "",
        " ".join("#" + t for t in _tags(bible)),
    ]))[:2100]


# ── 記錄 ──────────────────────────────────────────────────

def _state_path(slug: str) -> Path:
    return paths.series_dir(slug) / "published.json"


def _load(slug: str) -> dict:
    p = _state_path(slug)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(slug: str, state: dict) -> None:
    _state_path(slug).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _video_file(slug: str, n: int, folder: Path) -> Path:
    f = folder / f"{slug}-ep{n:02d}.mp4"
    if not f.exists():
        raise FileNotFoundError(f"找不到 {f}（release {slug} 裡沒有這集？）")
    return f


# ── YouTube ───────────────────────────────────────────────

class YouTube:
    API = "https://www.googleapis.com/youtube/v3"
    UPLOAD = "https://www.googleapis.com/upload/youtube/v3"

    def __init__(self):
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["YT_CLIENT_ID"],
            "client_secret": os.environ["YT_CLIENT_SECRET"],
            "refresh_token": os.environ["YT_REFRESH_TOKEN"],
            "grant_type": "refresh_token"}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"YouTube 授權失敗（refresh token 失效？）：{r.text[:300]}")
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def playlist(self, title: str) -> str | None:
        try:
            r = requests.get(f"{self.API}/playlists", headers=self.h,
                             params={"part": "snippet", "mine": "true", "maxResults": 50}, timeout=30)
            r.raise_for_status()
            for p in r.json().get("items", []):
                if p["snippet"]["title"] == title:
                    return p["id"]
            r = requests.post(f"{self.API}/playlists", headers=self.h, params={"part": "snippet,status"}, json={
                "snippet": {"title": title, "description": f"《{title}》全集，依集數排序"},
                "status": {"privacyStatus": "public"}}, timeout=30)
            r.raise_for_status()
            return r.json()["id"]
        except requests.RequestException as e:
            print(f"  ⚠ 播放清單建立失敗（不影響上傳）：{e}", flush=True)
            return None

    def upload(self, file: Path, title: str, desc: str, tags: list[str]) -> str:
        meta = {
            "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "1",
                        "defaultLanguage": "zh-Hant", "defaultAudioLanguage": "zh"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True},
        }
        size = file.stat().st_size
        r = requests.post(f"{self.UPLOAD}/videos", headers={
            **self.h, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
            params={"uploadType": "resumable", "part": "snippet,status"}, json=meta, timeout=60)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:300]}")
        loc = r.headers["Location"]
        for attempt in range(5):
            try:
                with file.open("rb") as f:
                    up = requests.put(loc, headers={**self.h, "Content-Type": "video/mp4"}, data=f, timeout=900)
                if up.ok:
                    return up.json()["id"]
                if up.status_code < 500:
                    raise RuntimeError(f"HTTP {up.status_code} {up.text[:300]}")
            except requests.RequestException as e:
                print(f"  … 上傳中斷（{e}），重試", flush=True)
            time.sleep(10 * (attempt + 1))
        raise RuntimeError("上傳重試 5 次仍失敗")

    def thumbnail(self, vid: str, jpg: Path) -> None:
        if not jpg.exists():
            return
        r = requests.post(f"{self.UPLOAD}/thumbnails/set", headers={**self.h, "Content-Type": "image/jpeg"},
                          params={"videoId": vid}, data=jpg.read_bytes(), timeout=60)
        if not r.ok:
            print(f"  ⚠ 縮圖沒設成（頻道需先完成手機驗證）：HTTP {r.status_code}", flush=True)

    def add(self, playlist: str, vid: str, pos: int) -> None:
        r = requests.post(f"{self.API}/playlistItems", headers=self.h, params={"part": "snippet"}, json={
            "snippet": {"playlistId": playlist, "position": pos,
                        "resourceId": {"kind": "youtube#video", "videoId": vid}}}, timeout=30)
        if not r.ok:  # 位置超出範圍時改成加到最後
            requests.post(f"{self.API}/playlistItems", headers=self.h, params={"part": "snippet"}, json={
                "snippet": {"playlistId": playlist,
                            "resourceId": {"kind": "youtube#video", "videoId": vid}}}, timeout=30)

    def privacy(self, vid: str) -> str:
        r = requests.get(f"{self.API}/videos", headers=self.h, params={"part": "status", "id": vid}, timeout=30)
        items = r.json().get("items", []) if r.ok else []
        return items[0]["status"]["privacyStatus"] if items else "?"


# ── Instagram Reels ───────────────────────────────────────

class Instagram:
    G = "https://graph.instagram.com/v23.0"

    def __init__(self):
        self.token = os.environ["IG_TOKEN"]
        r = requests.get(f"{self.G}/me", params={"fields": "user_id,username", "access_token": self.token}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Instagram 權杖無效或過期：{r.text[:300]}")
        me = r.json()
        self.uid, self.username = me["user_id"], me.get("username", "")

    def quota_left(self) -> int:
        try:
            r = requests.get(f"{self.G}/{self.uid}/content_publishing_limit",
                             params={"fields": "quota_usage,config", "access_token": self.token}, timeout=30)
            d = r.json()["data"][0]
            return int(d["config"]["quota_total"]) - int(d.get("quota_usage", 0))
        except Exception:
            return 25

    def _container(self, file: Path, caption: str, cover: str | None) -> str:
        params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                  "share_to_feed": "true", "access_token": self.token}
        if cover:
            params["cover_url"] = cover
        r = requests.post(f"{self.G}/{self.uid}/media", data=params, timeout=60)
        if not r.ok:
            raise RuntimeError(f"建立 Reels 失敗：HTTP {r.status_code} {r.text[:300]}")
        cid, uri = r.json()["id"], r.json().get("uri") or f"https://rupload.facebook.com/ig-api-upload/v23.0/{r.json()['id']}"
        with file.open("rb") as f:
            up = requests.post(uri, headers={"Authorization": f"OAuth {self.token}", "offset": "0",
                                             "file_size": str(file.stat().st_size)}, data=f, timeout=900)
        if not up.ok:
            raise RuntimeError(f"影片傳送失敗：HTTP {up.status_code} {up.text[:300]}")
        return cid

    def post(self, file: Path, caption: str, cover: str | None) -> str:
        cid = self._container(file, caption, cover)
        for _ in range(60):  # Instagram 轉檔，最多等 10 分鐘
            time.sleep(10)
            s = requests.get(f"{self.G}/{cid}", params={"fields": "status_code,status",
                                                        "access_token": self.token}, timeout=30).json()
            if s.get("status_code") == "FINISHED":
                break
            if s.get("status_code") in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram 轉檔失敗：{s.get('status')}")
        else:
            raise RuntimeError("Instagram 轉檔逾時")
        r = requests.post(f"{self.G}/{self.uid}/media_publish",
                          data={"creation_id": cid, "access_token": self.token}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"發布失敗：HTTP {r.status_code} {r.text[:300]}")
        return r.json()["id"]


# ── 調度 ──────────────────────────────────────────────────

def publish(slug: str, eps: list[int], platforms: list[str], folder: Path, limit: int = 0,
            gap: int = 0) -> dict:
    """回傳 {platform: {"ok": [...], "fail": {ep: msg}, "note": str}}"""
    bible, outline = _meta(slug)
    state = _load(slug)
    report: dict = {}
    for plat in platforms:
        done = state.setdefault(plat, {})
        todo = [n for n in eps if str(n) not in done]
        rep = report[plat] = {"ok": [], "fail": {}, "skip": len(eps) - len(todo), "note": ""}
        if not todo:
            continue
        try:
            client = YouTube() if plat == "youtube" else Instagram()
        except (KeyError, RuntimeError) as e:
            rep["note"] = f"沒上傳：{'缺少金鑰 ' + str(e) if isinstance(e, KeyError) else e}"
            print(f"✗ {plat}：{rep['note']}", flush=True)
            continue
        if plat == "instagram":
            left = client.quota_left()
            if left < len(todo):
                rep["note"] = f"Instagram 24 小時內只剩 {left} 則額度，其餘下次再跑"
                todo = todo[:max(0, left)]
        if limit:
            todo = todo[:limit]
        playlist = client.playlist(f"《{bible['title']}》") if plat == "youtube" else None
        print(f"▶ {plat}：上架 {len(todo)} 集", flush=True)
        for i, n in enumerate(todo):
            ep = outline.get(n, {"title": f"第{n}集"})
            try:
                file = _video_file(slug, n, folder)
                if plat == "youtube":
                    title, desc = youtube_text(bible, ep, n)
                    vid = client.upload(file, title, desc, _tags(bible))
                    client.thumbnail(vid, folder / f"{slug}-ep{n:02d}.jpg")
                    if playlist:
                        client.add(playlist, vid, n - 1)
                    done[str(n)] = vid
                    if not rep["ok"]:
                        rep["privacy"] = client.privacy(vid)
                else:
                    cover = f"https://github.com/{REPO}/releases/download/{slug}/{slug}-ep{n:02d}.jpg"
                    done[str(n)] = client.post(file, instagram_caption(bible, ep, n), _resolve(cover))
                rep["ok"].append(n)
                print(f"  ✓ 第 {n} 集 → {done[str(n)]}", flush=True)
            except Exception as e:  # 單集失敗不拖垮整批
                rep["fail"][n] = str(e)[:300]
                print(f"  ✗ 第 {n} 集：{e}", flush=True)
                if "quota" in str(e).lower() or "uploadLimitExceeded" in str(e):
                    rep["note"] = "今日額度用完，明天重跑會自動接續"
                    break
            finally:
                _save(slug, state)
            if gap and i < len(todo) - 1:
                time.sleep(gap)
        if playlist:
            rep["playlist"] = playlist
    return report


def _resolve(url: str) -> str | None:
    """GitHub release 下載網址會轉址到短效簽名網址；給 Instagram 最終網址比較穩。"""
    try:
        r = requests.head(url, allow_redirects=True, timeout=20)
        return r.url if r.ok else None
    except requests.RequestException:
        return None
