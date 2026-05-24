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

IGNORED_DIR_NAMES = {"@eaDir", ".DS_Store", "__MACOSX"}
REASON_FILE_PATTERNS = [
    re.compile(r'^目录.*无法识别\.txt$'),
    re.compile(r'^目录中.*文件未识别到\.txt$'),
    re.compile(r'^目录中.*目标文件已存在\.txt$'),
    re.compile(r'.*\s未识别到\.txt$'),
    re.compile(r'.*\s目标文件已存在\.txt$'),
]


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


def extract_episode_from_filename(path: str) -> Tuple[Optional[int], Optional[int]]:
    """在剧集目录上下文中,从文件名提取季集号。"""
    parsed = pname.parse(Path(path).name)
    season = parsed.season
    episode = parsed.episode
    if episode is not None:
        return season, episode

    stem = Path(path).stem
    # 常见网盘剧集命名: 01 4K.mp4 / 01.mp4 / 第01集.mp4
    m = re.search(r'(?:^|[\s._\-\[【(])(?:第)?0*(\d{1,3})(?:集|话|話)?(?:[\s._\-\]】)]|$)', stem)
    if m:
        value = int(m.group(1))
        if 1 <= value <= 200:
            return season, value
    return season, None


def is_episode_style_filename(path: str) -> bool:
    """判断文件名是否像剧集编号,而不是完整片名。"""
    stem = Path(path).stem.strip()
    compact = re.sub(r'(?i)\b(4k|8k|2160p|1080p|720p|uhd|hdr|hevc|x26[45]|h\.?26[45]|aac|ddp?\d?\.?\d?)\b', '', stem)
    compact = re.sub(r'[\s._\-\[\]【】()（）]+', '', compact)
    return bool(re.fullmatch(r'(?:s\d{1,2}e\d{1,3}|(?:第)?\d{1,3}(?:集|话|話)?)', compact, re.I))


def is_likely_tv_batch(videos: List[str]) -> bool:
    if len(videos) < 2:
        return False
    return all(is_episode_style_filename(video) for video in videos)


def should_skip_path(path: str | Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in Path(path).parts)


def is_reason_file(path: str | Path) -> bool:
    name = Path(path).name
    return any(pattern.match(name) for pattern in REASON_FILE_PATTERNS)


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
        parsed = pname.parse(filename)
        log.debug(f"规则解析: {parsed}")

        if parsed.title:
            result = self.tmdb.identify(parsed.title, parsed.year, parsed.is_tv)
            if result:
                result["season"] = parsed.season
                result["episode"] = parsed.episode
                log.info(f"规则+TMDB 命中: {filename} -> {result['title']} ({result['year']})")
                return result

        log.warning(f"无法识别: {filename}")
        return None

    def identify_dir(self, dirname: str, force_tv: bool = False) -> Optional[Dict]:
        """使用目录名识别媒体信息（目录批次专用）。"""
        parsed = pname.parse(dirname)
        log.debug(f"目录名规则解析: {parsed}")

        if parsed.title:
            result = self.tmdb.identify(parsed.title, parsed.year, parsed.is_tv or force_tv)
            if result:
                result["season"] = parsed.season
                result["episode"] = parsed.episode
                log.info(f"目录名识别命中: {dirname} -> {result['title']} ({result['year']})")
                return result

        log.warning(f"目录名无法识别: {dirname}")
        return None

    def build_target_path(self, src: str, info: Dict, keep_name: bool = False) -> str:
        """根据 TMDB 信息构造目标路径。
        keep_name=True 仅用于兼容旧调用；默认按媒体库规范重命名。
        """
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
            filename = Path(src).name if keep_name else f"{title} - {ep_str}{ext}"
            return os.path.join(settings.TV_DIR, folder, season_dir, filename)

    def _target_show_root(self, info: Dict) -> str:
        """返回剧集/电影的顶层目标目录（用于存放附属文件）。"""
        title = safe_name(info["title"])
        year = info.get("year") or "Unknown"
        folder = f"{title} ({year})"
        base = settings.TV_DIR if info["type"] == "tv" else settings.MOVIE_DIR
        return os.path.join(base, folder)

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
        """同目录下同名字幕跟随处理。字幕按目标视频文件名规范重命名。"""
        src_path = Path(src)
        dst_path = Path(dst)
        base = src_path.stem

        # glob 不支持特殊字符,改用目录遍历匹配
        for sub in src_path.parent.iterdir():
            if not sub.is_file():
                continue
            if sub.suffix.lower() not in settings.SUBTITLE_EXTENSIONS:
                continue
            if not sub.name.startswith(base):
                continue
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

    def move_to_pending_delete(self, src: str, reason: str) -> str:
        """把空目录移入待删除目录。"""
        return self.quarantine(src, settings.pending_delete_dir, reason)

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
        for root, dirs, names in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
            if should_skip_path(root):
                continue
            for name in names:
                path = os.path.join(root, name)
                if should_skip_path(path):
                    continue
                if Path(path).suffix.lower() in settings.VIDEO_EXTENSIONS:
                    files.append(path)
        return sorted(files)

    def has_subtitle_files(self, directory: str) -> bool:
        for root, dirs, names in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
            if should_skip_path(root):
                continue
            for name in names:
                if Path(name).suffix.lower() in settings.SUBTITLE_EXTENSIONS:
                    return True
        return False

    def validate_plan_targets(self, plan: List[Tuple[str, Dict, str]]) -> Optional[Dict]:
        seen_targets = {}
        for src, _info, dst in plan:
            if os.path.exists(dst):
                return {"reason": "duplicate_target_exists", "video": src, "target": dst}
            if dst in seen_targets:
                return {"reason": "duplicate_target_in_batch", "video": src, "target": dst, "other": seen_targets[dst]}
            seen_targets[dst] = src
        return None

    def plan_directory(self, directory: str) -> Tuple[List[Tuple[str, Dict, str]], Optional[Dict]]:
        dir_path = Path(directory)
        videos = self.video_files(directory)

        force_tv = is_likely_tv_batch(videos)
        dir_info = self.identify_dir(dir_path.name, force_tv=force_tv)
        if dir_info and dir_info["type"] == "tv":
            return self.plan_tv_directory(directory, dir_info, videos)
        if dir_info and dir_info["type"] == "movie" and len(videos) == 1:
            dst = self.build_target_path(videos[0], dir_info)
            if os.path.exists(dst):
                return [], {"reason": "duplicate_target_exists", "video": videos[0], "target": dst}
            return [(videos[0], dir_info, dst)], None

        # 电影合集或目录名无法识别时,逐个视频文件识别。全部成功才整理。
        plan = []
        seen_targets = {}
        for video in videos:
            try:
                size_mb = os.path.getsize(video) / 1024 / 1024
                if size_mb < settings.MIN_FILE_SIZE_MB:
                    log.debug(f"目录内文件太小({size_mb:.1f}MB),跳过识别: {video}")
                    continue
            except OSError:
                continue

            info = self.identify(Path(video).name)
            if not info:
                return [], {"reason": "unidentified_video", "video": video}
            dst = self.build_target_path(video, info)
            if os.path.exists(dst):
                return [], {"reason": "duplicate_target_exists", "video": video, "target": dst}
            if dst in seen_targets:
                return [], {"reason": "duplicate_target_in_batch", "video": video, "target": dst, "other": seen_targets[dst]}
            seen_targets[dst] = video
            plan.append((video, info, dst))
        return plan, None

    def plan_tv_directory(self, directory: str, dir_info: Dict, videos: List[str]) -> Tuple[List[Tuple[str, Dict, str]], Optional[Dict]]:
        plan = []
        seen_targets = {}
        for video in videos:
            try:
                size_mb = os.path.getsize(video) / 1024 / 1024
                if size_mb < settings.MIN_FILE_SIZE_MB:
                    log.debug(f"目录内文件太小({size_mb:.1f}MB),跳过识别: {video}")
                    continue
            except OSError:
                continue

            season, episode = extract_episode_from_filename(video)
            if episode is None:
                return [], {"reason": "episode_unidentified", "video": video}

            info = dict(dir_info)
            info["season"] = season or dir_info.get("season") or 1
            info["episode"] = episode
            dst = self.build_target_path(video, info, keep_name=False)
            if os.path.exists(dst):
                return [], {"reason": "duplicate_target_exists", "video": video, "target": dst}
            if dst in seen_targets:
                return [], {"reason": "duplicate_target_in_batch", "video": video, "target": dst, "other": seen_targets[dst]}
            seen_targets[dst] = video
            plan.append((video, info, dst))
        return plan, None

    def process_directory(self, directory: str):
        """目录作为一个事件处理:全部视频识别成功才移动,否则整个目录进未识别。"""
        directory = os.path.abspath(directory)
        if not os.path.isdir(directory):
            return {"status": "skipped", "reason": "not_directory", "src": directory}

        plan, failure = self.plan_directory(directory)
        if failure:
            if failure["reason"] in {"duplicate_target_exists", "duplicate_target_in_batch"}:
                failed_video = failure["video"]
                reason_name = f"目录中{Path(failed_video).name}目标文件已存在.txt"
                detail = (
                    "目录中存在重复目标文件,整个目录未整理:\n"
                    f"源文件: {failed_video}\n"
                    f"目标文件: {failure['target']}\n"
                )
                if failure.get("other"):
                    detail += f"同批次另一源文件: {failure['other']}\n"
                if not settings.DRY_RUN:
                    self.write_reason_file(directory, reason_name, detail)
                dst = self.move_to_duplicates(directory, "directory_contains_duplicate_target")
                self.state.mark_failed(directory, "directory_contains_duplicate_target", detail)
                notify(f"⚠️ 目录存在重复目标: {Path(directory).name}")
                return {"status": "failed", "reason": "directory_contains_duplicate_target", "src": directory, "dst": dst}

            failed_video = failure.get("video")
            if failed_video:
                reason_name = f"目录中{Path(failed_video).name}文件未识别到.txt"
                detail = f"目录中存在无法识别的视频文件,整个目录未整理:\n{failed_video}\n原因: {failure['reason']}\n"
            else:
                dir_name = Path(directory).name
                reason_name = f"目录{dir_name}无法识别.txt"
                detail = f"目录名无法识别,整个目录未整理:\n{directory}\n"
            if not settings.DRY_RUN:
                self.write_reason_file(directory, reason_name, detail)
            dst = self.move_to_unrecognized(directory, "directory_unidentified")
            self.state.mark_failed(directory, "directory_unidentified", detail)
            notify(f"⚠️ 目录未识别: {Path(directory).name}")
            return {"status": "failed", "reason": "directory_unidentified", "src": directory, "dst": dst}

        if not plan:
            if self.has_subtitle_files(directory):
                log.info(f"目录中只有字幕文件，没有视频，移入未识别: {directory}")
                dst = self.move_to_unrecognized(directory, "subtitle_only")
                self.state.mark_failed(directory, "subtitle_only", "目录中只有字幕文件，没有视频")
                notify(f"⚠️ 仅含字幕无视频: {Path(directory).name}")
                return {"status": "failed", "reason": "subtitle_only", "src": directory, "dst": dst}
            log.info(f"目录中无视频无字幕，移入待删除: {directory}")
            dst = self.move_to_pending_delete(directory, "empty_or_no_media")
            return {"status": "skipped", "reason": "no_media", "src": directory, "dst": dst}

        duplicate_failure = self.validate_plan_targets(plan)
        if duplicate_failure:
            failed_video = duplicate_failure["video"]
            detail = (
                "目录中存在重复目标文件,整个目录未整理:\n"
                f"源文件: {failed_video}\n"
                f"目标文件: {duplicate_failure['target']}\n"
            )
            if duplicate_failure.get("other"):
                detail += f"同批次另一源文件: {duplicate_failure['other']}\n"
            if not settings.DRY_RUN:
                self.write_reason_file(directory, f"目录中{Path(failed_video).name}目标文件已存在.txt", detail)
            dst = self.move_to_duplicates(directory, "directory_contains_duplicate_target")
            self.state.mark_failed(directory, "directory_contains_duplicate_target", detail)
            notify(f"⚠️ 目录存在重复目标: {Path(directory).name}")
            return {"status": "failed", "reason": "directory_contains_duplicate_target", "src": directory, "dst": dst}

        # 所有 plan 共享同一个 dir_info，取第一条的 info 即可
        dir_info = plan[0][1]
        results = []
        for src, info, dst in plan:
            try:
                action = self.transfer_file(src, dst)
                if action == "exists":
                    detail = f"执行前目标文件已存在,目录停止整理:\n源文件: {src}\n目标文件: {dst}\n"
                    self.state.mark_failed(directory, "directory_transfer_duplicate", detail)
                    return {"status": "failed", "reason": "directory_transfer_duplicate", "src": directory, "target": dst}
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
            # 把目录内剩余附属文件（非视频）也一起迁移到目标目录
            target_root = self._target_show_root(dir_info)
            self._move_remaining_files(directory, target_root)
            # 清理已空的源目录
            self.cleanup_empty_dirs(directory)

        notify(f"✅ 目录整理完成: {Path(directory).name}")
        return {"status": "ok", "src": directory, "count": len(results), "results": results}

    def _move_remaining_files(self, directory: str, target_root: str):
        """把源目录中视频以外的文件（.nfo、图片、未匹配字幕等）移动到目标根目录，
        保留相对于源目录的子目录结构。
        """
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
            if should_skip_path(root):
                continue
            for name in files:
                src = os.path.join(root, name)
                if Path(src).suffix.lower() in settings.VIDEO_EXTENSIONS:
                    continue  # 视频已由主流程处理
                if is_reason_file(src):
                    log.info(f"跳过历史原因文件: {src}")
                    continue
                rel = os.path.relpath(src, directory)
                dst = os.path.join(target_root, rel)
                try:
                    Path(dst).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, dst)
                    log.info(f"移动附属文件: {src} -> {dst}")
                except Exception as e:
                    log.warning(f"移动附属文件失败 {src}: {e}")

    def cleanup_empty_dirs(self, directory: str):
        """清理目录树中的空目录（包括根目录自身）。
        os.rmdir 只在目录为空时成功，无需额外判断。
        """
        for root, _dirs, _files in os.walk(directory, topdown=False):
            try:
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
