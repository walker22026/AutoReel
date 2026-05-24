"""配置加载 - 从 JSON 配置文件读取,环境变量仅用于指定配置文件路径。"""
import json
import os
from pathlib import Path


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


CONFIG_FILE = os.getenv("CONFIG_FILE", "/config/settings.json")
APP_CONFIG = load_config(CONFIG_FILE)


def get_config(key: str, default: str = ""):
    value = APP_CONFIG.get(key)
    if value not in (None, ""):
        return value
    return default


def get_bool(key: str, default: bool = False) -> bool:
    return str(get_config(key, str(default))).lower() in {"1", "true", "yes", "on"}


def get_int(key: str, default: int) -> int:
    try:
        return int(get_config(key, str(default)))
    except (TypeError, ValueError):
        return default


class Settings:
    CONFIG_FILE = CONFIG_FILE
    APP_CONFIG = APP_CONFIG

    # 路径配置
    WATCH_DIR = get_config("WATCH_DIR", "/host/emby/source")
    MOVIE_DIR = get_config("MOVIE_DIR", "/host/emby/电影")
    TV_DIR = get_config("TV_DIR", "/host/emby/剧集")
    UNRECOGNIZED_DIR_NAME = get_config("UNRECOGNIZED_DIR_NAME", "_unrecognized")
    UNRECOGNIZED_DIR = get_config("UNRECOGNIZED_DIR", "")

    # TMDB
    TMDB_API_KEY = get_config("TMDB_API_KEY", "")
    TMDB_LANG = get_config("TMDB_LANG", "zh-CN")

    # 行为
    DRY_RUN = get_bool("DRY_RUN", True)
    SCAN_ON_START = get_bool("SCAN_ON_START", True)
    QUIET_SECONDS = get_int("QUIET_SECONDS", 10)
    # 文件处理模式:
    # - move: 移动/重命名源文件到媒体库
    # - hardlink: 保留下载源文件,在媒体库创建硬链接
    # - copy: 复制一份到媒体库
    FILE_ACTION = str(get_config("FILE_ACTION", "")).lower().strip()
    if not FILE_ACTION:
        FILE_ACTION = "move"
    if FILE_ACTION not in {"move", "hardlink", "copy"}:
        FILE_ACTION = "move"
    USE_HARDLINK = FILE_ACTION == "hardlink"
    MIN_FILE_SIZE_MB = get_int("MIN_FILE_SIZE_MB", 100)  # 过滤样片

    # 视频后缀
    VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".rmvb", ".webm"}

    # 字幕后缀(随主文件一起搬)
    SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}

    # 状态记录
    STATE_DB = os.getenv("STATE_DB", "/config/state.db")

    # 日志
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 通知 (复用你已有的 Telegram bot)
    TELEGRAM_BOT_TOKEN = get_config("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = get_config("TELEGRAM_CHAT_ID", "")

    @property
    def unrecognized_dir(self) -> str:
        if self.UNRECOGNIZED_DIR:
            return self.UNRECOGNIZED_DIR
        return str(Path(self.WATCH_DIR) / self.UNRECOGNIZED_DIR_NAME)


settings = Settings()
