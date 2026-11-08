"""
Cấu hình tập trung cho toàn bot.

Đọc secret từ file `.env` (dùng python-dotenv).
Các ID kênh cũng đọc từ .env để dễ đổi khi migrate server mà không phải sửa code.
"""
import datetime
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# =========================================================
#  BASE PATHS
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
# Cho phép đổi thư mục qua biến môi trường (dùng khi chạy test)
DATA_DIR = Path(os.getenv("GACHAZ_DATA_DIR", BASE_DIR / "data"))
LOG_DIR = Path(os.getenv("GACHAZ_LOG_DIR", BASE_DIR / "logs"))

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


# =========================================================
#  DISCORD SECRETS & IDS
# =========================================================
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "❌ Thiếu DISCORD_TOKEN trong .env. "
        "Copy .env.example thành .env và điền token vào."
    )


def _env_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key, str(default)).strip()
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


GUILD_ID = _env_int("GUILD_ID")
TARGET_CATEGORY_ID = _env_int("TARGET_CATEGORY_ID")
ADMIN_CHANNEL_ID = _env_int("ADMIN_CHANNEL_ID")
USER_CHANNEL_ID = _env_int("USER_CHANNEL_ID")
LEADERBOARD_CHANNEL_ID = _env_int("LEADERBOARD_CHANNEL_ID")
LATE_ALERT_CHANNEL_ID = _env_int("LATE_ALERT_CHANNEL_ID")
CHECK_CTV_CHANNEL_ID = _env_int("CHECK_CTV_CHANNEL_ID")


def is_main_guild(guild) -> bool:
    """True nếu `guild` (object hoặc id) là server bot quản lý.

    GUILD_ID = 0 → không lọc (chấp nhận mọi server).
    """
    if not GUILD_ID:
        return True
    gid = getattr(guild, "id", guild)
    try:
        return gid is not None and int(gid) == GUILD_ID
    except (TypeError, ValueError):
        return False


# =========================================================
#  TIME
# =========================================================
VN_TZ = datetime.timezone(datetime.timedelta(hours=7), name="Asia/Ho_Chi_Minh")


# =========================================================
#  DATA FILES
# =========================================================
DATA_FILES = {
    "channels":       DATA_DIR / "data_channels.json",
    "ctv":            DATA_DIR / "data_ctv.json",
    "subscribers":    DATA_DIR / "data_subs.json",
    "embeds":         DATA_DIR / "data_embeds.json",
    "next_live":      DATA_DIR / "data_next_live.json",
    "live_state":     DATA_DIR / "data_live_state.json",
    "panel_messages": DATA_DIR / "data_panel_msgs.json",  # dùng cho auto-refresh (batch 4C)
    "voice_settings": DATA_DIR / "data_voice_settings.json",  # cài đặt voice alert (batch 5)
    "voice_alerts":   DATA_DIR / "data_voice_alerts.json",    # state CTV đang thoại chưa live (batch 5)
}

# Lịch sử phiên (live + audio) lưu trong SQLite — giữ vĩnh viễn.
# File JSON cũ `data_sessions.json` được chuyển sang đây tự động ở lần chạy đầu.
SESSIONS_DB_FILE = DATA_DIR / "sessions.db"
LEGACY_SESSIONS_JSON = DATA_DIR / "data_sessions.json"


# =========================================================
#  BUSINESS RULES
# =========================================================
LATE_REMINDER_MAX = 6
REMINDER_INTERVAL_MINUTES = 5
MIN_SESSION_SECONDS = 5

# Bộ tính giờ
HEARTBEAT_SECONDS = 30            # Bot ghi "vẫn đang chạy" mỗi 30 giây
RECONCILE_MINUTES = 5             # Định kỳ đối chiếu trạng thái với Discord
LEGACY_LIVE_MAX_HOURS = 24        # Lần đầu nâng cấp: chỉ giữ phiên cũ nếu bắt đầu < 24h trước

# Thông báo & nhắc lịch
NOTIFY_COOLDOWN_MINUTES = 30            # Mỗi CTV báo live cho khách tối đa 1 lần / 30 phút
NEXT_LIVE_PROMPT_DELAY_SECONDS = 120    # Tắt live 2 phút (không bật lại) mới hỏi giờ live tiếp theo

# Voice Alert
VOICE_ALERT_REJOIN_GRACE_MINUTES = 5    # Rời rồi vào lại trong 5 phút → giữ đồng hồ cũ

# Loại phiên
KIND_STREAM = "stream"            # Bật Go Live (chia sẻ màn hình) — chỉ loại này tính là LIVE
KIND_AUDIO = "audio"              # Ở trong kênh voice nhưng không chia sẻ màn hình

DELAY_OPTIONS = [
    ("Hoãn 5 phút",   5 * 60),
    ("Hoãn 30 phút", 30 * 60),
    ("Hoãn 1 tiếng", 60 * 60),
]

CHECK_RANGE_OPTIONS = [
    ("Hôm nay",   1),
    ("7 ngày",    7),
    ("15 ngày",  15),
]


# =========================================================
#  EMBED KEYS
# =========================================================
EMBED_MAIN_SELECT       = "embed_main_select"
EMBED_CUSTOMER_LIVE     = "embed_customer_live"      # MỚI: hiện khi CTV đang LIVE
EMBED_CUSTOMER_OFFLINE  = "embed_customer_offline"   # MỚI: hiện khi kênh OFF
EMBED_CTV_INFO          = "embed_ctv_info"
EMBED_LIVE_NOTIF        = "embed_live_notif"
EMBED_CTV_NEXT_LIVE     = "embed_ctv_next_live"
EMBED_CTV_REMINDER      = "embed_ctv_reminder"
EMBED_CHECK_CTV_PANEL   = "embed_check_ctv_panel"

# Legacy — chỉ dùng cho migration, không nên dùng mới
EMBED_CUSTOMER_INFO     = "embed_customer_info"
