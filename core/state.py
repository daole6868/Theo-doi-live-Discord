"""
Trạng thái runtime: mỗi CTV đang ở trong voice có đúng 1 "đoạn" đang chạy.

Structure: {user_id (str): {"kind": "stream"|"audio", "channel_id": str, "start_ts": float}}

- kind = "stream": đang bật Go Live (chia sẻ màn hình) → tính là LIVE.
- kind = "audio":  ở trong kênh voice nhưng không chia sẻ màn hình.

Khác bản cũ (key theo kênh → mỗi kênh chỉ tính 1 người):
key theo từng CTV, nên nhiều CTV live chung 1 kênh đều được tính riêng,
và ai tắt Go Live thì chỉ đoạn của người đó bị chốt.

State được ghi xuống file sau mỗi thay đổi để bot restart không mất.
"""
import datetime
import logging

from config import KIND_STREAM, VN_TZ
from core.data_manager import db

log = logging.getLogger(__name__)


class LiveState:
    def __init__(self):
        self._data: dict[str, dict] = {}

    # ---------- INIT / PERSIST ----------
    def load_from_disk(self) -> None:
        """Đọc state từ file. Tự chuyển định dạng cũ (key theo kênh) sang mới."""
        raw = db.load("live_state")
        cleaned: dict[str, dict] = {}
        legacy = 0
        for key, info in raw.items():
            if not isinstance(info, dict) or "start_ts" not in info:
                continue
            if "kind" in info and "channel_id" in info:
                cleaned[str(key)] = {
                    "kind": str(info["kind"]),
                    "channel_id": str(info["channel_id"]),
                    "start_ts": float(info["start_ts"]),
                }
            else:
                # Định dạng cũ: {channel_id: {"start_ts", "user_id"}} — chỉ có stream
                uid = str(info.get("user_id", "")).strip()
                if not uid:
                    continue
                cleaned[uid] = {
                    "kind": KIND_STREAM,
                    "channel_id": str(key),
                    "start_ts": float(info["start_ts"]),
                }
                legacy += 1
        self._data = cleaned
        if legacy:
            log.info(f"Chuyển {legacy} phiên live định dạng cũ sang định dạng theo CTV.")
            self._persist()
        if cleaned:
            log.info(f"Đọc {len(cleaned)} đoạn đang chạy từ disk.")

    def _persist(self) -> None:
        db.save("live_state", self._data)

    # ---------- QUERY ----------
    def get(self, user_id) -> dict | None:
        info = self._data.get(str(user_id))
        return dict(info) if info else None

    def user_ids(self) -> list[str]:
        return list(self._data.keys())

    def snapshot(self) -> dict[str, dict]:
        return {uid: dict(info) for uid, info in self._data.items()}

    def is_streaming(self, user_id) -> bool:
        info = self._data.get(str(user_id))
        return bool(info and info["kind"] == KIND_STREAM)

    def streams(self) -> dict[str, dict]:
        """Tất cả CTV đang LIVE: {user_id: info}."""
        return {uid: dict(i) for uid, i in self._data.items() if i["kind"] == KIND_STREAM}

    def streams_in_channel(self, channel_id) -> dict[str, dict]:
        cid = str(channel_id)
        return {
            uid: dict(i) for uid, i in self._data.items()
            if i["kind"] == KIND_STREAM and i["channel_id"] == cid
        }

    def channel_has_stream(self, channel_id) -> bool:
        cid = str(channel_id)
        return any(i["kind"] == KIND_STREAM and i["channel_id"] == cid for i in self._data.values())

    @staticmethod
    def start_dt(info: dict) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(info["start_ts"], tz=VN_TZ)

    # ---------- MUTATE ----------
    def set(self, user_id, kind: str, channel_id, start_ts: float) -> None:
        self._data[str(user_id)] = {
            "kind": kind,
            "channel_id": str(channel_id),
            "start_ts": float(start_ts),
        }
        self._persist()

    def pop(self, user_id) -> dict | None:
        info = self._data.pop(str(user_id), None)
        if info is not None:
            self._persist()
        return info


# Singleton
state = LiveState()
