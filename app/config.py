"""配置加载 - Web 配置文件优先,环境变量兜底"""
import json
import os
from pathlib import Path


def load_web_config(path: str) -> dict:
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
WEB_CONFIG = load_web_config(CONFIG_FILE)


def get_config(key: str, default: str = ""):
    value = WEB_CONFIG.get(key)
    if value not in (None, ""):
        return value
    return os.getenv(key, default)


class Settings:
    CONFIG_FILE = CONFIG_FILE
    WEB_CONFIG = WEB_CONFIG

    # 路径配置
    WATCH_DIR = get_config("WATCH_DIR", "/host/volume1/downloads")
    OUTPUT_ROOT = get_config("OUTPUT_ROOT", "/host/volume1/media")
    MOVIE_DIR_NAME = get_config("MOVIE_DIR_NAME", "Movies")
    TV_DIR_NAME = get_config("TV_DIR_NAME", "TV")

    # TMDB
    TMDB_API_KEY = get_config("TMDB_API_KEY", "")
    TMDB_LANG = get_config("TMDB_LANG", "zh-CN")

    # LLM (LiteLLM gateway). Web 配置文件优先,环境变量兜底。
    LITELLM_BASE = get_config("LITELLM_BASE", "http://litellm:4000")
    LITELLM_KEY = get_config("LITELLM_KEY", "")
    LITELLM_MODEL = get_config("LITELLM_MODEL", "gemini-flash")

    # 行为
    DRY_RUN = str(get_config("DRY_RUN", "true")).lower() == "true"
    SCAN_ON_START = str(get_config("SCAN_ON_START", "false")).lower() == "true"
    # 文件处理模式:
    # - move: 移动/重命名源文件到媒体库
    # - hardlink: 保留下载源文件,在媒体库创建硬链接
    # - copy: 复制一份到媒体库
    FILE_ACTION = str(get_config("FILE_ACTION", "")).lower().strip()
    if not FILE_ACTION:
        FILE_ACTION = os.getenv("FILE_ACTION", "move").lower().strip()
    if FILE_ACTION not in {"move", "hardlink", "copy"}:
        FILE_ACTION = "move"
    USE_HARDLINK = FILE_ACTION == "hardlink"
    MIN_FILE_SIZE_MB = int(get_config("MIN_FILE_SIZE_MB", "100"))  # 过滤样片

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


settings = Settings()
