"""
核心整理器:
  parser (规则) -> TMDB -> 移动/硬链接/复制 + 重命名
"""
import os
import re
import sqlite3
import logging
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import parser as pname
from tmdb import TMDBClient
from config import settings
from notify import notify

log = logging.getLogger("organizer")


def safe_name(name: str) -> str:
    """文件系统安全的文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def unique_path(path: str) -> str:
    """目标已存在时追加序号,避免覆盖。"""
    target = Path(path)
    if not target.exists():
        return str(target)
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 1000):
        candidate = parent / f"{stem}.{i}{suffix}"
        if not candidate.exists():
            return str(candidate)
    raise FileExistsError(f"无法生成唯一目标路径: {path}")


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
        self.state = StateDB(settings.STATE_DB)

    def identify(self, filename: str) -> Optional[Dict]:
        """识别:规则解析 -> TMDB 查询。"""
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

        log.warning(f"无法识别: {filename}")
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
            return os.path.join(settings.MOVIE_DIR, folder, filename)
        else:
            # /media/TV/剧集名 (2024)/Season 01/剧集名 - S01E01.mkv
            folder = f"{title} ({year})"
            season = info.get("season") or 1
            episode = info.get("episode") or 0
            season_dir = f"Season {season:02d}"
            ep_str = f"S{season:02d}E{episode:02d}" if episode else f"S{season:02d}"
            filename = f"{title} - {ep_str}{ext}"
            return os.path.join(settings.TV_DIR, folder, season_dir, filename)

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

    def quarantine(self, src: str, base_dir: str, reason: str) -> str:
        """把文件或目录移入指定隔离目录。"""
        quarantine_dir = Path(base_dir)
        dst = quarantine_dir / Path(src).name

        if settings.DRY_RUN:
            log.info(f"[DRY-RUN][unrecognized] {src} -> {dst} ({reason})")
            return str(dst)

        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dst = Path(unique_path(str(dst)))
        shutil.move(src, dst)
        log.info(f"移入隔离目录: {src} -> {dst}")
        return str(dst)

    def move_to_unrecognized(self, src: str, reason: str) -> str:
        """把未识别文件或目录移入未识别目录。"""
        return self.quarantine(src, settings.unrecognized_dir, reason)

    def move_to_duplicates(self, src: str, reason: str) -> str:
        """把重复文件或目录移入未识别目录下的重复区域。"""
        return self.quarantine(src, settings.duplicate_dir, reason)

    def write_reason_file(self, directory: str, name: str, detail: str):
        if settings.DRY_RUN:
            log.info(f"[DRY-RUN] 写入未识别原因: {directory}/{name}")
            return
        path = Path(directory) / safe_name(name)
        path.write_text(detail, encoding="utf-8")

    def process(self, src: str):
        """处理单个文件 - 主入口"""
        src = os.path.abspath(src)

        # 即使状态库记录过,只要源文件仍在输入目录,也要重新决策。
        # 这样重复文件会进入重复目录,不会静默留在输入目录反复扫描。
        if self.state.is_done(src):
            log.info(f"源文件已有处理记录但仍存在,重新检查是否重复: {src}")

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
            self.state.mark_failed(src, "unidentified", "规则和 TMDB 均未识别成功")
            reason_name = f"{Path(src).name} 未识别到.txt"
            dst = self.move_to_unrecognized(src, "unidentified")
            self.write_reason_file(Path(dst).parent, reason_name, f"文件未识别到:\n{src}\n")
            return {"status": "failed", "reason": "unidentified", "src": src, "dst": dst}

        # 构造目标路径
        dst = self.build_target_path(src, info)

        if os.path.exists(dst):
            detail = f"目标文件已存在,源文件未整理:\n源文件: {src}\n目标文件: {dst}\n"
            self.state.mark_failed(src, "duplicate_target_exists", detail)
            duplicate_dst = self.move_to_duplicates(src, "duplicate_target_exists")
            self.write_reason_file(Path(duplicate_dst).parent, f"{Path(src).name} 目标文件已存在.txt", detail)
            notify(f"⚠️ 重复文件: {Path(src).name}")
            return {"status": "failed", "reason": "duplicate_target_exists", "src": src, "dst": duplicate_dst, "target": dst}

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

    def video_files(self, directory: str) -> List[str]:
        files = []
        for root, _, names in os.walk(directory):
            for name in names:
                path = os.path.join(root, name)
                if Path(path).suffix.lower() in settings.VIDEO_EXTENSIONS:
                    files.append(path)
        return sorted(files)

    def plan_directory(self, directory: str) -> Tuple[List[Tuple[str, Dict, str]], Optional[Dict]]:
        plan = []
        for video in self.video_files(directory):
            try:
                size_mb = os.path.getsize(video) / 1024 / 1024
                if size_mb < settings.MIN_FILE_SIZE_MB:
                    log.debug(f"目录内文件太小({size_mb:.1f}MB),跳过识别: {video}")
                    continue
            except OSError:
                continue

            info = self.identify(Path(video).name)
            if not info:
                return [], {"reason": "unidentified", "video": video}
            dst = self.build_target_path(video, info)
            if os.path.exists(dst):
                return [], {"reason": "duplicate_target_exists", "video": video, "target": dst}
            plan.append((video, info, dst))
        return plan, None

    def process_directory(self, directory: str):
        """目录作为一个事件处理:全部视频识别成功才移动,否则整个目录进未识别。"""
        directory = os.path.abspath(directory)
        if not os.path.isdir(directory):
            return {"status": "skipped", "reason": "not_directory", "src": directory}

        plan, failure = self.plan_directory(directory)
        if failure:
            failed_video = failure["video"]
            if failure["reason"] == "duplicate_target_exists":
                reason_name = f"目录中{Path(failed_video).name}目标文件已存在.txt"
                detail = (
                    "目录中存在目标文件已存在的视频,整个目录未整理:\n"
                    f"源文件: {failed_video}\n"
                    f"目标文件: {failure['target']}\n"
                )
                if not settings.DRY_RUN:
                    self.write_reason_file(directory, reason_name, detail)
                dst = self.move_to_duplicates(directory, "directory_contains_duplicate_target")
                self.state.mark_failed(directory, "directory_contains_duplicate_target", detail)
                notify(f"⚠️ 目录存在重复目标: {Path(directory).name}")
                return {"status": "failed", "reason": "directory_contains_duplicate_target", "src": directory, "dst": dst, "failed_video": failed_video, "target": failure["target"]}

            reason_name = f"目录中{Path(failed_video).name}文件未识别到.txt"
            detail = f"目录中存在未识别视频文件,整个目录未整理:\n{failed_video}\n"
            if not settings.DRY_RUN:
                self.write_reason_file(directory, reason_name, detail)
            dst = self.move_to_unrecognized(directory, "directory_contains_unidentified_video")
            self.state.mark_failed(directory, "directory_contains_unidentified_video", detail)
            notify(f"⚠️ 目录未识别: {Path(directory).name}")
            return {"status": "failed", "reason": "directory_contains_unidentified_video", "src": directory, "dst": dst, "failed_video": failed_video}

        if not plan:
            log.info(f"目录中没有可处理视频: {directory}")
            return {"status": "skipped", "reason": "no_video", "src": directory}

        results = []
        for src, info, dst in plan:
            try:
                action = self.transfer_file(src, dst)
                if action != "dry_run":
                    self.process_subtitles(src, dst)
                    if action != "exists":
                        self.state.mark(src, dst, info)
                results.append({"src": src, "dst": dst, "status": action})
            except Exception as e:
                log.exception(f"目录整理失败: {src}: {e}")
                self.state.mark_failed(src, "directory_transfer_failed", str(e))
                return {"status": "failed", "reason": "directory_transfer_failed", "src": directory, "error": str(e)}

        if not settings.DRY_RUN and settings.FILE_ACTION == "move":
            self.cleanup_empty_dirs(directory)
        notify(f"✅ 目录整理完成: {Path(directory).name}")
        return {"status": "ok", "src": directory, "count": len(results), "results": results}

    def cleanup_empty_dirs(self, directory: str):
        """移动模式下清理源目录剩余空目录。"""
        for root, dirs, files in os.walk(directory, topdown=False):
            try:
                if not dirs and not files:
                    os.rmdir(root)
            except OSError:
                pass

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
