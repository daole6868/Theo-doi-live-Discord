"""
Cấu hình logging tập trung.

Dùng thay cho print() để có:
- Level (DEBUG/INFO/WARNING/ERROR)
- Timestamp
- Ghi ra file (rotate mỗi 5MB, giữ 5 file cũ)
- Vẫn hiện trên terminal
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import LOG_DIR


def setup_logging(level: int = logging.INFO) -> None:
    """Gọi 1 lần khi bot khởi động."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # 1. Console handler (stdout)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)

    # 2. File handler (rotate 5MB × 5 files)
    fh = RotatingFileHandler(
        LOG_DIR / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Reset handler cũ (tránh duplicate khi hot-reload)
    root.handlers.clear()
    root.addHandler(ch)
    root.addHandler(fh)

    # Bớt spam log từ discord.py internal
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
