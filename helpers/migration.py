"""
Migration script — chạy 1 lần khi bot khởi động.

Xử lý các thay đổi data structure giữa các version của bot.
Idempotent — chạy nhiều lần không sao.
"""
import json
import logging

from config import KIND_STREAM, LEGACY_SESSIONS_JSON, LEGACY_SUBS_JSON
from core.data_manager import db
from core.session_store import store

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


def migrate_sessions_to_sqlite():
    """
    Chuyển lịch sử phiên từ `data_sessions.json` (key theo kênh) sang SQLite.

    Chạy 1 lần: đánh dấu trong bảng meta và đổi tên file JSON thành `.migrated`
    (giữ lại để đối chiếu nếu cần).
    """
    if store.get_meta("migrated_sessions_json") == "1":
        return
    path = LEGACY_SESSIONS_JSON
    if not path.exists():
        store.set_meta("migrated_sessions_json", "1")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        log.error(f"data_sessions.json lỗi định dạng ({e}) — bỏ qua migrate, giữ nguyên file")
        return

    imported = skipped = 0
    for cid, lst in (data.items() if isinstance(data, dict) else []):
        if not isinstance(lst, list):
            continue
        for s in lst:
            if not isinstance(s, dict):
                continue
            uid = str(s.get("user_id", "")).strip()
            ts_start = s.get("start_time", s.get("start"))
            ts_end = s.get("end_time", s.get("end"))
            if not uid or not ts_start or not ts_end:
                skipped += 1  # Phiên rất cũ không có user_id → không biết của ai
                continue
            saved = store.add_session(
                uid, str(cid), KIND_STREAM, float(ts_start), float(ts_end), min_seconds=0,
            )
            if saved:
                imported += 1
            else:
                skipped += 1

    store.set_meta("migrated_sessions_json", "1")
    try:
        path.rename(path.with_name(path.name + ".migrated"))
    except OSError:
        log.exception("Đổi tên data_sessions.json lỗi (dữ liệu đã chuyển xong)")
    log.info(f"✓ Migrate: chuyển {imported} phiên sang SQLite (bỏ qua {skipped})")


def migrate_next_live_per_user():
    """
    Lịch live tiếp theo: đổi key từ kênh sang từng CTV.

    Cũ:  {channel_id: {"time", "time_str", "user_id", "reminders_sent"}}
    Mới: {user_id:    {"time", "time_str", "channel_id", "reminders_sent"}}
    Nếu 1 CTV có nhiều lịch (nhiều kênh) → giữ lịch sớm nhất.
    """
    data = db.load("next_live")
    if not any(isinstance(v, dict) and "user_id" in v for v in data.values()):
        return

    new: dict[str, dict] = {}
    for key, info in data.items():
        if not isinstance(info, dict):
            continue
        if "user_id" not in info:
            new[str(key)] = info  # đã là định dạng mới
            continue
        uid = str(info.get("user_id", "")).strip()
        if not uid or not info.get("time"):
            continue
        entry = {
            "time": info["time"],
            "time_str": info.get("time_str", "?"),
            "channel_id": str(key),
            "reminders_sent": int(info.get("reminders_sent", 0)),
        }
        if uid not in new or entry["time"] < new[uid]["time"]:
            new[uid] = entry

    db.save("next_live", new)
    log.info(f"✓ Migrate: chuyển {len(new)} lịch live tiếp theo sang key theo CTV")


def migrate_subscribers_to_ctv():
    """
    Đăng ký thông báo: đổi từ theo KÊNH sang theo CTV.

    Cũ:  data_subs.json      {channel_id: [khách]}
    Mới: data_ctv_subs.json  {ctv_id: [khách]}
    Khách đã bật thông báo ở kênh X → theo dõi các CTV chủ kênh X.
    Kênh không có CTV chủ thì trước đây cũng không bao giờ gửi thông báo → bỏ.
    """
    if store.get_meta("migrated_subs_by_ctv") == "1":
        return
    path = LEGACY_SUBS_JSON
    if not path.exists():
        store.set_meta("migrated_subs_by_ctv", "1")
        return
    try:
        old = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        log.error(f"data_subs.json lỗi định dạng ({e}) — bỏ qua migrate, giữ nguyên file")
        return

    ctv_data = db.load("ctv")
    owners_of: dict[str, list[str]] = {}
    for uid, chans in ctv_data.items():
        if isinstance(chans, list):
            for c in chans:
                owners_of.setdefault(str(c), []).append(str(uid))

    new = db.load("ctv_subscribers")
    moved = dropped = 0
    for cid, customers in (old.items() if isinstance(old, dict) else []):
        if not isinstance(customers, list):
            continue
        owners = owners_of.get(str(cid), [])
        if not owners:
            dropped += len(customers)
            continue
        for owner in owners:
            lst = new.setdefault(owner, [])
            for cust in customers:
                if str(cust) not in [str(x) for x in lst]:
                    lst.append(str(cust))
                    moved += 1

    db.save("ctv_subscribers", new)
    store.set_meta("migrated_subs_by_ctv", "1")
    try:
        path.rename(path.with_name(path.name + ".migrated"))
    except OSError:
        log.exception("Đổi tên data_subs.json lỗi (dữ liệu đã chuyển xong)")
    log.info(
        f"✓ Migrate: chuyển đăng ký thông báo sang theo CTV — {moved} lượt theo dõi "
        f"(bỏ {dropped} đăng ký ở kênh không có CTV)"
    )


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
    try:
        migrate_sessions_to_sqlite()
    except Exception:
        log.exception("migrate_sessions_to_sqlite lỗi")
    try:
        migrate_next_live_per_user()
    except Exception:
        log.exception("migrate_next_live_per_user lỗi")
    try:
        migrate_subscribers_to_ctv()
    except Exception:
        log.exception("migrate_subscribers_to_ctv lỗi")
    log.info("Migrations xong.")
    log.info("=" * 40)
