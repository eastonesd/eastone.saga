"""所有交給 LLM 的提示詞。改編劇風格，改這裡就好。"""
from __future__ import annotations

import json

VOICES = {
    "女聲-溫柔": "zh-TW-HsiaoChenNeural",
    "女聲-活潑": "zh-TW-HsiaoYuNeural",
    "男聲-沉穩": "zh-TW-YunJheNeural",
    "男聲-少年": "zh-CN-YunxiNeural",
    "男聲-霸氣": "zh-CN-YunjianNeural",
    "女聲-御姐": "zh-CN-XiaoyiNeural",
    "旁白-男": "zh-CN-YunyangNeural",
    "旁白-女": "zh-CN-XiaoxiaoNeural",
}

DRAMA_RULES = """
【短劇鐵律】
1. 每集開頭 3 秒內必須有鉤子：衝突、反差、懸念或爽點，禁止慢熱鋪陳。
2. 每集結尾必須留懸念（卡在關鍵一刻），讓觀眾想看下一集。
3. 主線衝突持續升級；大約每 8～10 集要有一次大反轉。
4. 台詞口語、短促，每句不超過 20 個中文字；以對白推動劇情，旁白只在必要時使用。
5. 角色動機清楚，反派要有壓迫感，主角要有爽點（打臉、逆襲、揭穿）。
6. 全部使用繁體中文。
""".strip()

BIBLE_SCHEMA = {
    "title": "劇名（繁中，8 字內，有吸引力）",
    "logline": "一句話賣點",
    "genre": "類型",
    "tone": "基調",
    "style_prompt": "英文 Stable Diffusion 畫風標籤，例如 'cinematic film still, soft lighting, 35mm, highly detailed'",
    "negative_prompt": "英文負面提示詞",
    "narrator_voice": "從可用聲線挑一個 key",
    "characters": [
        {
            "id": "英文小寫代號，例如 lin_xia",
            "name": "角色中文名",
            "role": "主角/反派/配角",
            "age": 25,
            "gender": "female/male",
            "appearance": "英文外觀標籤：年齡、性別、髮型髮色、五官、固定服裝。要非常具體且固定，確保每集一致",
            "personality": "性格（中文）",
            "voice": "從可用聲線挑一個 key",
        }
    ],
    "locations": [{"id": "英文代號", "name": "中文名", "visual": "英文場景描述標籤"}],
}

OUTLINE_SCHEMA = {
    "episodes": [
        {
            "ep": 1,
            "title": "單集標題（繁中）",
            "summary": "本集劇情 2～3 句",
            "hook": "開頭 3 秒鉤子",
            "cliffhanger": "結尾懸念",
        }
    ]
}

EPISODE_SCHEMA = {
    "ep": 1,
    "title": "單集標題",
    "shots": [
        {
            "id": "s01",
            "location": "場景 id（必須來自 bible.locations）",
            "characters": ["出場角色 id，第一個是畫面主角"],
            "camera": "close-up / medium / wide / over-shoulder",
            "motion": "zoom_in / zoom_out / pan_left / pan_right / static",
            "visual": "英文畫面描述：動作、表情、構圖、光線。不要寫角色名字也不要寫外觀（系統會自動帶入）",
            "lines": [{"speaker": "角色 id 或 narrator", "text": "台詞（繁中，≤20 字）"}],
            "key": False,
        }
    ],
}


def _voices_text() -> str:
    return "、".join(VOICES.keys())


def bible_prompt(idea: str, genre: str, style: str, episodes: int, seconds: int) -> str:
    return f"""你是頂尖的直式短劇（抖音／ReelShort 風格）總編劇兼美術指導。
根據使用者的靈感，建立整部劇的「劇本聖經」。

【使用者靈感】
{idea}

【指定】類型：{genre or "由你判斷"}；畫風：{style or "寫實電影感"}；共 {episodes} 集，每集約 {seconds} 秒。

{DRAMA_RULES}

【要求】
- 3～6 個主要角色，4～8 個常用場景。
- appearance 與 visual 用英文 SD 標籤寫，具體到髮色、服裝顏色，60 集都不換造型。
- 可用聲線：{_voices_text()}。男女角色選對應聲線，主要角色盡量不要重複。

只輸出 JSON，格式如下：
{json.dumps(BIBLE_SCHEMA, ensure_ascii=False, indent=1)}
"""


def outline_prompt(bible: dict, start: int, end: int, total: int, previous: list[dict]) -> str:
    prev_text = "\n".join(f"第{e['ep']}集《{e['title']}》：{e['summary']}" for e in previous[-15:]) or "（這是第一批）"
    arc_hint = ""
    if end >= total:
        arc_hint = f"第 {total} 集是大結局，必須收束主線、給出最終爽點。"
    return f"""你是短劇總編劇。這部劇共 {total} 集。請寫第 {start}～{end} 集的分集大綱。

【劇本聖經】
{json.dumps({k: bible[k] for k in ("title", "logline", "genre", "tone")}, ensure_ascii=False)}
角色：{json.dumps([{k: c[k] for k in ("id", "name", "role", "personality")} for c in bible["characters"]], ensure_ascii=False)}

【前面集數摘要】
{prev_text}

{DRAMA_RULES}
{arc_hint}

只輸出 JSON，episodes 陣列要剛好包含第 {start} 到第 {end} 集：
{json.dumps(OUTLINE_SCHEMA, ensure_ascii=False, indent=1)}
"""


def episode_prompt(bible: dict, outline: dict, ep: int, seconds: int) -> str:
    eps = {e["ep"]: e for e in outline["episodes"]}
    cur = eps[ep]
    prev = eps.get(ep - 1)
    nxt = eps.get(ep + 1)
    shots_hint = max(12, round(seconds / 2.6))
    return f"""你是短劇分鏡編劇。把第 {ep} 集寫成可直接生產的分鏡劇本。

【劇本聖經】
{json.dumps({k: bible[k] for k in ("title", "genre", "tone")}, ensure_ascii=False)}
角色：{json.dumps([{k: c[k] for k in ("id", "name", "role", "personality")} for c in bible["characters"]], ensure_ascii=False)}
場景：{json.dumps([{k: l[k] for k in ("id", "name")} for l in bible["locations"]], ensure_ascii=False)}

【上一集】{json.dumps(prev, ensure_ascii=False) if prev else "無（第一集）"}
【本集大綱】{json.dumps(cur, ensure_ascii=False)}
【下一集】{json.dumps(nxt, ensure_ascii=False) if nxt else "無（大結局）"}

{DRAMA_RULES}

【分鏡要求】
- 約 {shots_hint} 個鏡頭，總長約 {seconds} 秒；每個鏡頭 0～2 句台詞。
- 第 1 個鏡頭就是鉤子；最後 1 個鏡頭是懸念定格。
- 用特寫表現情緒、中景表現對話、遠景交代場景，交替使用。
- 挑 3～5 個最有張力的鏡頭標記 "key": true（之後會做成真影片）。
- speaker 必須是角色 id 或 "narrator"。
- lines 只放「會被念出來的台詞」。禁止在台詞裡寫括號動作、音效或語氣說明（例如「（砰！）」「（冷笑）」）；音效與動作請寫進 visual。

只輸出 JSON：
{json.dumps(EPISODE_SCHEMA, ensure_ascii=False, indent=1)}
"""
