"""
Voice Alert Cog — nhắc CTV ở trong voice mà không bật Go Live.

LOGIC:
1. CTV ở trong BẤT KỲ kênh voice nào của server (trừ kênh AFK), không chia sẻ màn hình,
   và trong kênh đó không có CTV nào đang live → tính giờ (joined_ts).
   Kênh đang có CTV live → coi như đang xem, không nhắc.
2. Task mỗi 20 giây:
   - Sau `delay_before_alert_minutes` (mặc định 15) → bắt đầu nhắc.
   - Trong `alert_window_minutes` (mặc định 15), phát giọng nói `alert_count` lần cách đều.
   - Hết cửa sổ vẫn chưa live → ngắt khỏi voice + ghi log.
3. Bật Go Live → xóa đồng hồ.
4. Rời voice / sang kênh đang có người live → TẠM DỪNG đồng hồ.
   Quay lại trong 5 phút → giữ đồng hồ cũ (chống lách bằng cách ra vào liên tục).
   Quá 5 phút → đồng hồ mới. Chuyển thẳng sang kênh khác → giữ đồng hồ.
5. Mỗi sự kiện voice: xét lại TẤT CẢ CTV trong kênh cũ và kênh mới
   (vd. CTV đang live rời đi → các CTV còn ngồi audio bắt đầu bị tính giờ).

Cog này KHÔNG động vào việc tính giờ live/audio (live_tracker_cog).
"""
import asyncio
import logging

import discord
from discord.ext import commands, tasks

from config import LATE_ALERT_CHANNEL_ID, VOICE_ALERT_REJOIN_GRACE_MINUTES, is_main_guild
from core.data_manager import db
from helpers.time_utils import from_ts, fmt_dur, fmt_full, now_ts, now_vn
from helpers.tts import generate_tts_file, cleanup_tts_file, DEFAULT_VOICE
from helpers.voice_manager import play_audio_in_channel, kick_from_voice

log = logging.getLogger(__name__)

GRACE_SECONDS = VOICE_ALERT_REJOIN_GRACE_MINUTES * 60

STATUS_STREAMING = "streaming"   # đang chia sẻ màn hình
STATUS_TRACK = "track"           # cần tính giờ
STATUS_AWAY = "away"             # không ở voice / đang xem live / kênh AFK


def _same_ts(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def _is_afk(channel) -> bool:
    afk = getattr(channel.guild, "afk_channel", None)
    return afk is not None and afk.id == channel.id


def _channel_has_ctv_stream(channel, ctv_data: dict) -> bool:
    return any(
        not m.bot and str(m.id) in ctv_data and m.voice is not None and m.voice.self_stream
        for m in channel.members
    )


def classify(member, ctv_data: dict) -> tuple[str, str | None]:
    """Trạng thái Voice Alert của 1 CTV: (status, channel_id)."""
    voice = member.voice
    if voice is None or voice.channel is None:
        return STATUS_AWAY, None
    ch = voice.channel
    if not is_main_guild(ch.guild) or _is_afk(ch):
        return STATUS_AWAY, None
    if getattr(ch, "type", None) == discord.ChannelType.stage_voice:
        return STATUS_AWAY, None
    if voice.self_stream:
        return STATUS_STREAMING, str(ch.id)
    if _channel_has_ctv_stream(ch, ctv_data):
        return STATUS_AWAY, str(ch.id)  # đang xem CTV khác live
    return STATUS_TRACK, str(ch.id)


def apply_member(data: dict, member, ctv_data: dict, now: float) -> str | None:
    """Cập nhật đồng hồ của 1 người trong `data` (mutate). Trả về ghi chú để log."""
    uid = str(member.id)
    entry = data.get(uid)
    if uid not in ctv_data:
        if entry is not None:
            data.pop(uid, None)
            return "không còn là CTV → xóa đồng hồ"
        return None

    status, cid = classify(member, ctv_data)

    if status == STATUS_STREAMING:
        if entry is not None:
            data.pop(uid, None)
            return "bật Go Live → xóa đồng hồ"
        return None

    if status == STATUS_TRACK:
        if entry is None:
            data[uid] = {"channel_id": cid, "joined_ts": now, "alerts_sent": 0}
            return f"bắt đầu tính giờ tại kênh {cid}"
        if entry.get("paused_ts"):
            if now - float(entry["paused_ts"]) <= GRACE_SECONDS:
                entry.pop("paused_ts", None)
                entry["channel_id"] = cid
                return f"quay lại trong {VOICE_ALERT_REJOIN_GRACE_MINUTES} phút → giữ đồng hồ cũ"
            data[uid] = {"channel_id": cid, "joined_ts": now, "alerts_sent": 0}
            return "quay lại sau thời gian chờ → đồng hồ mới"
        if entry.get("channel_id") != cid:
            entry["channel_id"] = cid
            return f"chuyển sang kênh {cid} → giữ đồng hồ"
        return None

    # STATUS_AWAY
    if entry is not None and not entry.get("paused_ts"):
        entry["paused_ts"] = now
        return "rời voice / đang xem live → tạm dừng đồng hồ"
    return None


class VoiceAlertCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False
        self._was_enabled: bool | None = None
        self._eval_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.check_voice_alerts.start()

    def cog_unload(self):
        self.check_voice_alerts.cancel()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _main_guilds(self):
        return [g for g in self.bot.guilds if is_main_guild(g)]

    # =====================================================
    #  ĐỐI CHIẾU TOÀN BỘ
    # =====================================================
    async def _full_sync(self, reason: str) -> None:
        """Xét lại mọi CTV đang trong voice + mọi đồng hồ đang có."""
        ctv_data = db.load("ctv")
        members: dict[int, discord.Member] = {}
        for guild in self._main_guilds():
            for channel in guild.voice_channels:
                for m in channel.members:
                    if not m.bot and str(m.id) in ctv_data:
                        members[m.id] = m

        async with self._eval_lock:
            now = now_ts()
            notes: list[tuple[str, str]] = []

            def _apply(data):
                for m in members.values():
                    note = apply_member(data, m, ctv_data, now)
                    if note:
                        notes.append((m.display_name, note))
                # Đồng hồ của người không còn trong voice → tạm dừng
                for uid, entry in list(data.items()):
                    if not uid.isdigit() or not isinstance(entry, dict):
                        data.pop(uid, None)
                        continue
                    if int(uid) in members:
                        continue
                    if uid not in ctv_data:
                        data.pop(uid, None)
                    elif not entry.get("paused_ts"):
                        entry["paused_ts"] = now

            await db.aupdate("voice_alerts", _apply)

        for name, note in notes:
            log.info(f"🎙️ [voice-alert:{reason}] {name}: {note}")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True
        if db.load("voice_settings").get("enabled", True):
            await self._full_sync("khởi động")

    # =====================================================
    #  EVENT: voice state update
    # =====================================================
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or not is_main_guild(member.guild):
            return
        if not db.load("voice_settings").get("enabled", True):
            return

        ctv_data = db.load("ctv")
        # Xét người vừa thay đổi + mọi CTV trong kênh cũ và kênh mới
        members: dict[int, discord.Member] = {}
        if str(member.id) in ctv_data:
            members[member.id] = member
        for ch in (before.channel, after.channel):
            if ch is None:
                continue
            for m in ch.members:
                if not m.bot and str(m.id) in ctv_data:
                    members[m.id] = m
        if not members:
            return

        async with self._eval_lock:
            now = now_ts()
            notes: list[tuple[str, str]] = []

            def _apply(data):
                for m in members.values():
                    note = apply_member(data, m, ctv_data, now)
                    if note:
                        notes.append((m.display_name, note))

            await db.aupdate("voice_alerts", _apply)

        for name, note in notes:
            log.info(f"🎙️ [voice-alert] {name}: {note}")

    # =====================================================
    #  TASK: kiểm tra mỗi 20 giây
    # =====================================================
    @tasks.loop(seconds=20)
    async def check_voice_alerts(self):
        try:
            settings = db.load("voice_settings")
            enabled = settings.get("enabled", True)

            if not enabled:
                # Tắt tính năng → xóa mọi đồng hồ (tránh đồng hồ cũ kick nhầm khi bật lại)
                if self._was_enabled is not False:
                    await db.asave("voice_alerts", {})
                self._was_enabled = False
                return
            if self._was_enabled is False:
                # Vừa bật lại → tính giờ mới cho người đang ngồi trong voice
                self._was_enabled = True
                await self._full_sync("bật lại tính năng")
            self._was_enabled = True

            delay_sec = max(0, int(settings.get("delay_before_alert_minutes", 15)) * 60)
            window_sec = max(60, int(settings.get("alert_window_minutes", 15)) * 60)
            count = max(1, int(settings.get("alert_count", 5)))
            interval_sec = window_sec / count
            message = settings.get("message", "Vui lòng bật Go Live.")
            voice = settings.get("voice", DEFAULT_VOICE)
            alert_volume = float(settings.get("alert_volume", 1.0))

            now = now_ts()
            voice_alerts = db.load("voice_alerts")

            to_update: dict[str, tuple[float, int]] = {}
            to_remove: dict[str, float | None] = {}
            to_disconnect: list[tuple[str, str, float]] = []
            to_alert: list[tuple[str, str, int, float]] = []

            for uid, info in voice_alerts.items():
                if not isinstance(info, dict):
                    to_remove[uid] = None
                    continue
                joined_ts = float(info.get("joined_ts", 0))
                channel_id = info.get("channel_id")

                if info.get("paused_ts"):
                    if now - float(info["paused_ts"]) > GRACE_SECONDS:
                        to_remove[uid] = joined_ts
                    continue
                if not channel_id:
                    to_remove[uid] = joined_ts
                    continue

                time_in_voice = now - joined_ts
                if time_in_voice < delay_sec:
                    continue

                time_in_window = time_in_voice - delay_sec
                if time_in_window >= window_sec:
                    to_disconnect.append((uid, channel_id, joined_ts))
                    to_remove[uid] = joined_ts
                    continue

                # Lượt nhắc hiện tại (không phát bù các lượt đã lỡ)
                expected = min(int(time_in_window / interval_sec) + 1, count)
                if int(info.get("alerts_sent", 0)) < expected:
                    to_alert.append((uid, channel_id, expected, joined_ts))
                    to_update[uid] = (joined_ts, expected)

            if voice_alerts:
                log.debug(
                    f"[voice_alerts tick] {len(voice_alerts)} đồng hồ | delay={delay_sec}s "
                    f"window={window_sec}s count={count} | nhắc={len(to_alert)} ngắt={len(to_disconnect)}"
                )

            if to_update or to_remove:
                def _apply(data):
                    for uid, (joined, c) in to_update.items():
                        e = data.get(uid)
                        if isinstance(e, dict) and _same_ts(e.get("joined_ts"), joined) and not e.get("paused_ts"):
                            e["alerts_sent"] = c
                    for uid, joined in to_remove.items():
                        e = data.get(uid)
                        if joined is None or (isinstance(e, dict) and _same_ts(e.get("joined_ts"), joined)):
                            data.pop(uid, None)

                await db.aupdate("voice_alerts", _apply)

            for uid, cid, n, joined in to_alert:
                self._spawn(self._play_alert(uid, cid, message, voice, n, count, alert_volume, joined))
            for uid, cid, joined in to_disconnect:
                self._spawn(self._disconnect_ctv(uid, cid, joined))

        except Exception:
            log.exception("[TASK] check_voice_alerts lỗi")

    @check_voice_alerts.error
    async def _check_voice_alerts_error(self, error):
        log.exception(f"[TASK] check_voice_alerts CRASH: {error}")
        try:
            self.check_voice_alerts.restart()
            log.info("[TASK] check_voice_alerts đã restart")
        except Exception:
            log.exception("Restart task fail")

    @check_voice_alerts.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  KIỂM TRA LẠI TRƯỚC KHI NHẮC / NGẮT
    # =====================================================
    def _still_tracking(self, user_id: str, channel_id: str):
        """Trả về (channel, member) nếu CTV vẫn đang ngồi đúng kênh mà không live."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            return None, None
        member = channel.guild.get_member(int(user_id))
        if member is None:
            return channel, None
        status, cid = classify(member, db.load("ctv"))
        if status != STATUS_TRACK or cid != str(channel.id):
            return channel, None
        return channel, member

    async def _revert_alert_count(self, user_id: str, joined_ts: float, count: int) -> None:
        def _rv(data):
            e = data.get(user_id)
            if isinstance(e, dict) and _same_ts(e.get("joined_ts"), joined_ts) and int(e.get("alerts_sent", 0)) >= count:
                e["alerts_sent"] = count - 1

        await db.aupdate("voice_alerts", _rv)

    # =====================================================
    #  PHÁT NHẮC
    # =====================================================
    async def _play_alert(
        self,
        user_id: str,
        channel_id: str,
        message: str,
        voice: str,
        count: int,
        total: int,
        volume: float,
        joined_ts: float,
    ):
        try:
            channel, member = self._still_tracking(user_id, channel_id)
            if member is None:
                log.info(f"[voice_alerts] Bỏ lượt nhắc #{count} cho {user_id}: không còn ngồi chờ ở kênh {channel_id}")
                return

            personalized = message.replace("{ten_ctv}", member.display_name)
            audio_path = await generate_tts_file(personalized, voice=voice)
            if not audio_path:
                # Tạo giọng đọc lỗi → không tính lượt này, lần kiểm tra sau thử lại
                await self._revert_alert_count(user_id, joined_ts, count)
                log.warning(f"Tạo giọng đọc lỗi cho lượt nhắc #{count} của {member.display_name}, sẽ thử lại")
                return

            try:
                success = await play_audio_in_channel(channel, audio_path, volume=volume)
            finally:
                cleanup_tts_file(audio_path)

            if success:
                log.info(f"🔊 Đã phát nhắc #{count}/{total} cho CTV {member.display_name} tại #{channel.name}")
            else:
                log.warning(
                    f"❌ Phát nhắc #{count}/{total} lỗi cho {member.display_name} — "
                    "kiểm tra quyền Connect/Speak của bot và FFmpeg"
                )
        except Exception:
            log.exception(f"_play_alert cho user {user_id} lỗi")

    # =====================================================
    #  NGẮT KẾT NỐI + LOG
    # =====================================================
    async def _disconnect_ctv(self, user_id: str, channel_id: str, joined_ts: float):
        try:
            channel, member = self._still_tracking(user_id, channel_id)
            if member is None:
                log.info(f"CTV {user_id} không còn ngồi chờ ở kênh {channel_id} → không ngắt")
                return

            if not await kick_from_voice(member):
                return

            log.warning(f"🚪 Đã ngắt CTV {member.display_name} khỏi #{channel.name} (quá hạn không live)")
            await self._log_disconnect(user_id, channel, joined_ts)
        except Exception:
            log.exception(f"_disconnect_ctv cho user {user_id} lỗi")

    async def _log_disconnect(self, user_id: str, channel, joined_ts: float):
        settings = db.load("voice_settings")
        # Chưa cài kênh log riêng → gửi về kênh báo CTV đi trễ
        log_ch_id = settings.get("log_channel_id", 0) or LATE_ALERT_CHANNEL_ID
        if not log_ch_id:
            return
        log_channel = self.bot.get_channel(int(log_ch_id))
        if not log_channel:
            log.warning(f"Không tìm thấy kênh log {log_ch_id}")
            return

        join_dt = from_ts(joined_ts)
        disconnect_dt = now_vn()
        duration_sec = (disconnect_dt - join_dt).total_seconds()

        embed = discord.Embed(
            title="🚪 CTV Bị Ngắt Kết Nối",
            description="CTV ở trong voice nhưng không bật Go Live trong thời gian quy định.\n​",
            color=discord.Color.red(),
        )
        embed.add_field(name="👤 CTV", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="📺 Kênh", value=channel.name, inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(name="🕐 Bắt đầu tính giờ", value=f"`{fmt_full(join_dt)}`", inline=True)
        embed.add_field(name="⏱️ Không live trong", value=f"`{fmt_dur(duration_sec)}`", inline=True)
        embed.add_field(name="🚪 Bị ngắt lúc", value=f"`{fmt_full(disconnect_dt)}`", inline=True)
        embed.set_footer(text="Voice Alert | Thời gian audio vẫn được ghi trong Check CTV")

        try:
            await log_channel.send(embed=embed)
        except Exception:
            log.exception("Gửi log ngắt kết nối lỗi")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceAlertCog(bot))
