"""
Time utilities — dùng thay cho `datetime.datetime.now()` naive.

Lý do: bản cũ mix naive datetime với tz-aware task loop → dễ lệch giờ khi VPS
không đặt timezone VN. Ở đây chuẩn hóa: mọi datetime hiển thị đều theo VN.

Ghi chú compat: các session cũ lưu timestamp bằng datetime.now().timestamp()
vẫn đọc được vì .timestamp() luôn ra UTC epoch (bất kể tz của datetime).
"""
import datetime

from config import VN_TZ


def now_vn() -> datetime.datetime:
    """Datetime hiện tại theo giờ VN (aware)."""
    return datetime.datetime.now(VN_TZ)


def now_ts() -> float:
    """Timestamp hiện tại (UTC epoch, an toàn để so sánh)."""
    return now_vn().timestamp()


def from_ts(ts: float) -> datetime.datetime:
    """Convert UTC timestamp sang datetime giờ VN (aware)."""
    return datetime.datetime.fromtimestamp(ts, tz=VN_TZ)


def fmt_dur(seconds: float | int) -> str:
    """Format số giây → 'H:MM:SS'."""
    return str(datetime.timedelta(seconds=int(seconds)))


def fmt_hm(dt: datetime.datetime) -> str:
    """Format datetime → 'HH:MM' theo giờ VN."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ).strftime("%H:%M")


def fmt_full(dt: datetime.datetime) -> str:
    """Format datetime → 'HH:MM DD/MM/YYYY' theo giờ VN."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y")


def start_of_day_vn(dt: datetime.datetime | None = None) -> datetime.datetime:
    """Trả về 00:00:00 của ngày `dt` theo giờ VN (default: hôm nay)."""
    d = dt or now_vn()
    if d.tzinfo is None:
        d = d.replace(tzinfo=VN_TZ)
    d = d.astimezone(VN_TZ)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)
