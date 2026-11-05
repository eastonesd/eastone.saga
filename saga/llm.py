"""LLM 呼叫層：多家供應商備援鏈，任何一家擁塞就立刻換下一家，不空等。

順序：Gemini → Groq → OpenRouter（有設金鑰的才會啟用；各自自動偵測可用模型）
--mock 時產生假資料供測試。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import requests


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
        return json.loads(text[start:text.rfind("}") + 1])


class _Unavailable(Exception):
    """這個模型暫時不能用（擁塞、沒額度、不存在）→ 換下一個"""


# ── 供應商 1：Gemini ───────────────────────────────────────

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_FALLBACK = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]
_EXCLUDE = ("image", "tts", "audio", "live", "embed", "vision", "computer", "robotics", "aqa", "learnlm", "nano")


def _gemini_rank(name: str) -> tuple:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    ver = float(m.group(1)) if m else (0.5 if "latest" in name else 0.0)
    tier = 0 if ("flash" in name and "lite" not in name) else 1 if "flash" in name else 2
    return (tier, -ver, any(t in name for t in ("preview", "exp")), name)


class Gemini:
    name = "Gemini"

    def __init__(self, key: str):
        self.key = key
        self._models: list[str] | None = None

    def models(self) -> list[str]:
        if self._models is None:
            names = []
            try:
                r = requests.get(f"{GEMINI_BASE}/models", params={"key": self.key, "pageSize": 1000}, timeout=20)
                r.raise_for_status()
                for m in r.json().get("models", []):
                    n = m["name"].removeprefix("models/")
                    if ("generateContent" in m.get("supportedGenerationMethods", [])
                            and n.startswith("gemini") and not any(x in n for x in _EXCLUDE)):
                        names.append(n)
            except requests.RequestException as e:
                print(f"  Gemini 模型清單查詢失敗：{e}", flush=True)
            names = sorted(set(names), key=_gemini_rank)[:6] or GEMINI_FALLBACK
            pinned = os.environ.get("GEMINI_MODEL")
            self._models = ([pinned] if pinned else []) + [n for n in names if n != pinned]
        return self._models

    def call(self, model: str, prompt: str, temperature: float) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent", params={"key": self.key},
                          json=body, timeout=180)
        if not r.ok:
            raise _Unavailable(f"HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])


# ── 供應商 2+：OpenAI 相容的免費服務（Groq、OpenRouter）─────────

class OpenAICompat:
    """任何 OpenAI 相容 API。prefer 為模型 id 的偏好規則（正規表示式，越前面越優先）。"""

    def __init__(self, name: str, base: str, key: str, prefer: list[str], only=None, json_mode=True):
        self.name, self.base, self.key = name, base.rstrip("/"), key
        self.prefer, self.only, self.json_mode = prefer, only, json_mode
        self._models: list[str] | None = None

    def _h(self):
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def models(self) -> list[str]:
        if self._models is None:
            ids = []
            try:
                r = requests.get(f"{self.base}/models", headers=self._h(), timeout=20)
                r.raise_for_status()
                ids = [m["id"] for m in r.json().get("data", []) if not self.only or self.only(m)]
            except (requests.RequestException, ValueError) as e:
                print(f"  {self.name} 模型清單查詢失敗：{e}", flush=True)

            def rank(i: str) -> int:
                return next((k for k, p in enumerate(self.prefer) if re.search(p, i, re.I)), 99)
            ids = [i for i in ids if not re.search(r"safeguard|guard|whisper|tts|embed", i, re.I)]
            self._models = [i for i in sorted(ids, key=rank) if rank(i) < 99][:4]
        return self._models

    def call(self, model: str, prompt: str, temperature: float) -> str:
        body = {"model": model, "temperature": temperature,
                "messages": [{"role": "system", "content": "只輸出合法 JSON，不要任何說明文字。"},
                             {"role": "user", "content": prompt}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(f"{self.base}/chat/completions", headers=self._h(), json=body, timeout=180)
        if not r.ok:
            raise _Unavailable(f"HTTP {r.status_code} {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except ValueError:
            raise _Unavailable(f"非 JSON 回應：{r.text[:120]}")


# ── 調度 ──────────────────────────────────────────────────

_lock = threading.Lock()
_providers: list | None = None
_cooldown: dict[str, float] = {}  # 模型 → 冷卻到何時
COOLDOWN_S = 65


def _get_providers() -> list:
    global _providers
    with _lock:
        if _providers is None:
            providers = []
            if os.environ.get("GEMINI_API_KEY"):
                providers.append(Gemini(os.environ["GEMINI_API_KEY"]))
            if os.environ.get("GROQ_API_KEY"):
                providers.append(OpenAICompat(
                    "Groq", "https://api.groq.com/openai/v1", os.environ["GROQ_API_KEY"],
                    prefer=[r"qwen", r"kimi", r"gpt-oss-120b", r"deepseek", r"llama-4", r"llama-3\.3-70b", r"gpt-oss"]))
            if os.environ.get("OPENROUTER_API_KEY"):
                providers.append(OpenAICompat(
                    "OpenRouter", "https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"],
                    prefer=[r"deepseek", r"qwen", r"glm", r"kimi", r"gpt-oss", r"llama"],
                    only=lambda m: m["id"].endswith(":free"), json_mode=False))
            if not providers:
                raise LLMError("沒有可用的 LLM：請至少設定 GEMINI_API_KEY 或 GROQ_API_KEY")
            for p in providers:
                print(f"  {p.name} 候選：{', '.join(p.models())}", flush=True)
            _providers = providers
    return _providers


def _generate(prompt: str, temperature: float) -> dict:
    errors: list[str] = []
    deadline = time.time() + 15 * 60
    for round_no in range(4):
        for p in _get_providers():
            for model in p.models():
                tag = f"{p.name}/{model}"
                if _cooldown.get(tag, 0) > time.time():
                    continue
                try:
                    t0 = time.time()
                    result = _extract_json(p.call(model, prompt, temperature))
                    print(f"  ✓ {tag}（{time.time() - t0:.0f}s）", flush=True)
                    return result
                except (_Unavailable, requests.RequestException) as e:
                    msg = str(e)
                    errors.append(f"{tag}: {msg[:160]}")
                    print(f"  ✗ {tag} → {msg[:120]}", flush=True)
                    # 不存在／無權限：這次執行不再試；擁塞／額度：冷卻一陣子
                    gone = "HTTP 404" in msg or "HTTP 403" in msg or "HTTP 401" in msg
                    daily = "HTTP 429" in msg and re.search(r"quota|per ?day|PerDay|daily", msg, re.I)
                    _cooldown[tag] = time.time() + (10**6 if gone else 3600 if daily else COOLDOWN_S)
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                    errors.append(f"{tag}: 回傳不是合法 JSON（{e}）")
                    print(f"  ✗ {tag} → 回傳不是合法 JSON", flush=True)
        if time.time() > deadline:
            break
        soonest = min((t for t in _cooldown.values() if t > time.time()), default=time.time() + 5)
        wait = max(5, min(60, soonest - time.time()))
        print(f"  所有模型都忙，{wait:.0f}s 後再試一輪…", flush=True)
        time.sleep(wait)
    raise LLMError("所有 LLM 都失敗：\n" + "\n".join(errors[-10:]))


def generate_json(prompt: str, *, kind: str, mock: bool = False, context: dict | None = None,
                  temperature: float = 0.9) -> dict:
    if mock:
        from . import mock as _mock
        return _mock.fake(kind, context or {})
    return _generate(prompt, temperature)
