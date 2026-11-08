"""
Bộ tính giờ — logic thuần, không gọi Discord API (để dễ kiểm thử).

Ý tưởng: mỗi CTV có 1 "trạng thái mong muốn" lấy từ Discord:
    None                      → không ở trong voice
    ("stream", channel_id)    → đang bật Go Live (chia sẻ màn hình)
    ("audio",  channel_id)    → ở trong voice nhưng không chia sẻ màn hình

`Tracker.apply()` so sánh với đoạn đang chạy trong `state`:
    - giống nhau      → không làm gì (bật/tắt mic, tắt loa... không ảnh hưởng)
    - khác nhau       → chốt đoạn cũ (lưu vào SQLite), mở đoạn mới

Mỗi CTV có đoạn riêng → nhiều CTV live chung kênh vẫn tính đúng cho từng người,
và người khác tắt stream không làm chốt phiên của CTV đang live.
"""
import logging
from dataclasses import dataclass

from config import KIND_AUDIO, KIND_STREAM

log = logging.getLogger(__name__)

FLAG_OFFLINE = "offline"  # giờ kết thúc ước tính do bot offline / mất kết nối


@dataclass
class Change:
    user_id: str
    action: str            # "start" | "stop"
    kind: str              # "stream" | "audio"
    channel_id: str
    start_ts: float
    end_ts: float | None = None
    saved_seconds: int = 0
    flags: str = ""


def desired_from_voice(voice, guild=None) -> tuple[str, str] | None:
    """
    Tính trạng thái mong muốn từ 1 VoiceState của Discord.

    Kênh AFK của server không được tính (coi như không ở trong voice).
    """
    if voice is None or getattr(voice, "channel", None) is None:
        return None
    channel = voice.channel
    g = guild or getattr(channel, "guild", None)
    afk = getattr(g, "afk_channel", None) if g is not None else None
    if afk is not None and afk.id == channel.id:
        return None
    kind = KIND_STREAM if getattr(voice, "self_stream", False) else KIND_AUDIO
    return kind, str(channel.id)


class Tracker:
    def __init__(self, state, store):
        self.state = state
        self.store = store

    # -----------------------------------------------------
    def apply(
        self,
        user_id,
        desired: tuple[str, str] | None,
        now: float,
        end_ts: float | None = None,
        flags: str = "",
    ) -> list[Change]:
        """
        Đưa đoạn đang chạy của `user_id` về `desired`.

        end_ts: giờ chốt đoạn cũ (mặc định = now). Dùng khi biết bot đã mất kết nối
                từ trước đó — không tính phần thời gian bot không theo dõi được.
        """
        uid = str(user_id)
        cur = self.state.get(uid)

        if cur and desired and cur["kind"] == desired[0] and cur["channel_id"] == str(desired[1]):
            return []
        if cur is None and desired is None:
            return []

        changes: list[Change] = []
        if cur:
            end = now if end_ts is None else max(cur["start_ts"], min(end_ts, now))
            saved = self.store.add_session(
                uid, cur["channel_id"], cur["kind"], cur["start_ts"], end, flags=flags,
            )
            self.state.pop(uid)
            changes.append(Change(
                uid, "stop", cur["kind"], cur["channel_id"], cur["start_ts"], end, saved, flags,
            ))

        if desired:
            kind, cid = desired
            self.state.set(uid, kind, cid, now)
            changes.append(Change(uid, "start", kind, str(cid), now))

        return changes

    # -----------------------------------------------------
    def reconcile(
        self,
        desired_map: dict[str, tuple[str, str]],
        now: float,
        end_ts: float | None = None,
    ) -> list[Change]:
        """
        Đối chiếu toàn bộ với Discord.

        desired_map: {user_id: (kind, channel_id)} của các CTV ĐANG ở trong voice.
        Ai đang được tính mà không có trong map → chốt đoạn (đã rời voice).
        Đoạn bị chốt ở đây được đánh dấu "offline" (giờ kết thúc là ước tính,
        vì bot đã bỏ lỡ sự kiện thật).
        """
        changes: list[Change] = []
        for uid in sorted(set(self.state.user_ids()) | set(desired_map)):
            changes += self.apply(
                uid, desired_map.get(uid), now, end_ts=end_ts, flags=FLAG_OFFLINE,
            )
        return changes

    # -----------------------------------------------------
    def startup(
        self,
        desired_map: dict[str, tuple[str, str]],
        now: float,
        last_heartbeat: float | None,
        legacy_max_age: float,
    ) -> list[Change]:
        """
        Chạy 1 lần khi bot khởi động.

        - Có nhịp tim: mọi đoạn cũ được chốt tại lần cuối bot còn chạy
          (khoảng bot offline KHÔNG được tính). Ai vẫn còn trong voice được
          mở đoạn mới từ bây giờ.
        - Chưa có nhịp tim (lần đầu chạy bản mới): chỉ giữ đoạn cũ nếu CTV vẫn đang
          ở đúng trạng thái đó và đoạn bắt đầu chưa quá `legacy_max_age` giây;
          còn lại bỏ đi (không biết họ tắt lúc nào → không tính để tránh giờ ảo).
        """
        changes: list[Change] = []
        for uid, info in self.state.snapshot().items():
            if last_heartbeat is not None:
                end = max(info["start_ts"], min(last_heartbeat, now))
                saved = self.store.add_session(
                    uid, info["channel_id"], info["kind"], info["start_ts"], end,
                    flags=FLAG_OFFLINE,
                )
                self.state.pop(uid)
                changes.append(Change(
                    uid, "stop", info["kind"], info["channel_id"], info["start_ts"], end,
                    saved, FLAG_OFFLINE,
                ))
                continue

            d = desired_map.get(uid)
            same = d is not None and d[0] == info["kind"] and str(d[1]) == info["channel_id"]
            if same and now - info["start_ts"] <= legacy_max_age:
                continue  # Giữ nguyên, tiếp tục tính
            self.state.pop(uid)
            log.warning(
                f"Bỏ đoạn cũ không xác minh được của {uid} "
                f"({info['kind']} @ {info['channel_id']}, start_ts={info['start_ts']})"
            )

        changes += self.reconcile(desired_map, now)
        return changes
