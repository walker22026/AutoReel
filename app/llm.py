"""
LLM 兜底:当规则解析 + TMDB 搜索都失败时,
让 LLM 从混乱的文件名里提取干净的片名和年份。
对接你已有的 LiteLLM gateway。
"""
import json
import logging
import re
import requests
from typing import Optional, Dict
from config import settings

log = logging.getLogger("llm")


PROMPT_TEMPLATE = """You are a media file name parser. Extract the clean movie or TV show title from a messy filename.

Filename: {filename}

Rules:
1. Identify whether this is a MOVIE or TV episode
2. Extract the clean title (in original language - Chinese stays Chinese, English stays English)
3. If both Chinese and English titles are present, prefer the Chinese title for Chinese productions, English for foreign
4. Extract the year if present
5. For TV: extract season and episode numbers
6. Ignore release group tags, codec info, resolution, source info

Respond with ONLY a JSON object, no markdown, no explanation:
{{
  "type": "movie" or "tv",
  "title": "clean title",
  "year": YYYY or null,
  "season": N or null,
  "episode": N or null,
  "confidence": 0.0-1.0
}}"""


class LLMParser:
    def __init__(self):
        self.configure(settings.LITELLM_BASE, settings.LITELLM_KEY, settings.LITELLM_MODEL)

    def configure(self, base: str, key: str, model: str):
        self.base = (base or "").rstrip("/")
        self.key = key or ""
        self.model = model or ""
        self.enabled = bool(self.base and self.model)

    def parse(self, filename: str) -> Optional[Dict]:
        if not self.enabled:
            return None

        try:
            r = requests.post(
                f"{self.base}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": PROMPT_TEMPLATE.format(filename=filename)}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
                timeout=30,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]

            # 容忍 LLM 偶尔输出代码块包裹
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
            result = json.loads(content)

            log.info(f"LLM 解析 '{filename}' -> {result}")
            return result
        except Exception as e:
            log.warning(f"LLM 解析失败 '{filename}': {e}")
            return None
