"""
核心整理器:
  parser (规则) -> TMDB -> LLM 兜底 -> TMDB -> 移动/硬链接/复制 + 重命名
"""
import os
import re
import sqlite3
import logging
import shutil
from pathlib import Path
from typing import Optional, Dict

import parser as pname
from tmdb import TMDBClient
from llm import LLMParser
from config import settings
from notify import notify

log = logging.getLogger("organizer")


def safe_name(name: str) -> str:
    """文件系统安全的文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


class StateDB:
    """记录已处理文件,避免重复 + 提供历史查询"""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed (
                src TEXT PRIMARY KEY,
                dst TEXT,
                tmdb_id INTEGER,
                media_type TEXT,
                title TEXT,
                year TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS failed (
                src TEXT PRIMARY KEY,
                reason TEXT,
                detail TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def is_done(self, src: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM processed WHERE src = ?", (src,))
        return cur.fetchone() is not None

    def mark(self, src: str, dst: str, info: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO processed (src, dst, tmdb_id, media_type, title, year)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (src, dst, info.get("id"), info.get("type"), info.get("title"), info.get("year")))
        self.clear_failed(src)
        self.conn.commit()

    def mark_failed(self, src: str, reason: str, detail: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO failed (src, reason, detail, failed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (src, reason, detail))
        self.conn.commit()

    def clear_failed(self, src: str):
        self.conn.execute("DELETE FROM failed WHERE src = ?", (src,))
        self.conn.commit()

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM processed")
        return int(cur.fetchone()[0])

    def recent(self, limit: int = 20):
        cur = self.conn.execute("""
            SELECT src, dst, tmdb_id, media_type, title, year, processed_at
            FROM processed
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,))
        keys = ["src", "dst", "tmdb_id", "media_type", "title", "year", "processed_at"]
        return [dict(zip(keys, row)) for row in cur.fetchall()]

    def failed(self, limit: int = 50):
        cur = self.conn.execute("""
            SELECT src, reason, detail, failed_at
            FROM failed
            ORDER BY failed_at DESC
            LIMIT ?
        """, (limit,))
        keys = ["src", "reason", "detail", "failed_at"]
        return [dict(zip(keys, row)) for row in cur.fetchall()]


class MediaOrganizer:
    def __init__(self):
        self.tmdb = TMDBClient()
        self.llm = LLMParser()
        self.state = StateDB(settings.STATE_DB)

    def identify(self, filename: str) -> Optional[Dict]:
        """三级识别:规则 -> TMDB -> LLM -> TMDB"""
        # Step 1: 规则解析
        parsed = pname.parse(filename)
        log.debug(f"规则解析: {parsed}")

        # Step 2: TMDB 查询
        if parsed.title:
            result = self.tmdb.identify(parsed.title, parsed.year, parsed.is_tv)
            if result:
                # 把季集信息补上
                result["season"] = parsed.season
                result["episode"] = parsed.episode
                log.info(f"规则+TMDB 命中: {filename} -> {result['title']} ({result['year']})")
                return result

        # Step 3: LLM 兜底
        log.info(f"规则识别失败,启用 LLM 兜底: {filename}")
        llm_result = self.llm.parse(filename)
        if not llm_result:
            return None

        # Step 4: 用 LLM 结果再查 TMDB
        result = self.tmdb.identify(
            llm_result.get("title", ""),
            llm_result.get("year"),
            llm_result.get("type") == "tv",
        )
        if result:
            result["season"] = llm_result.get("season")
            result["episode"] = llm_result.get("episode")
            log.info(f"LLM+TMDB 命中: {filename} -> {result['title']} ({result['year']})")
            return result

        log.warning(f"完全无法识别: {filename}")
        return None

    def build_target_path(self, src: str, info: Dict) -> str:
        """根据 TMDB 信息构造目标路径"""
        ext = Path(src).suffix
        title = safe_name(info["title"])
        year = info.get("year") or "Unknown"

        if info["type"] == "movie":
            # /media/Movies/电影名 (2024)/电影名 (2024).mkv
            folder = f"{title} ({year})"
            filename = f"{title} ({year}){ext}"
            return os.path.join(settings.OUTPUT_ROOT, settings.MOVIE_DIR_NAME, folder, filename)
        else:
            # /media/TV/剧集名 (2024)/Season 01/剧集名 - S01E01.mkv
            folder = f"{title} ({year})"
            season = info.get("season") or 1
            episode = info.get("episode") or 0
            season_dir = f"Season {season:02d}"
            ep_str = f"S{season:02d}E{episode:02d}" if episode else f"S{season:02d}"
            filename = f"{title} - {ep_str}{ext}"
            return os.path.join(
                settings.OUTPUT_ROOT, settings.TV_DIR_NAME, folder, season_dir, filename
            )

    def transfer_file(self, src: str, dst: str):
        """按配置移动、硬链接或复制文件到目标路径。"""
        if os.path.exists(dst):
            log.info(f"目标已存在,跳过: {dst}")
            return "exists"

        if settings.DRY_RUN:
            log.info(f"[DRY-RUN][{settings.FILE_ACTION}] {src} -> {dst}")
            return "dry_run"

        Path(dst).parent.mkdir(parents=True, exist_ok=True)

        if settings.FILE_ACTION == "move":
            shutil.move(src, dst)
            log.info(f"移动/重命名成功: {dst}")
            return "moved"

        if settings.FILE_ACTION == "hardlink":
            try:
                os.link(src, dst)
                log.info(f"硬链接成功: {dst}")
                return "hardlinked"
            except OSError as e:
                log.warning(f"硬链接失败(可能跨设备),改为复制: {e}")

        shutil.copy2(src, dst)
        log.info(f"复制成功: {dst}")
        return "copied"

    def link_or_copy(self, src: str, dst: str):
        """兼容旧调用名。"""
        self.transfer_file(src, dst)

    def process_subtitles(self, src: str, dst: str):
        """同目录下同名字幕跟随处理"""
        src_path = Path(src)
        dst_path = Path(dst)
        base = src_path.stem

        for sub in src_path.parent.glob(f"{base}*"):
            if sub.suffix.lower() in settings.SUBTITLE_EXTENSIONS:
                # 字幕保留语言后缀,如 .zh.srt
                sub_suffix = sub.name[len(base):]
                sub_dst = dst_path.parent / (dst_path.stem + sub_suffix)
                try:
                    self.transfer_file(str(sub), str(sub_dst))
                except Exception as e:
                    log.warning(f"字幕处理失败 {sub}: {e}")

    def process(self, src: str):
        """处理单个文件 - 主入口"""
        src = os.path.abspath(src)

        # 跳过已处理
        if self.state.is_done(src):
            log.debug(f"已处理过,跳过: {src}")
            return {"status": "skipped", "reason": "already_processed", "src": src}

        # 跳过小文件(样片、海报)
        try:
            size_mb = os.path.getsize(src) / 1024 / 1024
            if size_mb < settings.MIN_FILE_SIZE_MB:
                log.debug(f"文件太小({size_mb:.1f}MB),跳过: {src}")
                return {"status": "skipped", "reason": "too_small", "src": src, "size_mb": size_mb}
        except OSError:
            return {"status": "skipped", "reason": "missing_or_unreadable", "src": src}

        log.info(f"处理: {src}")

        # 识别
        info = self.identify(Path(src).name)
        if not info:
            notify(f"⚠️ 无法识别: {Path(src).name}")
            self.state.mark_failed(src, "unidentified", "规则、TMDB、LLM 均未识别成功")
            return {"status": "failed", "reason": "unidentified", "src": src}

        # 构造目标路径
        dst = self.build_target_path(src, info)

        # 执行链接
        try:
            action = self.transfer_file(src, dst)
            if action == "dry_run":
                return {"status": "dry_run", "src": src, "dst": dst, "info": info}

            self.process_subtitles(src, dst)
            if action != "exists":
                self.state.mark(src, dst, info)

            type_emoji = "🎬" if info["type"] == "movie" else "📺"
            notify(f"{type_emoji} 整理完成: {info['title']} ({info['year']})")
            return {"status": "ok" if action != "exists" else "skipped", "reason": action, "src": src, "dst": dst, "info": info}
        except Exception as e:
            log.exception(f"链接失败: {e}")
            notify(f"❌ 整理失败: {Path(src).name}\n{e}")
            self.state.mark_failed(src, "link_failed", str(e))
            return {"status": "failed", "reason": "link_failed", "src": src, "error": str(e)}

    def process_manual(self, src: str, info: Dict):
        """使用人工提供的信息整理文件。"""
        src = os.path.abspath(src)
        if not os.path.exists(src):
            self.state.mark_failed(src, "missing_or_unreadable", "人工处理时源文件不存在")
            return {"status": "failed", "reason": "missing_or_unreadable", "src": src}

        info = dict(info)
        info["type"] = info.get("type") or "movie"
        info["title"] = (info.get("title") or "").strip()
        if not info["title"]:
            return {"status": "failed", "reason": "missing_title", "src": src}

        dst = self.build_target_path(src, info)
        try:
            action = self.transfer_file(src, dst)
            if action == "dry_run":
                return {"status": "dry_run", "src": src, "dst": dst, "info": info}

            self.process_subtitles(src, dst)
            if action != "exists":
                self.state.mark(src, dst, info)
            notify(f"✅ 人工整理完成: {info['title']} ({info.get('year') or 'Unknown'})")
            return {"status": "ok" if action != "exists" else "skipped", "reason": action, "src": src, "dst": dst, "info": info}
        except Exception as e:
            log.exception(f"人工整理失败: {e}")
            self.state.mark_failed(src, "manual_failed", str(e))
            return {"status": "failed", "reason": "manual_failed", "src": src, "error": str(e)}
