"""配置加载 - 从 docker-compose.yml 的 environment 读取。"""
import os
from pathlib import Path


def get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in {"1", "true", "yes", "on"}


def get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


class Settings:
    # 路径配置
    WATCH_DIR = os.getenv("WATCH_DIR", "/host/emby/source")
    MOVIE_DIR = os.getenv("MOVIE_DIR", "/host/emby/电影")
    TV_DIR = os.getenv("TV_DIR", "/host/emby/剧集")
    UNRECOGNIZED_DIR_NAME = os.getenv("UNRECOGNIZED_DIR_NAME", "_unrecognized")
    UNRECOGNIZED_DIR = os.getenv("UNRECOGNIZED_DIR", "")
    DUPLICATE_DIR_NAME = os.getenv("DUPLICATE_DIR_NAME", "_duplicates")
    PENDING_DELETE_DIR_NAME = os.getenv("PENDING_DELETE_DIR_NAME", "_pending_delete")
    PENDING_DELETE_DIR = os.getenv("PENDING_DELETE_DIR", "")

    # TMDB
    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
    TMDB_LANG = os.getenv("TMDB_LANG", "zh-CN")

    # 行为
    DRY_RUN = get_bool("DRY_RUN", True)
    SCAN_ON_START = get_bool("SCAN_ON_START", True)
    QUIET_SECONDS = get_int("QUIET_SECONDS", 10)
    FILE_ACTION = os.getenv("FILE_ACTION", "move").lower().strip()
    if FILE_ACTION not in {"move", "hardlink", "copy"}:
        FILE_ACTION = "move"
    USE_HARDLINK = FILE_ACTION == "hardlink"
    MIN_FILE_SIZE_MB = get_int("MIN_FILE_SIZE_MB", 100)

    # 视频后缀
    VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".rmvb", ".webm"}

    # 字幕后缀(随主文件一起搬)
    SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}

    # 状态记录
    STATE_DB = os.getenv("STATE_DB", "/config/state.db")

    # 别名表（用户自定义，TMDB 搜不到时优先使用）
    ALIASES_FILE = os.getenv("ALIASES_FILE", "/config/aliases.json")

    # 日志
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 通知
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # LLM 兜底（可选）
    LLM_API_URL = os.getenv("LLM_API_URL", "")          # OpenAI 兼容接口，空则禁用
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_TIMEOUT = get_int("LLM_TIMEOUT", 15)            # 单次请求超时秒数

    @property
    def unrecognized_dir(self) -> str:
        if self.UNRECOGNIZED_DIR:
            return self.UNRECOGNIZED_DIR
        return str(Path(self.WATCH_DIR) / self.UNRECOGNIZED_DIR_NAME)

    @property
    def duplicate_dir(self) -> str:
        return str(Path(self.unrecognized_dir) / self.DUPLICATE_DIR_NAME)

    @property
    def pending_delete_dir(self) -> str:
        if self.PENDING_DELETE_DIR:
            return self.PENDING_DELETE_DIR
        return str(Path(self.WATCH_DIR) / self.PENDING_DELETE_DIR_NAME)


settings = Settings()
