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


def day_bounds_ts(day: datetime.date) -> tuple[float, float]:
    """(00:00, 24:00) của 1 ngày giờ VN dưới dạng timestamp."""
    start = datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=VN_TZ)
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def split_by_day(start_ts: float, end_ts: float) -> list[tuple[float, float, datetime.date]]:
    """
    Cắt khoảng [start_ts, end_ts) tại các mốc 00:00 giờ VN.

    Ví dụ 23:00 → 01:00 trả về 2 đoạn: (23:00, 24:00, hôm qua) và (00:00, 01:00, hôm nay).
    """
    pieces: list[tuple[float, float, datetime.date]] = []
    if end_ts <= start_ts:
        return pieces
    cur = start_ts
    while cur < end_ts:
        day = from_ts(cur).date()
        _, day_end = day_bounds_ts(day)
        piece_end = min(end_ts, day_end)
        pieces.append((cur, piece_end, day))
        cur = piece_end
    return pieces
