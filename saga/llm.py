"""LLM 呼叫層：預設用 Google Gemini 免費額度，--mock 時產生假資料供測試。"""
from __future__ import annotations

import json
import os
import re
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# 依序嘗試；Google 換代時只要改環境變數 GEMINI_MODEL 即可
DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]


class LLMError(RuntimeError):
    pass


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.loads(text[start:])


def _gemini(prompt: str, temperature: float) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("缺少 GEMINI_API_KEY（到 https://aistudio.google.com/apikey 免費申請）")
    models = [m for m in [os.environ.get("GEMINI_MODEL")] if m] + DEFAULT_MODELS
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    last_err = None
    for model in models:
        for attempt in range(4):
            try:
                r = requests.post(
                    GEMINI_URL.format(model=model),
                    params={"key": key},
                    json=body,
                    timeout=180,
                )
                if r.status_code == 404:
                    last_err = f"{model}: 404"
                    break  # 換下一個模型
                if r.status_code == 429 or r.status_code >= 500:
                    wait = 20 * (attempt + 1)
                    print(f"  Gemini {r.status_code}，{wait}s 後重試…", flush=True)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                return _extract_json(text)
            except (json.JSONDecodeError, KeyError) as e:
                last_err = f"{model}: 回傳格式錯誤 {e}"
                time.sleep(3)
            except requests.RequestException as e:
                last_err = f"{model}: {e}"
                time.sleep(5)
    raise LLMError(f"Gemini 呼叫失敗：{last_err}")


def generate_json(prompt: str, *, kind: str, mock: bool = False, context: dict | None = None,
                  temperature: float = 0.9) -> dict:
    if mock:
        from . import mock as _mock
        return _mock.fake(kind, context or {})
    return _gemini(prompt, temperature)
