"""
AutoReel - NAS 影视文件自动整理
- 从 docker-compose.yml 的 environment 读取配置
- 启动后扫描输入目录
- 持续监听输入目录
- 根目录单文件: 识别成功后整理,失败移入未识别目录
- 根目录子目录: 作为一个批次,全部视频识别成功才整理,否则整个目录移入未识别目录
"""
import logging
import os
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import settings
from organizer import MediaOrganizer

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


class InputHandler(FileSystemEventHandler):
    """监听输入目录,把根目录文件和根目录子目录作为事件处理。"""

    def __init__(self, organizer: MediaOrganizer):
        self.organizer = organizer
        self.pending = {}
        self.watch_dir = Path(settings.WATCH_DIR).resolve()
        self.unrecognized_dir = Path(settings.unrecognized_dir).resolve()

    def on_created(self, event):
        self.queue(event.src_path)

    def on_modified(self, event):
        self.queue(event.src_path)

    def on_moved(self, event):
        self.queue(event.dest_path)

    def queue(self, path: str):
        target = self.event_target(path)
        if not target:
            return
        self.pending[str(target)] = time.time()

    def event_target(self, path: str) -> Path | None:
        path_obj = Path(path)
        try:
            resolved = path_obj.resolve()
            rel = resolved.relative_to(self.watch_dir)
        except (OSError, ValueError):
            return None

        if self.is_in_unrecognized(resolved):
            return None

        parts = rel.parts
        if not parts:
            return None

        first = self.watch_dir / parts[0]
        if first.resolve() == self.unrecognized_dir:
            return None

        if first.is_dir():
            return first

        if first.is_file() and first.suffix.lower() in settings.VIDEO_EXTENSIONS:
            return first

        return None

    def is_in_unrecognized(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.unrecognized_dir)
            return True
        except (OSError, ValueError):
            return False

    def flush(self):
        now = time.time()
        ready = [p for p, t in self.pending.items() if now - t >= settings.QUIET_SECONDS]
        for path in ready:
            del self.pending[path]
            self.process(path)

    def process(self, path: str):
        path_obj = Path(path)
        if self.is_in_unrecognized(path_obj):
            return
        if path_obj.is_dir():
            log.info(f"处理目录批次: {path}")
            self.organizer.process_directory(path)
        elif path_obj.is_file() and path_obj.suffix.lower() in settings.VIDEO_EXTENSIONS:
            log.info(f"处理单文件: {path}")
            self.organizer.process(path)


def ensure_dirs():
    Path(settings.WATCH_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.MOVIE_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.TV_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.unrecognized_dir).mkdir(parents=True, exist_ok=True)


def scan_existing(handler: InputHandler):
    """启动时扫描输入目录的第一层:根目录视频文件 + 根目录子目录。"""
    log.info(f"扫描输入目录: {settings.WATCH_DIR}")
    watch = Path(settings.WATCH_DIR)
    for item in sorted(watch.iterdir(), key=lambda p: p.name):
        try:
            if item.resolve() == Path(settings.unrecognized_dir).resolve():
                continue
        except OSError:
            continue

        if item.is_dir():
            handler.process(str(item))
        elif item.is_file() and item.suffix.lower() in settings.VIDEO_EXTENSIONS:
            handler.process(str(item))
    log.info("输入目录扫描完成")


def main():
    log.info("AutoReel 启动")
    log.info(f"输入目录: {settings.WATCH_DIR}")
    log.info(f"电影目录: {settings.MOVIE_DIR}")
    log.info(f"剧集目录: {settings.TV_DIR}")
    log.info(f"未识别目录: {settings.unrecognized_dir}")
    log.info(f"文件动作: {settings.FILE_ACTION}, Dry Run: {settings.DRY_RUN}")

    ensure_dirs()
    organizer = MediaOrganizer()
    handler = InputHandler(organizer)

    if settings.SCAN_ON_START:
        scan_existing(handler)

    observer = Observer()
    observer.schedule(handler, settings.WATCH_DIR, recursive=True)
    observer.start()
    log.info("监控已启动")

    try:
        while True:
            time.sleep(2)
            handler.flush()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
