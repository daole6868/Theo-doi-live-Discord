"""
Quản lý state runtime: danh sách kênh đang LIVE.

Cải tiến so với bản cũ (biến `current_lives = {}` trong RAM):
1. Persist xuống file → bot restart không mất phiên đang live.
2. Nhất quán key kiểu str (JSON keys luôn là str).
3. Lưu thêm user_id của CTV đang stream → biết ai cày phiên nào (cho BXH cá nhân).
4. Datetime tz-aware (giờ VN) → không lỗi khi trừ với now_vn().
5. API rõ ràng thay cho thao tác dict thô.
"""
import datetime
import logging

from config import VN_TZ
from core.data_manager import db

log = logging.getLogger(__name__)


class LiveState:
    """
    Structure: {channel_id (str): {"start_ts": float, "user_id": str}}

    - start_ts: UTC timestamp (từ dt.timestamp(), bất kể tz)
    - user_id: ID của CTV đang stream (str)
    """

    def __init__(self):
        self._data: dict[str, dict] = {}

    # ---------- INIT / PERSIST ----------
    def load_from_disk(self) -> None:
        """Gọi trong on_ready — restore session đang live sau khi bot restart."""
        raw = db.load("live_state")
        cleaned: dict[str, dict] = {}
        for cid, info in raw.items():
            if isinstance(info, dict) and "start_ts" in info:
                cleaned[str(cid)] = {
                    "start_ts": float(info["start_ts"]),
                    "user_id": str(info.get("user_id", "")),
                }
        self._data = cleaned
        if cleaned:
            log.info(f"Restore {len(cleaned)} phiên live đang chạy từ disk.")

    def _persist(self) -> None:
        db.save("live_state", self._data)

    # ---------- QUERY ----------
    def is_live(self, channel_id) -> bool:
        return str(channel_id) in self._data

    def get_start(self, channel_id) -> datetime.datetime | None:
        """Trả datetime giờ VN (aware)."""
        info = self._data.get(str(channel_id))
        return datetime.datetime.fromtimestamp(info["start_ts"], tz=VN_TZ) if info else None

    def get_user(self, channel_id) -> str | None:
        info = self._data.get(str(channel_id))
        return info["user_id"] if info else None

    def all_channel_ids(self) -> list[str]:
        return list(self._data.keys())

    def snapshot(self) -> dict[str, dict]:
        """Copy toàn bộ state — dùng cho task quét (tránh mutate khi iter)."""
        return {cid: dict(info) for cid, info in self._data.items()}

    # ---------- MUTATE ----------
    def start(self, channel_id, user_id, when: datetime.datetime | None = None) -> None:
        if when is None:
            when = datetime.datetime.now(VN_TZ)
        elif when.tzinfo is None:
            # Naive → assume VN
            when = when.replace(tzinfo=VN_TZ)
        self._data[str(channel_id)] = {
            "start_ts": when.timestamp(),
            "user_id": str(user_id),
        }
        self._persist()

    def stop(self, channel_id) -> dict | None:
        """Pop session ra khỏi state, return info (dict) hoặc None."""
        info = self._data.pop(str(channel_id), None)
        if info is not None:
            self._persist()
        return info

    def reset_start(self, channel_id, when: datetime.datetime) -> None:
        """Đổi mốc bắt đầu — dùng cho midnight cutoff (cắt session qua ngày)."""
        cid = str(channel_id)
        if cid in self._data:
            if when.tzinfo is None:
                when = when.replace(tzinfo=VN_TZ)
            self._data[cid]["start_ts"] = when.timestamp()
            self._persist()


# Singleton
state = LiveState()
