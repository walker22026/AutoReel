"""TMDB API 客户端"""
import logging
import requests
from typing import Optional, List, Dict
from config import settings

log = logging.getLogger("tmdb")

BASE = "https://api.themoviedb.org/3"


class TMDBClient:
    def __init__(self):
        self.session = requests.Session()
        self.configure(settings.TMDB_API_KEY, settings.TMDB_LANG)

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

    def search_movie(self, title: str, year: Optional[int] = None) -> List[Dict]:
        params = {"query": title}
        if year:
            params["year"] = year
        data = self._get("/search/movie", **params)
        return (data or {}).get("results", [])

    def search_tv(self, title: str, year: Optional[int] = None) -> List[Dict]:
        params = {"query": title}
        if year:
            params["first_air_date_year"] = year
        data = self._get("/search/tv", **params)
        return (data or {}).get("results", [])

    def best_match(self, results: List[Dict], year: Optional[int] = None, media_type: str = "movie") -> Optional[Dict]:
        """从搜索结果选最匹配的一个"""
        if not results:
            return None

        # 有年份就优先匹配
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

        # 否则取 popularity 最高的(TMDB 默认已按相关性排序)
        return results[0]

    def identify(self, title: str, year: Optional[int] = None, is_tv: bool = False) -> Optional[Dict]:
        """识别一个标题。返回标准化的 dict 或 None。"""
        if not title:
            return None

        if is_tv:
            results = self.search_tv(title, year)
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
            results = self.search_movie(title, year)
            match = self.best_match(results, year, "movie")
            if match:
                return {
                    "type": "movie",
                    "id": match["id"],
                    "title": match.get("title") or match.get("original_title"),
                    "original_title": match.get("original_title"),
                    "year": (match.get("release_date") or "")[:4] or None,
                }

        # 类型猜错了? 反向再试一次
        if not is_tv:
            results = self.search_tv(title, year)
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
