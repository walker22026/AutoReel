"""
Media Organizer - 自动整理 NAS 上的电影/剧集文件
- 监控下载目录
- 规则 + TMDB + LLM 三级识别
- 移动/硬链接/复制到媒体库,标准命名
"""
import os
import sys
import time
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from config import settings
from organizer import MediaOrganizer

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


class DownloadHandler(FileSystemEventHandler):
    """监听下载目录的新文件"""

    def __init__(self, organizer: MediaOrganizer):
        self.organizer = organizer
        self.pending = {}  # 防抖:文件可能还在写入

    def on_created(self, event):
        if event.is_directory:
            return
        self._queue(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._queue(event.dest_path)

    def _queue(self, path):
        if not self._is_video(path):
            return
        # 等文件写完再处理(5 秒静默期)
        self.pending[path] = time.time()

    def _is_video(self, path):
        return Path(path).suffix.lower() in settings.VIDEO_EXTENSIONS

    def flush(self):
        """处理静默期已过的文件"""
        now = time.time()
        ready = [p for p, t in self.pending.items() if now - t >= 5]
        for path in ready:
            del self.pending[path]
            try:
                self.organizer.process(path)
            except Exception as e:
                log.exception(f"处理失败 {path}: {e}")


def scan_existing(organizer: MediaOrganizer):
    """启动时扫描存量文件"""
    log.info(f"扫描存量目录: {settings.WATCH_DIR}")
    count = 0
    for root, dirs, files in os.walk(settings.WATCH_DIR):
        for f in files:
            if Path(f).suffix.lower() in settings.VIDEO_EXTENSIONS:
                path = os.path.join(root, f)
                try:
                    organizer.process(path)
                    count += 1
                except Exception as e:
                    log.exception(f"存量处理失败 {path}: {e}")
    log.info(f"存量扫描完成,处理 {count} 个文件")


def main():
    log.info("Media Organizer 启动")
    log.info(f"监控目录: {settings.WATCH_DIR}")
    log.info(f"输出根目录: {settings.OUTPUT_ROOT}")

    organizer = MediaOrganizer()

    # 启动时扫描一次
    if settings.SCAN_ON_START:
        scan_existing(organizer)

    # 持续监控
    handler = DownloadHandler(organizer)
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
