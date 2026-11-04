"""--mock 模式的假資料：不花任何 API 額度就能驗證整條流水線。"""
from __future__ import annotations

import random


def fake(kind: str, ctx: dict) -> dict:
    if kind == "bible":
        return {
            "title": "逆襲女總裁",
            "logline": "被趕出家門的千金，三年後以神秘投資人身分回歸。",
            "genre": "都市逆襲",
            "tone": "爽快、反轉",
            "style_prompt": "cinematic film still, soft key light, 35mm, shallow depth of field, highly detailed",
            "negative_prompt": "lowres, bad anatomy, extra fingers, blurry, watermark, text",
            "narrator_voice": "旁白-女",
            "characters": [
                {"id": "lin_xia", "name": "林夏", "role": "主角", "age": 26, "gender": "female",
                 "appearance": "young asian woman, long straight black hair, sharp eyes, white tailored suit",
                 "personality": "隱忍、聰明、反擊狠", "voice": "女聲-御姐"},
                {"id": "zhou_yan", "name": "周彥", "role": "反派", "age": 30, "gender": "male",
                 "appearance": "asian man, slicked back hair, thin glasses, navy three-piece suit",
                 "personality": "傲慢、算計", "voice": "男聲-沉穩"},
            ],
            "locations": [
                {"id": "office", "name": "集團頂樓辦公室", "visual": "luxury office, floor-to-ceiling windows, city skyline at night"},
                {"id": "lobby", "name": "公司大廳", "visual": "grand marble lobby, crowd of employees"},
            ],
        }
    if kind == "outline":
        return {"episodes": [
            {"ep": e, "title": f"第{e}集標題", "summary": f"第{e}集的劇情摘要。",
             "hook": "開場鉤子", "cliffhanger": "結尾懸念"}
            for e in range(ctx["start"], ctx["end"] + 1)
        ]}
    if kind == "episode":
        rng = random.Random(ctx["ep"])
        motions = ["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]
        samples = [
            ("lin_xia", "三年了，我回來了。"),
            ("zhou_yan", "妳以為妳還是林家大小姐？"),
            ("lin_xia", "今天起，這家公司姓林。"),
            ("narrator", "沒有人知道，她手上握著什麼。"),
            ("zhou_yan", "不可能……這份合約是假的！"),
        ]
        shots = []
        for i in range(ctx.get("n_shots", 6)):
            speaker, text = samples[i % len(samples)]
            chars = ["lin_xia"] if speaker != "zhou_yan" else ["zhou_yan", "lin_xia"]
            shots.append({
                "id": f"s{i + 1:02d}",
                "location": rng.choice(["office", "lobby"]),
                "characters": chars,
                "camera": rng.choice(["close-up", "medium", "wide"]),
                "motion": motions[i % len(motions)],
                "visual": "dramatic expression, looking at camera",
                "lines": [{"speaker": speaker, "text": text}],
                "key": i == 0,
            })
        return {"ep": ctx["ep"], "title": f"第{ctx['ep']}集標題", "shots": shots}
    raise ValueError(kind)
