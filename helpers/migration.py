"""
Migration script — chạy 1 lần khi bot khởi động.

Xử lý các thay đổi data structure giữa các version của bot.
Idempotent — chạy nhiều lần không sao.
"""
import logging

from core.data_manager import db

log = logging.getLogger(__name__)


DEFAULT_VOICE_SETTINGS = {
    "enabled": True,
    "message": (
        "Xin chào bạn, bạn đã vào kênh voice nhưng chưa bật Go Live. "
        "Vui lòng bật Go Live để bắt đầu phiên livestream."
    ),
    "delay_before_alert_minutes": 15,
    "alert_window_minutes": 15,
    "alert_count": 5,
    "log_channel_id": 0,
    "voice": "vi-VN-HoaiMyNeural",  # giọng nữ mặc định
    "alert_volume": 1.0,             # Âm lượng voice alert (0.0-2.0, 1.0=100%)
    # Broadcast (nút "Gửi Voice" ở admin panel)
    "broadcast_message": "Xin chào các bạn, đây là thông báo từ Admin.",
    "broadcast_volume": 1.0,
}


def migrate_embeds():
    """
    Tách `embed_customer_info` cũ thành `embed_customer_live` + `embed_customer_offline`.
    """
    embeds = db.load("embeds")
    changed = False

    old = embeds.get("embed_customer_info")
    if old:
        if "embed_customer_live" not in embeds:
            embeds["embed_customer_live"] = dict(old)
            log.info("✓ Migrate: tạo embed_customer_live từ embed_customer_info")
            changed = True
        if "embed_customer_offline" not in embeds:
            embeds["embed_customer_offline"] = dict(old)
            log.info("✓ Migrate: tạo embed_customer_offline từ embed_customer_info")
            changed = True

    if changed:
        db.save("embeds", embeds)


def migrate_voice_settings():
    """
    Thêm default settings cho voice alert nếu chưa có (batch 5).
    """
    settings = db.load("voice_settings")
    changed = False

    for k, v in DEFAULT_VOICE_SETTINGS.items():
        if k not in settings:
            settings[k] = v
            log.info(f"✓ Migrate: set default voice_settings[{k}]")
            changed = True

    if changed:
        db.save("voice_settings", settings)


def run_all_migrations():
    """Gọi trong setup_hook của main.py."""
    log.info("=" * 40)
    log.info("Chạy migrations...")
    try:
        migrate_embeds()
    except Exception:
        log.exception("migrate_embeds lỗi")
    try:
        migrate_voice_settings()
    except Exception:
        log.exception("migrate_voice_settings lỗi")
    log.info("Migrations xong.")
    log.info("=" * 40)
