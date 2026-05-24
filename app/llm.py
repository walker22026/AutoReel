"""
LLM 兜底：当规则解析 + TMDB 直接搜索都失败时，
让 LLM 推断这个标题在 TMDB 上可能使用的候选搜索词（英文原名、别名等）。

不替代 parser，只生成 TMDB 重试用的候选词列表。
LLM_API_URL 为空时整个模块静默禁用。
"""
import json
import logging
import requests
from typing import List

from config import settings

log = logging.getLogger("llm")

# 只让 LLM 做一件事：给出 TMDB 可能收录的候选搜索词
PROMPT = """\
你是一个影视数据库搜索助手。用户有一个影视标题在 TMDB 中文搜索时找不到，
请推断它在 TMDB 上最可能使用的 1-3 个候选标题（英文原名、国际通行译名或别名）。

待查标题：{title}

要求：
- 只返回 JSON 数组，元素为字符串，不要解释
- 按可信度从高到低排列
- 如果原标题已是英文，尝试给出其他常见拼写或别名
- 示例输出：["Shanghai Wonton", "菜肉馄饨"]
"""


def _enabled() -> bool:
    return bool(settings.LLM_API_URL and settings.LLM_API_KEY)


def suggest_titles(title: str) -> List[str]:
    """
    返回 LLM 推荐的候选搜索词列表。
    未配置 LLM 或请求失败时返回空列表。
    """
    if not _enabled():
        return []

    prompt = PROMPT.format(title=title)
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 100,
    }
    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.LLM_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()

        # 提取 JSON 数组（LLM 偶尔会带 markdown 代码块）
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            log.warning(f"LLM 返回格式异常: {text!r}")
            return []

        candidates = json.loads(text[start:end])
        if not isinstance(candidates, list):
            return []

        result = [str(c).strip() for c in candidates if str(c).strip()]
        log.info(f"LLM 候选词 for '{title}': {result}")
        return result

    except Exception as e:
        log.warning(f"LLM 请求失败: {e}")
        return []
