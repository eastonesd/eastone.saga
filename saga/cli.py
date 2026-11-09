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
    from .voice import voice_batch
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

    if a.revoice:  # 重補配音：保留已成功的 IndexTTS 檔，刪掉備援的 Edge 檔讓它重錄
        for ep in batch:
            for f in (paths.build_ep_dir(a.slug, ep) / "audio").glob("*_edge.mp3"):
                f.unlink()
    if a.rewrite or a.redo:
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
    try:
        voice_batch(a.slug, batch, mock=a.mock, wait_gpu=th.join)
    except Exception as e:  # 配音出錯不拖垮整批，剪輯時會以缺檔處理
        print(f"  ⚠ 配音失敗：{e}", flush=True)
    stamp["voice"] = time.time() - t0
    th.join()
    stamp["render"] = time.time() - t0
    if "error" in result:
        raise result["error"]
    manifest = result["manifest"]
    from . import poster
    if not (poster.poster_dir(a.slug) / "poster.jpg").exists():
        try:
            poster.finalize(a.slug)
        except Exception as e:  # 海報失敗不影響生產
            print(f"  ⚠ 海報合成失敗：{e}", flush=True)
    if a.no_assemble:  # 剪輯交給平行的 matrix 工作
        ok = [e for e in batch if (paths.build_ep_dir(a.slug, e) / "audio" / "timing.json").exists()]
        print(f"▶ 算圖與配音完成，交給平行剪輯：{ok}", flush=True)
        from .voice import WARNINGS
        _write_status(a, batch=ok, done=[], remaining=[e for e in todo if e not in ok],
                      partial=bool(manifest.get("partial")), timing=stamp, warnings=WARNINGS)
        return

    def assemble(ep):
        try:
            return ep, assemble_episode(a.slug, ep), None
        except (FileNotFoundError, KeyError, RuntimeError) as e:
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


def cmd_stylebench(a) -> None:
    """在 Kaggle 上用同一批鏡頭比較畫風設定，輸出對照圖 build/<slug>/stylebench.jpg"""
    from PIL import Image, ImageDraw, ImageFont
    from . import render

    original = paths.read_json(paths.bible_path(a.slug))
    bible = dict(original, render_style=a.style)
    paths.write_json(paths.bible_path(a.slug), bible)  # 暫時套用，建 job 用
    try:
        job = render.build_job(a.slug, [], video=False, poster=False)
    finally:
        paths.write_json(paths.bible_path(a.slug), original)
    for c in job["characters"]:
        c["ref_b64"] = c["ref_url"] = None  # 依新畫風重做定妝照
    ep = paths.read_json(paths.episode_path(a.slug, 1))
    picks = [s for s in ep["shots"] if s["characters"]][: a.n]
    duo = next((s for s in ep["shots"] if len(s["characters"]) >= 2), None)
    if duo and duo not in picks:
        picks[-1] = duo
    variants = [float(x) for x in a.lora.split(",")]
    for i, s in enumerate(picks):
        for v in variants:
            job["shots"].append({"key": f"ep99_b{i}v{int(v * 100):03d}", "prompt": render.shot_prompt(s, bible),
                                 "refs": s["characters"][:2], "seed": render._shot_seed(a.slug, 1, s["id"]), "lora": v})
    dl = paths.BUILD_DIR / a.slug / "_bench"
    shutil.rmtree(dl, ignore_errors=True)
    manifest = render._run_mock(job, dl) if a.mock else render._run_on_kaggle(job, dl)
    font = ImageFont.load_default()
    cols, tw, th = len(variants), 448, 256
    sheet = Image.new("RGB", (cols * tw, len(picks) * th + 20 + 2 * 288), "black")
    d = ImageDraw.Draw(sheet)
    for j, v in enumerate(variants):
        d.text((j * tw + 6, 4), f"style LoRA {v}", fill="white", font=font)
    for i in range(len(picks)):
        for j, v in enumerate(variants):
            f = next(iter(dl.rglob(f"ep99_b{i}v{int(v * 100):03d}.png")), None)
            if f:
                sheet.paste(Image.open(f).resize((tw, th)), (j * tw, 20 + i * th))
    refs = sorted(dl.rglob("ref_*.jpg"))
    for k, f in enumerate(refs):
        sheet.paste(Image.open(f).resize((224, 288)), ((k % (cols * 2)) * 224, 20 + len(picks) * th + (k // (cols * 2)) * 288))
    out = paths.BUILD_DIR / a.slug / "stylebench.jpg"
    sheet.save(out, quality=90)
    print(f"▶ 對照圖：{out}（{len(manifest.get('images', []))} 張）")


def cmd_reoutline(a) -> None:
    """調整總集數／每集秒數：依原大綱重新濃縮成新集數（保留結局），清掉舊分鏡讓它照新長度重寫"""
    from . import prompts
    from .llm import generate_json

    import time
    bible = paths.read_json(paths.bible_path(a.slug))
    total = a.episodes
    # 中途失敗可接續：原大綱另存一份，已重排好的集數也先寫進暫存檔
    orig_p = paths.series_dir(a.slug) / "outline.orig.json"
    part_p = paths.series_dir(a.slug) / "outline.partial.json"
    if not orig_p.exists():
        paths.write_json(orig_p, paths.read_json(paths.outline_path(a.slug)))
    old = paths.read_json(orig_p)["episodes"]
    plan_text = "\n".join(f"{e['ep']}. {e['title']}" for e in old)
    bible["episode_count"], bible["episode_seconds"] = total, a.seconds
    eps: list[dict] = []
    if part_p.exists():
        part = paths.read_json(part_p)
        if part.get("total") == total:
            eps = part["episodes"]
            print(f"▶ 接續上次進度：已有 {len(eps)} 集", flush=True)
    for start in range(len(eps) + 1, total + 1, 10):
        end = min(total, start + 9)
        if start > 1 and not a.mock:
            time.sleep(20)  # 避開免費 API 每分鐘的用量上限
        print(f"▶ 重排大綱：第 {start}～{end} 集", flush=True)
        prompt = prompts.outline_prompt(bible, start, end, total, eps) + f"""
【原本的 {len(old)} 集規劃（標題）】
{plan_text}

請把原規劃重新濃縮成 {total} 集：保留主線、關鍵反轉與大結局，合併節奏較慢的集數。第 {start}～{end} 集要對應原規劃中相應的進度。"""
        out = generate_json(prompt, kind="outline", mock=a.mock, context={"start": start, "end": end})
        got = {int(e["ep"]): e for e in out["episodes"] if start <= int(e.get("ep", 0)) <= end}
        for n in range(start, end + 1):
            e = got.get(n) or {"ep": n, "title": f"第{n}集", "summary": "（待補）", "hook": "", "cliffhanger": ""}
            e["ep"] = n
            eps.append(e)
        paths.write_json(part_p, {"total": total, "episodes": eps})
    paths.write_json(paths.outline_path(a.slug), {"episodes": eps})
    paths.write_json(paths.bible_path(a.slug), bible)
    part_p.unlink(missing_ok=True)
    orig_p.unlink(missing_ok=True)
    removed = 0
    for f in (paths.series_dir(a.slug) / "episodes").glob("ep*.json"):
        f.unlink()
        removed += 1
    print(f"▶ 完成：{total} 集 × {a.seconds} 秒；清掉 {removed} 份舊分鏡（生產時會依新長度重寫）")
    print(summary(a.slug))


def cmd_publish(a) -> None:
    """批次上架 YouTube / Instagram Reels；結果寫進 --report（Markdown）"""
    from pathlib import Path
    from . import publish

    bible = paths.read_json(paths.bible_path(a.slug))
    eps = paths.parse_eps(a.eps if a.eps != "all" else f"1-{bible['episode_count']}", bible["episode_count"])
    plats = ["youtube", "instagram"] if a.to == "all" else [a.to]
    rep = publish.publish(a.slug, eps, plats, Path(a.dir), limit=a.limit, gap=a.gap)
    name = {"youtube": "YouTube", "instagram": "Instagram Reels"}
    lines = [f"## 📤 《{bible['title']}》上架結果", ""]
    for plat, r in rep.items():
        lines.append(f"**{name[plat]}**：成功 {len(r['ok'])} 集" + (f"，先前已上架 {r['skip']} 集" if r["skip"] else "")
                     + (f"，失敗 {len(r['fail'])} 集" if r["fail"] else ""))
        if r.get("note"):
            lines.append(f"- {r['note']}")
        if r.get("privacy") == "private":
            lines.append("- 影片目前是「私人」：YouTube 規定未經審核的上傳程式只能先設私人。"
                         "到 YouTube Studio → 內容 → 全選 → 編輯 → 瀏覽權限 → 公開（或排程），一次搞定。")
        if r.get("playlist"):
            lines.append(f"- 播放清單：https://www.youtube.com/playlist?list={r['playlist']}")
        for n, msg in sorted(r["fail"].items()):
            lines.append(f"- 第 {n} 集：{msg[:200]}")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    if a.report:
        Path(a.report).write_text(text, encoding="utf-8")
    if not any(r["ok"] or r["skip"] for r in rep.values()):
        raise SystemExit(1)


def cmd_vertical(a) -> None:
    """把 --dir 裡的 16:9 成片轉成 9:16 直式版（<slug>-epNN-v.mp4）"""
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from . import vertical

    bible = paths.read_json(paths.bible_path(a.slug))
    eps = paths.parse_eps(a.eps if a.eps != "all" else f"1-{bible['episode_count']}", bible["episode_count"])
    d = Path(a.dir)
    todo = [e for e in eps if (d / f"{a.slug}-ep{e:02d}.mp4").exists()]

    def one(e):
        vertical.wrap_episode(a.slug, e, d / f"{a.slug}-ep{e:02d}.mp4", Path(a.out or a.dir))
        print(f"  ✓ 第 {e} 集直式版", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(one, todo))


def cmd_stylelab(a) -> None:
    from . import stylelab
    stylelab.run(a.slug, a.preset, mock=a.mock)


def cmd_poster(a) -> None:
    """重畫或重選海報：--regen 重新畫 4 張底圖；--pick N 改用第 N 張"""
    from . import poster, render

    d = poster.poster_dir(a.slug)
    if a.pick:
        d.mkdir(parents=True, exist_ok=True)
        (d / "choice.txt").write_text(str(a.pick))
    if a.regen or poster.needs_art(a.slug):
        for f in d.glob("art_v*.jpg"):
            f.unlink()
        job = render.build_job(a.slug, [], video=False, poster=True)
        dl = paths.BUILD_DIR / a.slug / "_poster"
        shutil.rmtree(dl, ignore_errors=True)
        manifest = render._run_mock(job, dl) if a.mock else render._run_on_kaggle(job, dl)
        refs = paths.refs_dir(a.slug)
        refs.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        for name in manifest.get("images", []):
            f = next(iter(dl.rglob(name)), None)
            if f and name.startswith("poster_"):
                d.mkdir(parents=True, exist_ok=True)
                Image.open(f).convert("RGB").save(d / name.replace("poster_", "art_").replace(".png", ".jpg"), quality=95)
        for name in manifest.get("refs", []):  # 若定妝照是這次才畫的，一併存回
            f = next(iter(dl.rglob(name)), None)
            if f:
                shutil.copy(f, refs / name.removeprefix("ref_"))
    poster.finalize(a.slug)


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
    pr.add_argument("--redo", action="store_true", help="重做畫面與配音，沿用現有劇本")
    pr.add_argument("--revoice", action="store_true", help="只重錄配音，沿用畫面")
    pr.add_argument("--no-assemble", action="store_true", help="只做到算圖與配音，剪輯另外平行跑")

    pa = sub.add_parser("assemble", help="剪輯單集（素材需已就緒）")
    pa.add_argument("--slug", required=True)
    pa.add_argument("--ep", type=int, required=True)
    pa.set_defaults(fn=lambda a: __import__("saga.assemble", fromlist=["x"]).assemble_episode(a.slug, a.ep))
    pr.add_argument("--mock", action="store_true")
    pr.set_defaults(fn=cmd_produce)

    po = sub.add_parser("reoutline", help="調整總集數與每集長度，重排大綱")
    po.add_argument("--slug", required=True)
    po.add_argument("--episodes", type=int, required=True)
    po.add_argument("--seconds", type=int, required=True)
    po.add_argument("--mock", action="store_true")
    po.set_defaults(fn=cmd_reoutline)

    pw = sub.add_parser("scripts", help="先把分鏡劇本全部寫好")
    pw.add_argument("--slug", required=True)
    pw.add_argument("--eps", default="all")
    pw.add_argument("--workers", type=int, default=2)
    pw.add_argument("--mock", action="store_true")
    pw.set_defaults(fn=cmd_scripts)

    pp2 = sub.add_parser("poster", help="劇集海報：重畫或重選底圖")
    pp2.add_argument("--slug", required=True)
    pp2.add_argument("--pick", type=int, default=0)
    pp2.add_argument("--regen", action="store_true")
    pp2.add_argument("--mock", action="store_true")
    pp2.set_defaults(fn=cmd_poster)

    pu = sub.add_parser("publish", help="批次上架 YouTube / Instagram Reels")
    pu.add_argument("--slug", required=True)
    pu.add_argument("--to", choices=["youtube", "instagram", "all"], default="all")
    pu.add_argument("--eps", default="all")
    pu.add_argument("--dir", required=True, help="放 mp4/jpg 的資料夾")
    pu.add_argument("--limit", type=int, default=0, help="每個平台這次最多上幾集（0＝不限）")
    pu.add_argument("--gap", type=int, default=0, help="每集之間隔幾秒")
    pu.add_argument("--report")
    pu.set_defaults(fn=cmd_publish)

    pvt = sub.add_parser("vertical", help="16:9 成片轉 9:16 直式版")
    pvt.add_argument("--slug", required=True)
    pvt.add_argument("--eps", default="all")
    pvt.add_argument("--dir", required=True)
    pvt.add_argument("--out")
    pvt.add_argument("--workers", type=int, default=2)
    pvt.set_defaults(fn=cmd_vertical)

    psl = sub.add_parser("stylelab", help="畫風實驗：多組算圖設定對照")
    psl.add_argument("--slug", required=True)
    psl.add_argument("--preset", required=True)
    psl.add_argument("--mock", action="store_true")
    psl.set_defaults(fn=cmd_stylelab)

    pb = sub.add_parser("stylebench", help="比較畫風設定")
    pb.add_argument("--slug", required=True)
    pb.add_argument("--style", default="donghua3d")
    pb.add_argument("--lora", default="0,0.6,0.9", help="要比較的畫風 LoRA 強度")
    pb.add_argument("--n", type=int, default=5)
    pb.add_argument("--mock", action="store_true")
    pb.set_defaults(fn=cmd_stylebench)

    pv = sub.add_parser("voicebench", help="IndexTTS 聲線試聽帶")
    pv.set_defaults(fn=lambda a: __import__("saga.tts", fromlist=["x"]).voicebench(paths.BUILD_DIR))

    ps = sub.add_parser("summary")
    ps.add_argument("--slug", required=True)
    ps.set_defaults(fn=lambda a: print(summary(a.slug)))

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
