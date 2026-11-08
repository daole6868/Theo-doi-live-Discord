"""
Live Tracker Cog — detect LIVE/OFF qua on_voice_state_update.

FIX BUG (batch 4A):
- BUG #1: Non-CTV user live → KHÔNG track, KHÔNG lưu session.
- BUG #2: Non-CTV user live xong → KHÔNG gửi DM chọn giờ live tiếp theo.
- BUG #2 (case CTV_Y live vào kênh của CTV_A): VẪN track (lưu session cho user Y),
  NHƯNG không gửi NextLive DM (vì Y không phải chủ kênh này).
  Report kênh cũng không cộng thời gian này (đã fix trong stats.py).
- BUG #3: Notify subscribers — không chỉ subs của kênh đang live, mà iterate qua
  TẤT CẢ kênh mà CTV đang live quản lý → notify TẤT CẢ subs của những kênh đó.
  Dedupe để không gửi DM trùng.
"""
import logging

import discord
from discord.ext import commands

from config import (
    MIN_SESSION_SECONDS,
    EMBED_LIVE_NOTIF,
    EMBED_CTV_NEXT_LIVE,
)
from core.data_manager import db
from core.state import state
from helpers.embeds import get_custom_embed
from helpers.time_utils import now_vn
from views.notify_view import NotifyView
from views.ctv_view import NextLiveView

log = logging.getLogger(__name__)


class LiveTrackerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready = False

    # =====================================================
    #  ON READY
    # =====================================================
    @commands.Cog.listener()
    async def on_ready(self):
        if self._ready:
            return
        self._ready = True

        log.info(f"✅ Bot đã đăng nhập: {self.bot.user} (id={self.bot.user.id})")

        state.load_from_disk()

        ctv_data = db.load("ctv")
        detected = 0

        # Scan TẤT CẢ voice channels trong mọi guild bot có mặt
        # (không chỉ managed — để detect CTV live ở kênh chưa add vào bot)
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    if not (member.voice and member.voice.self_stream):
                        continue
                    # Chỉ track CTV
                    if str(member.id) not in ctv_data:
                        log.info(f"⏭️ Bỏ qua non-CTV đang live: {member} @ {channel.name}")
                        continue
                    cid_str = str(channel.id)
                    if state.is_live(cid_str):
                        continue  # Đã track (từ persist)
                    state.start(cid_str, member.id)
                    detected += 1
                    log.info(f"🔴 Detect live sẵn (on_ready): {member} @ {channel.name}")
                    break  # 1 kênh chỉ track 1 người live

        if detected:
            log.info(f"Detect {detected} phiên live đang chạy khi khởi động.")

    # =====================================================
    #  ON VOICE STATE UPDATE
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

        was_streaming = bool(before.channel and before.self_stream)
        is_streaming = bool(after.channel and after.self_stream)

        # Track live của CTV ở BẤT KỲ kênh voice nào (không chỉ managed)
        # → Để notify subs khi CTV live ở kênh của người khác hoặc kênh chưa add vào bot
        # _start_live vẫn tự skip non-CTV bên trong

        # Case 1: bắt đầu stream
        if is_streaming and not was_streaming:
            await self._start_live(after.channel, member)

        # Case 2: kết thúc stream
        elif was_streaming and not is_streaming:
            await self._stop_live(before.channel.id, member.id)

        # Case 3: chuyển kênh khi đang stream
        elif was_streaming and is_streaming and before.channel.id != after.channel.id:
            await self._stop_live(before.channel.id, member.id)
            await self._start_live(after.channel, member)

    # ---------- HELPERS ----------
    def _is_managed(self, channel_id) -> bool:
        return str(channel_id) in db.load("channels")

    async def _start_live(self, channel: discord.VoiceChannel, member: discord.Member):
        """Bắt đầu 1 phiên live."""
        cid_str = str(channel.id)
        user_id_str = str(member.id)

        if state.is_live(cid_str):
            return

        # ===== FIX BUG #1: chỉ track nếu user là CTV (bất kỳ CTV nào) =====
        ctv_data = db.load("ctv")
        if user_id_str not in ctv_data:
            log.info(f"⏭️ Bỏ qua non-CTV live: {member} @ {channel.name}")
            return

        # Track session
        now = now_vn()
        state.start(cid_str, member.id, now)

        # Log rõ ràng case CTV live ở kênh không phải của mình
        is_own_channel = cid_str in ctv_data.get(user_id_str, [])
        if is_own_channel:
            log.info(f"🔴 LIVE START: {member} @ {channel.name} (kênh của mình)")
        else:
            log.info(f"🔴 LIVE START: {member} @ {channel.name} (kênh của CTV KHÁC — tính cho user)")

        # Huỷ next_live của kênh này (nếu có)
        def _remove_next(data):
            data.pop(cid_str, None)

        await db.aupdate("next_live", _remove_next)

        # ===== FIX BUG #3: Notify subs của TẤT CẢ kênh mà CTV này quản lý =====
        await self._notify_subscribers(channel, member, ctv_data)

    async def _notify_subscribers(
        self,
        channel: discord.VoiceChannel,
        member: discord.Member,
        ctv_data: dict,
    ):
        """
        Gửi DM cho subs của TẤT CẢ kênh mà CTV này quản lý.

        Ví dụ: CTV Y quản kênh Y1, Y2, Y3. Y live ở kênh X (bất kỳ).
        → Notify tất cả users đã sub bất kỳ Y1, Y2, Y3 (dedupe).
        """
        user_id_str = str(member.id)
        managed_channels = ctv_data.get(user_id_str, [])

        subs_data = db.load("subscribers")
        users_to_notify: set[str] = set()
        for ch_id in managed_channels:
            for uid in subs_data.get(str(ch_id), []):
                users_to_notify.add(str(uid))

        if not users_to_notify:
            return

        embed = get_custom_embed(
            EMBED_LIVE_NOTIF,
            title=f"🔴 CTV **{member.display_name}** đang LIVE!",
            desc=(
                f"Đang phát trực tiếp tại kênh **{channel.name}**.\n"
                "Vào xem ngay!"
            ),
            color=discord.Color.red(),
        )
        view = NotifyView(channel.id, channel.guild.id)

        sent = failed = 0
        for uid in users_to_notify:
            try:
                user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                await user.send(embed=embed, view=view)
                sent += 1
            except (discord.Forbidden, discord.HTTPException) as e:
                failed += 1
                log.debug(f"Không DM được user {uid}: {e}")
            except Exception:
                failed += 1
                log.exception(f"Lỗi gửi DM tới {uid}")

        log.info(
            f"Đã DM live tới {sent}/{len(users_to_notify)} sub (fail: {failed}). "
            f"CTV {member.display_name} → {len(managed_channels)} kênh quản lý được xét."
        )

    async def _stop_live(self, channel_id, member_id):
        """Kết thúc phiên: lưu session, gửi NextLive DM CHỈ KHI user là CTV chính của kênh."""
        cid_str = str(channel_id)

        if not state.is_live(cid_str):
            return

        start_dt = state.get_start(cid_str)
        user_id = state.get_user(cid_str) or str(member_id)
        state.stop(cid_str)

        if start_dt is None:
            return

        end_dt = now_vn()
        duration = (end_dt - start_dt).total_seconds()

        if duration < MIN_SESSION_SECONDS:
            log.debug(f"Bỏ qua session quá ngắn ({duration}s) kênh {channel_id}")
            return

        # Lưu session với user_id đầy đủ
        def _save(sessions):
            sessions.setdefault(cid_str, []).append({
                "start_time": start_dt.timestamp(),
                "end_time": end_dt.timestamp(),
                "duration": int(duration),
                "user_id": str(user_id),
            })

        await db.aupdate("sessions", _save)
        log.info(f"⚫ LIVE STOP: {user_id} @ kênh {channel_id}, {int(duration)}s")

        # ===== FIX BUG #2: CHỈ gửi NextLive DM khi user là CTV CHÍNH của kênh =====
        ctv_data = db.load("ctv")
        is_channel_owner = (
            str(user_id) in ctv_data
            and cid_str in ctv_data[str(user_id)]
        )

        if not is_channel_owner:
            log.info(
                f"⏭️ User {user_id} không phải CTV chính của kênh {channel_id}, "
                f"skip DM chọn giờ live tiếp theo"
            )
            return

        # OK, gửi DM
        try:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            channel = self.bot.get_channel(int(channel_id))
            channel_name = channel.name if channel else f"Kênh {channel_id}"

            embed = get_custom_embed(
                EMBED_CTV_NEXT_LIVE,
                title="🕐 Đặt lịch Live tiếp theo",
                desc=(
                    f"Cảm ơn bạn đã live kênh **{channel_name}**!\n"
                    "Vui lòng chọn giờ Live tiếp theo để Bot nhắc bạn đúng lúc."
                ),
                color=discord.Color.blue(),
            )
            view = NextLiveView(channel_id)
            await user.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning(f"Không DM được CTV {user_id} về next live: {e}")
        except Exception:
            log.exception(f"Lỗi gửi NextLive DM cho user {user_id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveTrackerCog(bot))
