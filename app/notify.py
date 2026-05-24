"""Telegram 通知 - 复用你已有的 bot"""
import logging
import requests
from config import settings

log = logging.getLogger("notify")


def notify(message: str):
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        log.debug(f"通知未配置,仅日志: {message}")
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_notification": True,  # 静默
            },
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram 通知失败: {e}")
