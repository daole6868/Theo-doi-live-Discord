"""
Voice Alert Cog — nhắc CTV vào voice channel nhưng chưa bật Go Live.

LOGIC:
1. CTV vào voice channel (chưa self_stream) → ghi vào voice_alerts state.
2. Task chạy mỗi phút:
   - Sau `delay_before_alert_minutes` (default 15) → bắt đầu nhắc.
   - Trong cửa sổ `alert_window_minutes` (default 15), phát TTS `alert_count` (default 5) lần cách đều.
   - Sau hết window vẫn chưa live → kick khỏi voice channel + log.
3. CTV bật Go Live bất cứ lúc nào → xóa khỏi voice_alerts state.
4. CTV rời voice → xóa khỏi voice_alerts state.

QUAN TRỌNG: Cog này KHÔNG động vào session tracking cũ (live_tracker_cog).
Chỉ track "voice thoại chưa live" — không nhầm với "live thật".
"""
import asyncio
import logging

import discord
from discord.ext import commands, tasks

from core.data_manager import db
from core.state import state
from helpers.time_utils import now_vn, from_ts, fmt_dur, fmt_full, now_ts
from helpers.tts import generate_tts_file, cleanup_tts_file, DEFAULT_VOICE
from helpers.voice_manager import play_audio_in_channel, kick_from_voice

log = logging.getLogger(__name__)


class VoiceAlertCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Set lock để tránh 2 task phát audio cùng lúc trong cùng guild
        self._play_locks: dict[int, asyncio.Lock] = {}
        self._synced = False
        self.check_voice_alerts.start()

    def cog_unload(self):
        self.check_voice_alerts.cancel()

    def _get_play_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._play_locks:
            self._play_locks[guild_id] = asyncio.Lock()
        return self._play_locks[guild_id]

    def _is_managed(self, channel_id) -> bool:
        return str(channel_id) in db.load("channels")

    # =====================================================
    #  ON READY — sync voice_alerts với thực tế Discord
    # =====================================================
    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True
        await self._sync_voice_alerts_state()

    async def _sync_voice_alerts_state(self):
        """
        Reconcile voice_alerts state với thực tế Discord sau restart.

        - Xóa entry nếu CTV không còn trong voice channel tương ứng (rời, chuyển, bật Live).
        - Thêm entry mới nếu phát hiện CTV đang thoại nhưng chưa được track.
        - Giữ nguyên joined_ts + alerts_sent cho entry vẫn valid (không reset timer).
        """
        voice_alerts = db.load("voice_alerts")
        ctv_data = db.load("ctv")
        channels_data = db.load("channels")

        to_remove: list[str] = []
        to_add: dict[str, dict] = {}
        valid_uids: set[str] = set()

        # 1. Verify existing entries — Option D: kênh managed + chưa có ai live
        for uid, info in voice_alerts.items():
            channel_id = info.get("channel_id")
            if not channel_id or not uid.isdigit():
                to_remove.append(uid)
                continue

            channel = self.bot.get_channel(int(channel_id))
            if not isinstance(channel, discord.VoiceChannel):
                to_remove.append(uid)
                continue

            # Kênh có ai đang live không? (Option D)
            if state.is_live(channel_id):
                to_remove.append(uid)
                log.info(f"[sync] Remove {uid}: kênh {channel_id} đã có người live")
                continue

            member = channel.guild.get_member(int(uid))
            if not member:
                # Cache chưa có → giữ entry, sẽ verify sau khi member cache load
                log.debug(f"[sync] Member {uid} chưa cache, giữ entry")
                valid_uids.add(uid)
                continue

            # Verify voice state
            if member.voice is None:
                # State chưa sync sau restart, giữ entry để chờ Discord cập nhật
                log.debug(f"[sync] {uid} voice state null (chưa sync), giữ entry")
                valid_uids.add(uid)
                continue

            if member.voice.channel is None:
                # User đã rời voice thật (state đã sync) → remove
                to_remove.append(uid)
                continue

            if member.voice.channel.id != int(channel_id):
                # User đã chuyển kênh khác → remove
                to_remove.append(uid)
                continue

            if member.voice.self_stream:
                # User đã bật Live → remove
                to_remove.append(uid)
                continue

            valid_uids.add(uid)

        # 2. Scan voice channels tìm CTV đang thoại nhưng chưa track
        # (Option D: chỉ track nếu kênh chưa có ai live — không cần "kênh mình quản")
        for cid_str in channels_data.keys():
            try:
                cid_int = int(cid_str)
            except ValueError:
                continue
            channel = self.bot.get_channel(cid_int)
            if not isinstance(channel, discord.VoiceChannel):
                continue

            # Nếu kênh này đã có người live → không track ai vào kênh này (Option D)
            if state.is_live(cid_str):
                continue

            for member in channel.members:
                if member.bot:
                    continue
                uid_str = str(member.id)
                if uid_str not in ctv_data:
                    continue  # Không phải CTV
                if member.voice.self_stream:
                    continue  # Đang stream (LiveTrackerCog đã handle)
                if uid_str in valid_uids:
                    continue  # Đã track
                # OK → thêm mới (không cần check kênh có phải mình quản không)
                to_add[uid_str] = {
                    "channel_id": cid_str,
                    "joined_ts": now_ts(),
                    "alerts_sent": 0,
                }
                ctv_own = cid_str in ctv_data.get(uid_str, [])
                marker = "" if ctv_own else " (kênh khác)"
                log.info(f"🎙️ [sync] Detect CTV {member.display_name} tại #{channel.name}{marker}")

        # Apply
        if to_remove or to_add:
            def _apply(data):
                for uid in to_remove:
                    data.pop(uid, None)
                for uid, entry in to_add.items():
                    data[uid] = entry

            await db.aupdate("voice_alerts", _apply)
            log.info(
                f"[sync voice_alerts] Xóa {len(to_remove)} entry stale, thêm {len(to_add)} entry mới"
            )
        else:
            log.debug("[sync voice_alerts] Không có thay đổi")

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
        if member.bot:
            return

        # Chỉ track CTV
        ctv_data = db.load("ctv")
        user_id_str = str(member.id)
        if user_id_str not in ctv_data:
            return

        # Check settings enabled
        settings = db.load("voice_settings")
        if not settings.get("enabled", True):
            return

        # Xác định state hiện tại từ `after`
        in_voice = bool(after.channel)
        is_streaming = bool(after.self_stream) if in_voice else False
        cid_str = str(after.channel.id) if in_voice else ""
        channel_managed = self._is_managed(after.channel.id) if in_voice else False

        # Option D — 4 điều kiện phải đều thỏa:
        # 1. Đang trong voice
        # 2. Không stream
        # 3. Kênh bot quản lý (managed)
        # 4. Kênh chưa có ai đang live (state.is_live = False)
        # → KHÔNG check "kênh mình quản" — mọi CTV trong kênh chưa live đều bị nhắc
        channel_is_live = state.is_live(cid_str) if cid_str else False

        should_track = (
            in_voice
            and not is_streaming
            and channel_managed
            and not channel_is_live
        )

        current_alerts = db.load("voice_alerts")
        existing = current_alerts.get(user_id_str)

        if should_track:
            # Cùng kênh → giữ nguyên (không reset timer)
            if existing and existing.get("channel_id") == cid_str:
                return

            def _add(data):
                data[user_id_str] = {
                    "channel_id": cid_str,
                    "joined_ts": now_ts(),
                    "alerts_sent": 0,
                }

            await db.aupdate("voice_alerts", _add)

            # Log rõ có phải kênh mình quản hay không
            ctv_own = user_id_str in ctv_data and cid_str in ctv_data[user_id_str]
            marker = "" if ctv_own else " (kênh CTV khác - vẫn nhắc vì chưa ai live)"
            log.info(f"🎙️ CTV {member} vào voice #{after.channel.name}{marker}")

        else:
            # Không nên track → xóa entry nếu có
            if existing:
                def _rm(data):
                    data.pop(user_id_str, None)

                await db.aupdate("voice_alerts", _rm)

                # Log lý do
                if is_streaming:
                    log.info(f"CTV {member} đã bật Go Live → xóa voice_alert entry")
                elif not in_voice:
                    log.info(f"CTV {member} đã rời voice → xóa voice_alert entry")
                elif not channel_managed:
                    log.info(f"CTV {member} chuyển sang kênh không managed → xóa entry")
                elif channel_is_live:
                    log.info(f"CTV {member} vào kênh đã có người live (chỉ xem) → xóa entry")

            # Log info khi không track (dù không có entry cũ)
            elif in_voice and channel_managed and not is_streaming and channel_is_live:
                log.info(f"⏭️ CTV {member} vào #{after.channel.name} (đã có người live, chỉ xem)")

    # =====================================================
    #  TASK: check voice alerts mỗi 20 giây (thay vì 1 phút để timing chính xác hơn)
    # =====================================================
    @tasks.loop(seconds=20)
    async def check_voice_alerts(self):
        try:
            settings = db.load("voice_settings")
            if not settings.get("enabled", True):
                return

            delay_sec = max(0, int(settings.get("delay_before_alert_minutes", 15)) * 60)
            window_sec = max(60, int(settings.get("alert_window_minutes", 15)) * 60)
            count = max(1, int(settings.get("alert_count", 5)))
            interval_sec = window_sec / count
            message = settings.get("message", "Vui lòng bật Go Live.")
            voice = settings.get("voice", DEFAULT_VOICE)
            alert_volume = float(settings.get("alert_volume", 1.0))

            now = now_ts()
            voice_alerts = db.load("voice_alerts")

            # DEBUG: log tick khi có entries + hiện settings hiện tại để verify
            if voice_alerts:
                log.info(
                    f"[voice_alerts tick] Track {len(voice_alerts)} CTV | "
                    f"CONFIG: delay={delay_sec}s ({delay_sec//60}p), "
                    f"window={window_sec}s ({window_sec//60}p), "
                    f"count={count} lần, "
                    f"interval={interval_sec:.0f}s ({interval_sec/60:.1f}p mỗi lần)"
                )

            to_update: dict[str, int] = {}
            to_remove: list[str] = []
            to_disconnect: list[tuple[str, str, float]] = []
            to_alert: list[tuple[str, str, int]] = []

            for uid, info in voice_alerts.items():
                joined_ts = info.get("joined_ts", 0)
                channel_id = info.get("channel_id")
                alerts_sent = info.get("alerts_sent", 0)

                if not channel_id:
                    to_remove.append(uid)
                    continue

                # Option C: nếu giữa chừng có ai bật live trong kênh này → hủy tracking
                if state.is_live(channel_id):
                    log.info(
                        f"[tick] user={uid}: kênh {channel_id} vừa có người live "
                        f"→ hủy tracking (CTV này chỉ đang xem)"
                    )
                    to_remove.append(uid)
                    continue

                time_in_voice = now - joined_ts

                # DEBUG: log status của mỗi user
                if time_in_voice >= delay_sec:
                    time_in_window = time_in_voice - delay_sec
                    if time_in_window >= window_sec:
                        expected = count  # đã hết window
                    else:
                        expected = min(int(time_in_window / interval_sec) + 1, count)
                    log.info(
                        f"[tick] user={uid}: in_voice={int(time_in_voice)}s, "
                        f"in_window={int(time_in_window)}s, sent={alerts_sent}, "
                        f"expected={expected}, will_alert={alerts_sent < expected and time_in_window < window_sec}"
                    )
                else:
                    log.info(
                        f"[tick] user={uid}: in_voice={int(time_in_voice)}s "
                        f"(chờ delay {delay_sec}s)"
                    )

                # Chưa hết delay
                if time_in_voice < delay_sec:
                    continue

                time_in_window = time_in_voice - delay_sec

                # Đã hết window → cần disconnect
                if time_in_window >= window_sec:
                    log.info(f"[tick] user={uid} HẾT WINDOW → queue disconnect")
                    to_disconnect.append((uid, channel_id, joined_ts))
                    to_remove.append(uid)
                    continue

                # Tính số alert cần phát
                expected = min(int(time_in_window / interval_sec) + 1, count)

                if alerts_sent < expected:
                    next_count = alerts_sent + 1
                    to_alert.append((uid, channel_id, next_count))
                    to_update[uid] = next_count
                    log.info(
                        f"[voice_alerts] Queue alert #{next_count}/{count} cho user {uid} "
                        f"(in_voice={int(time_in_voice)}s, in_window={int(time_in_window)}s)"
                    )

            # Apply state changes
            if to_update or to_remove:
                def _apply(data):
                    for uid, c in to_update.items():
                        if uid in data:
                            data[uid]["alerts_sent"] = c
                    for uid in to_remove:
                        data.pop(uid, None)

                await db.aupdate("voice_alerts", _apply)

            # Play alerts (không block loop — spawn task)
            for uid, cid, next_count in to_alert:
                asyncio.create_task(
                    self._play_alert(uid, cid, message, voice, next_count, count, alert_volume)
                )

            # Disconnect (spawn task)
            for uid, cid, joined_ts in to_disconnect:
                asyncio.create_task(self._disconnect_ctv(uid, cid, joined_ts))

        except Exception:
            log.exception("[TASK] check_voice_alerts lỗi")

    @check_voice_alerts.error
    async def _check_voice_alerts_error(self, error):
        """Nếu task loop crash → log để biết + restart task."""
        log.exception(f"[TASK] check_voice_alerts CRASH: {error}")
        # Restart task
        try:
            self.check_voice_alerts.restart()
            log.info("[TASK] check_voice_alerts đã restart")
        except Exception:
            log.exception("Restart task fail")

    @check_voice_alerts.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  PLAY ALERT — TTS vào voice channel
    # =====================================================
    async def _play_alert(
        self,
        user_id: str,
        channel_id: str,
        message: str,
        voice: str,
        count: int = 1,
        total: int = 1,
        volume: float = 1.0,
    ):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if not isinstance(channel, discord.VoiceChannel):
                log.warning(f"Kênh {channel_id} không phải VoiceChannel, remove entry")
                await self._remove_entry(user_id)
                return

            # Verify CTV — CHỈ remove entry khi có EVIDENCE RÕ RÀNG
            # (voice state null có thể do Discord cache delay tạm thời, không nên remove)
            member = channel.guild.get_member(int(user_id))
            if not member:
                log.warning(f"Member {user_id} chưa có trong cache, skip alert (GIỮ entry để retry)")
                return
            if not member.voice or not member.voice.channel:
                log.warning(
                    f"CTV {user_id} voice state null (có thể do Discord delay), "
                    f"skip alert #{count}/{total} — GIỮ entry"
                )
                return
            # Đã chuyển kênh KHÁC → remove entry (evidence rõ ràng)
            if member.voice.channel.id != channel.id:
                log.info(f"CTV {user_id} đã chuyển kênh khác, remove entry")
                await self._remove_entry(user_id)
                return
            # Đã bật Live → remove entry (evidence rõ ràng)
            if member.voice.self_stream:
                log.info(f"CTV {user_id} đã bật Live, remove entry")
                await self._remove_entry(user_id)
                return

            # Lock để tránh 2 alert cùng lúc trên 1 guild
            lock = self._get_play_lock(channel.guild.id)
            async with lock:
                # Generate TTS
                personalized = message.replace("{ten_ctv}", member.display_name)
                log.info(
                    f"[voice_alerts] Bắt đầu phát alert #{count}/{total} cho "
                    f"{member.display_name} tại #{channel.name}"
                )
                audio_path = await generate_tts_file(personalized, voice=voice)
                if not audio_path:
                    log.warning(f"Generate TTS fail cho alert #{count}, skip")
                    return

                # Play với volume
                success = await play_audio_in_channel(channel, audio_path, volume=volume)

                # Cleanup
                cleanup_tts_file(audio_path)

                if success:
                    log.info(
                        f"🔊 Đã phát alert #{count}/{total} cho CTV "
                        f"{member.display_name} tại #{channel.name}"
                    )
                else:
                    log.warning(
                        f"❌ Play alert #{count}/{total} FAIL cho {member.display_name} — "
                        f"có thể do voice gateway hoặc FFmpeg"
                    )

        except Exception:
            log.exception(f"_play_alert cho user {user_id} lỗi")

    async def _remove_entry(self, user_id: str) -> None:
        """Xóa entry khỏi voice_alerts (dùng khi detect state cũ)."""
        def _rm(data):
            data.pop(user_id, None)

        try:
            await db.aupdate("voice_alerts", _rm)
        except Exception:
            log.exception(f"Remove voice_alerts entry {user_id} lỗi")

    # =====================================================
    #  DISCONNECT CTV + LOG
    # =====================================================
    async def _disconnect_ctv(self, user_id: str, channel_id: str, joined_ts: float):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                return

            member = channel.guild.get_member(int(user_id))
            if not member:
                log.warning(f"Không tìm thấy member {user_id} để kick, skip")
                return

            # Nếu voice state null → có thể Discord cache delay, retry sau 2 giây
            if not member.voice or not member.voice.channel:
                log.info(f"CTV {user_id} voice state null, retry sau 2s")
                await asyncio.sleep(2)
                # Re-fetch member để có state mới nhất
                member = channel.guild.get_member(int(user_id))
                if not member or not member.voice or not member.voice.channel:
                    log.info(f"CTV {user_id} đã rời voice thực sự, skip kick")
                    return

            if member.voice.channel.id != channel.id:
                log.info(f"CTV {user_id} đã chuyển kênh, không kick")
                return
            if member.voice.self_stream:
                log.info(f"CTV {user_id} vừa bật Live, không kick")
                return

            success = await kick_from_voice(member)
            if not success:
                return

            log.warning(f"🚪 Đã kick CTV {member.display_name} khỏi #{channel.name} (quá hạn không live)")

            # Log vào kênh log
            await self._log_disconnect(user_id, channel_id, joined_ts)

        except Exception:
            log.exception(f"_disconnect_ctv cho user {user_id} lỗi")

    async def _log_disconnect(self, user_id: str, channel_id: str, joined_ts: float):
        settings = db.load("voice_settings")
        log_ch_id = settings.get("log_channel_id", 0)
        if not log_ch_id:
            return

        log_channel = self.bot.get_channel(int(log_ch_id))
        if not log_channel:
            log.warning(f"Không tìm thấy log channel {log_ch_id}")
            return

        join_dt = from_ts(joined_ts)
        disconnect_dt = now_vn()
        duration_sec = (disconnect_dt - join_dt).total_seconds()

        channels_data = db.load("channels")
        c_name = channels_data.get(str(channel_id), {}).get("name", f"Kênh {channel_id}")

        embed = discord.Embed(
            title="🚪 CTV Bị Ngắt Kết Nối",
            description=(
                f"CTV vào voice channel nhưng không bật Go Live trong thời gian quy định.\n\u200b"
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name="👤 CTV", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="📺 Kênh", value=c_name, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
        embed.add_field(name="🕐 Vào kênh lúc", value=f"`{fmt_full(join_dt)}`", inline=True)
        embed.add_field(name="⏱️ Ở trong voice", value=f"`{fmt_dur(duration_sec)}`", inline=True)
        embed.add_field(name="🚪 Bị ngắt lúc", value=f"`{fmt_full(disconnect_dt)}`", inline=True)
        embed.set_footer(text="Voice Alert System | Không phải phiên live")

        try:
            await log_channel.send(embed=embed)
        except Exception:
            log.exception("Gửi log disconnect lỗi")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceAlertCog(bot))
