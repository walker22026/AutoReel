"""TMDB API 客户端"""
import json
import logging
import requests
from pathlib import Path
from typing import Optional, List, Dict
from config import settings

log = logging.getLogger("tmdb")

BASE = "https://api.themoviedb.org/3"


def _load_aliases() -> Dict[str, str]:
    """加载用户别名表 /config/aliases.json，文件不存在时返回空字典。"""
    path = Path(settings.ALIASES_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 统一转小写 key，便于大小写不敏感匹配
            return {k.strip().lower(): v.strip() for k, v in data.items() if k and v}
    except Exception as e:
        log.warning(f"别名表加载失败: {e}")
    return {}


class TMDBClient:
    def __init__(self):
        self.session = requests.Session()
        self.configure(settings.TMDB_API_KEY, settings.TMDB_LANG)
        self._aliases = _load_aliases()

    def reload_aliases(self):
        """热重载别名表（无需重启容器）。"""
        self._aliases = _load_aliases()
        log.info(f"别名表已重载，共 {len(self._aliases)} 条")

    def configure(self, key: str, lang: str):
        self.key = key or ""
        self.lang = lang or "zh-CN"

    def _get(self, path: str, **params) -> Optional[dict]:
        params["api_key"] = self.key
        params.setdefault("language", self.lang)
        try:
            r = self.session.get(f"{BASE}{path}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"TMDB 请求失败 {path}: {e}")
            return None

    def search_movie(self, title: str) -> List[Dict]:
        data = self._get("/search/movie", query=title)
        return (data or {}).get("results", [])

    def search_tv(self, title: str) -> List[Dict]:
        data = self._get("/search/tv", query=title)
        return (data or {}).get("results", [])

    def best_match(self, results: List[Dict], year: Optional[int] = None, media_type: str = "movie") -> Optional[Dict]:
        """从搜索结果选最匹配的一个"""
        if not results:
            return None

        # 有年份时必须匹配年份或 ±1 年容差,避免把同名但年份很远的作品误命中。
        if year:
            date_field = "release_date" if media_type == "movie" else "first_air_date"
            for r in results:
                d = r.get(date_field, "")
                if d.startswith(str(year)):
                    return r
            # 容差 ±1 年
            for r in results:
                d = r.get(date_field, "")[:4]
                if d.isdigit() and abs(int(d) - year) <= 1:
                    return r
            return None

        # 否则取 popularity 最高的(TMDB 默认已按相关性排序)
        return results[0]

    def _search_one(self, title: str, year: Optional[int], is_tv: bool) -> Optional[Dict]:
        """用单个标题搜索，含反向类型重试。
        年份不传给 TMDB API（避免 API 层硬过滤导致空结果），
        由 best_match 做 ±1 年容差匹配。
        """
        if is_tv:
            results = self.search_tv(title)
            match = self.best_match(results, year, "tv")
            if match:
                return {
                    "type": "tv",
                    "id": match["id"],
                    "title": match.get("name") or match.get("original_name"),
                    "original_title": match.get("original_name"),
                    "year": (match.get("first_air_date") or "")[:4] or None,
                }
        else:
            results = self.search_movie(title)
            match = self.best_match(results, year, "movie")
            if match:
                return {
                    "type": "movie",
                    "id": match["id"],
                    "title": match.get("title") or match.get("original_title"),
                    "original_title": match.get("original_title"),
                    "year": (match.get("release_date") or "")[:4] or None,
                }
            # 类型猜错了？反向再试一次
            results = self.search_tv(title)
            match = self.best_match(results, year, "tv")
            if match:
                log.info(f"'{title}' 实际是剧集而非电影")
                return {
                    "type": "tv",
                    "id": match["id"],
                    "title": match.get("name") or match.get("original_name"),
                    "original_title": match.get("original_name"),
                    "year": (match.get("first_air_date") or "")[:4] or None,
                }
        return None

    def identify(self, title: str, year: Optional[int] = None, is_tv: bool = False) -> Optional[Dict]:
        """识别一个标题。优先级：别名表 > TMDB 直接搜索。"""
        if not title:
            return None

        # 第一步：别名表（用户手动维护，最高优先级，100% 准确）
        alias = self._aliases.get(title.strip().lower())
        if alias:
            log.info(f"别名表命中: '{title}' -> '{alias}'")
            result = self._search_one(alias, year, is_tv)
            if result:
                return result
            log.warning(f"别名表映射 '{alias}' 在 TMDB 仍未找到，继续正常流程")

        # 第二步：直接搜索原始标题
        return self._search_one(title, year, is_tv)
