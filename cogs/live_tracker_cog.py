"""
Live Tracker Cog — tính giờ LIVE (chia sẻ màn hình) và AUDIO cho từng CTV.

- Mỗi sự kiện voice của 1 CTV → `Tracker.apply()` đưa đoạn đang chạy về đúng trạng thái.
- Khởi động: chốt các đoạn cũ tại nhịp tim cuối cùng (không tính lúc bot offline),
  rồi mở đoạn mới cho ai đang ở trong voice.
- Mất kết nối Discord rồi vào lại: đối chiếu, đoạn nào lệch thì chốt tại lúc mất kết nối.
- Định kỳ 5 phút: đối chiếu lại để bắt các sự kiện bị lỡ.
- Nhịp tim mỗi 30 giây.

Khi CTV bắt đầu LIVE (ở bất kỳ kênh nào): hủy lịch nhắc của chính CTV đó và báo
ngay cho khách đăng ký các kênh CTV đó quản lý (giới hạn tần suất bằng
NOTIFY_COOLDOWN_MINUTES trong config.py, mặc định 0 = báo mỗi lần bật live).
Khi CTV tắt LIVE: chờ 2 phút, nếu không bật lại thì DM hỏi giờ live tiếp theo.
"""
import asyncio
import logging

import discord
from discord.ext import commands, tasks

from config import (
    EMBED_CTV_NEXT_LIVE,
    EMBED_LIVE_NOTIF,
    HEARTBEAT_SECONDS,
    KIND_STREAM,
    LEGACY_LIVE_MAX_HOURS,
    NEXT_LIVE_PROMPT_DELAY_SECONDS,
    NOTIFY_COOLDOWN_MINUTES,
    RECONCILE_MINUTES,
    is_main_guild,
)
from core.data_manager import db
from core.session_store import store
from core.state import state
from core.tracker import Change, Tracker, desired_from_voice
from helpers.embeds import clamp_embed, get_custom_embed
from helpers.stats import channel_name
from helpers.time_utils import fmt_dur, now_ts
from views.ctv_view import NextLiveView
from views.notify_view import NotifyView

log = logging.getLogger(__name__)


class LiveTrackerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tracker = Tracker(state, store)
        self._lock = asyncio.Lock()
        self._started = False          # đã chạy đối chiếu khởi động chưa
        self._connected = False
        self._disconnected_at: float | None = None
        # user_id → ts lần cuối báo live cho khách (chống spam khi tắt/bật Go Live)
        self._last_notified: dict[str, float] = {}
        # user_id → (channel_id đã live, thời điểm sẽ hỏi giờ live tiếp theo)
        self._pending_prompts: dict[str, tuple[str, float]] = {}

    async def cog_load(self):
        # Đọc state trước khi nhận sự kiện (sự kiện bị bỏ qua cho đến khi đối chiếu xong)
        state.load_from_disk()
        self.heartbeat.start()
        self.periodic_reconcile.start()
        self.next_live_prompts.start()

    async def cog_unload(self):
        self.heartbeat.cancel()
        self.periodic_reconcile.cancel()
        self.next_live_prompts.cancel()

    # =====================================================
    #  TRẠNG THÁI THỰC TẾ TỪ DISCORD
    # =====================================================
    def collect_desired(self) -> dict[str, tuple[str, str]]:
        """{user_id: (kind, channel_id)} cho mọi CTV đang ở trong voice."""
        ctv_data = db.load("ctv")
        desired: dict[str, tuple[str, str]] = {}
        for guild in self.bot.guilds:
            if not is_main_guild(guild):
                continue
            for channel in list(guild.voice_channels) + list(guild.stage_channels):
                for member in channel.members:
                    if member.bot or str(member.id) not in ctv_data:
                        continue
                    d = desired_from_voice(member.voice, guild)
                    if d:
                        desired[str(member.id)] = d
        return desired

    async def sync_all(self, reason: str) -> None:
        """Đối chiếu toàn bộ (dùng sau khi vào lại / định kỳ / admin đổi danh sách CTV)."""
        async with self._lock:
            if not self._started:
                return
            changes = self.tracker.reconcile(self.collect_desired(), now_ts())
        if changes:
            log.info(f"[sync:{reason}] {len(changes)} thay đổi")
        await self._handle_changes(changes, from_event=False)

    # =====================================================
    #  KẾT NỐI
    # =====================================================
    @commands.Cog.listener()
    async def on_ready(self):
        async with self._lock:
            now = now_ts()
            desired = self.collect_desired()
            if not self._started:
                hb_raw = store.get_meta("heartbeat")
                last_hb = float(hb_raw) if hb_raw else None
                changes = self.tracker.startup(
                    desired, now, last_hb, LEGACY_LIVE_MAX_HOURS * 3600,
                )
                self._started = True
                log.info(
                    f"✅ Bot đã đăng nhập: {self.bot.user} — đối chiếu khởi động: "
                    f"{len(changes)} thay đổi, {len(state.user_ids())} CTV đang trong voice"
                )
            else:
                changes = self.tracker.reconcile(desired, now, end_ts=self._disconnected_at)
                log.info(f"🔄 Kết nối lại (ready) — đối chiếu: {len(changes)} thay đổi")
            self._connected = True
            self._disconnected_at = None
            store.set_meta("heartbeat", now)
        await self._handle_changes(changes, from_event=False)

    @commands.Cog.listener()
    async def on_resumed(self):
        async with self._lock:
            if not self._started:
                return
            changes = self.tracker.reconcile(
                self.collect_desired(), now_ts(), end_ts=self._disconnected_at,
            )
            self._connected = True
            self._disconnected_at = None
            store.set_meta("heartbeat", now_ts())
        if changes:
            log.info(f"🔄 Kết nối lại (resume) — đối chiếu: {len(changes)} thay đổi")
        await self._handle_changes(changes, from_event=False)

    @commands.Cog.listener()
    async def on_disconnect(self):
        if self._connected:
            self._connected = False
            self._disconnected_at = now_ts()
            log.warning("⚠️ Mất kết nối Discord — tạm dừng tính giờ cho đến khi vào lại")

    # =====================================================
    #  SỰ KIỆN VOICE
    # =====================================================
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or not self._started or not is_main_guild(member.guild):
            return

        uid = str(member.id)
        is_ctv = uid in db.load("ctv")
        if not is_ctv and state.get(uid) is None:
            return  # Không phải CTV, không có gì để chốt

        desired = desired_from_voice(after, member.guild) if is_ctv else None
        async with self._lock:
            changes = self.tracker.apply(uid, desired, now_ts())
        await self._handle_changes(changes, from_event=True)

    def write_final_heartbeat(self) -> None:
        if self._started and self._connected:
            store.set_meta("heartbeat", now_ts())

    # =====================================================
    #  TÁC VỤ NỀN
    # =====================================================
    @tasks.loop(seconds=HEARTBEAT_SECONDS)
    async def heartbeat(self):
        if self._started and self._connected:
            try:
                store.set_meta("heartbeat", now_ts())
            except Exception:
                log.exception("Ghi nhịp tim lỗi")

    @tasks.loop(minutes=RECONCILE_MINUTES)
    async def periodic_reconcile(self):
        if not (self._started and self._connected):
            return
        try:
            await self.sync_all("định kỳ")
        except Exception:
            log.exception("Đối chiếu định kỳ lỗi")

    @tasks.loop(seconds=30)
    async def next_live_prompts(self):
        """Gửi DM hỏi giờ live tiếp theo cho CTV đã tắt live đủ 2 phút."""
        now = now_ts()
        due = [(uid, cid) for uid, (cid, at) in self._pending_prompts.items() if at <= now]
        for uid, cid in due:
            self._pending_prompts.pop(uid, None)
            if state.is_streaming(uid):
                continue  # Đã bật lại live → chưa cần hỏi
            try:
                await self._send_next_live_prompt(uid, cid)
            except Exception:
                log.exception(f"Gửi DM hỏi giờ live tiếp theo cho {uid} lỗi")

    @heartbeat.before_loop
    @periodic_reconcile.before_loop
    @next_live_prompts.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  XỬ LÝ SAU KHI THAY ĐỔI
    # =====================================================
    async def _handle_changes(self, changes: list[Change], from_event: bool) -> None:
        if not changes:
            return
        # Ai vừa bị chốt stream rồi mở lại stream trong cùng đợt đối chiếu
        # (vd. đoạn bị cắt do bot restart) → không phải bắt đầu/kết thúc live thật.
        stopped_streams = {c.user_id for c in changes if c.action == "stop" and c.kind == KIND_STREAM}
        started_streams = {c.user_id for c in changes if c.action == "start" and c.kind == KIND_STREAM}

        for c in changes:
            name = channel_name(c.channel_id)
            if c.action == "stop":
                level = logging.INFO if c.kind == KIND_STREAM else logging.DEBUG
                log.log(level, f"⚫ STOP {c.kind}: {c.user_id} @ {name}, lưu {fmt_dur(c.saved_seconds)}"
                               f"{' (ước tính)' if c.flags else ''}")
                if c.kind == KIND_STREAM and c.user_id not in started_streams:
                    try:
                        await self._on_stream_end(c)
                    except Exception:
                        log.exception(f"Xử lý kết thúc live {c.user_id} lỗi")
            else:
                level = logging.INFO if c.kind == KIND_STREAM else logging.DEBUG
                log.log(level, f"🔴 START {c.kind}: {c.user_id} @ {name}")
                if c.kind == KIND_STREAM and (from_event or c.user_id not in stopped_streams):
                    try:
                        await self._on_stream_start(c)
                    except Exception:
                        log.exception(f"Xử lý bắt đầu live {c.user_id} lỗi")

    async def _on_stream_start(self, c: Change) -> None:
        """CTV bắt đầu LIVE: hủy lịch nhắc của chính CTV đó, báo cho khách đăng ký."""
        self._pending_prompts.pop(c.user_id, None)

        def _remove_next(data):
            data.pop(c.user_id, None)

        await db.aupdate("next_live", _remove_next)

        last = self._last_notified.get(c.user_id)
        if NOTIFY_COOLDOWN_MINUTES > 0 and last is not None and c.start_ts - last < NOTIFY_COOLDOWN_MINUTES * 60:
            log.info(f"⏭️ Bỏ qua DM báo live cho khách của {c.user_id} (đã báo trong {NOTIFY_COOLDOWN_MINUTES} phút)")
            return

        channel = self.bot.get_channel(int(c.channel_id))
        member = channel.guild.get_member(int(c.user_id)) if channel else None
        if channel is None or member is None:
            return
        self._last_notified[c.user_id] = c.start_ts
        await self._notify_subscribers(channel, member)

    async def _notify_subscribers(self, channel, member: discord.Member) -> None:
        """
        DM cho khách đăng ký BẤT KỲ kênh nào mà CTV này quản lý (mỗi khách 1 tin).
        CTV live ở kênh khác → link dẫn tới kênh đang live thực tế.
        Nút "Tắt Thông Báo" trong DM gắn theo CTV → tắt đúng các kênh khách đã đăng ký.
        """
        ctv_data = db.load("ctv")
        channels_data = db.load("channels")
        managed_channels = [str(c) for c in ctv_data.get(str(member.id), [])]
        subs_data = db.load("subscribers")

        # khách → các kênh (của CTV này) mà khách đã bật thông báo
        reasons: dict[str, list[str]] = {}
        for ch_id in managed_channels:
            for uid in subs_data.get(ch_id, []):
                reasons.setdefault(str(uid), []).append(ch_id)

        if not reasons:
            return

        base = get_custom_embed(
            EMBED_LIVE_NOTIF,
            title=f"🔴 CTV **{member.display_name}** đang LIVE!",
            desc=(
                f"Đang phát trực tiếp tại kênh **{channel.name}**.\n"
                "Vào xem ngay!"
            ),
            color=discord.Color.red(),
        )
        view = NotifyView(channel.id, channel.guild.id, ctv_id=member.id)

        sent = failed = 0
        for uid, chans in reasons.items():
            names = ", ".join(channels_data.get(c, {}).get("name", f"Kênh {c}") for c in chans)
            embed = base.copy()
            embed.description = (embed.description or "") + (
                f"\n\n*Bạn nhận tin này vì đã bật thông báo kênh: {names}.*"
            )
            try:
                user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                await user.send(embed=clamp_embed(embed), view=view)
                sent += 1
            except (discord.Forbidden, discord.HTTPException) as e:
                failed += 1
                log.debug(f"Không DM được user {uid}: {e}")
            except Exception:
                failed += 1
                log.exception(f"Lỗi gửi DM tới {uid}")

        log.info(
            f"Đã DM live tới {sent}/{len(reasons)} khách (lỗi: {failed}). "
            f"CTV {member.display_name} → {len(managed_channels)} kênh quản lý."
        )

    async def _on_stream_end(self, c: Change) -> None:
        """CTV tắt LIVE: hẹn 2 phút sau hỏi giờ live tiếp theo (nếu không bật lại)."""
        self._pending_prompts[c.user_id] = (c.channel_id, now_ts() + NEXT_LIVE_PROMPT_DELAY_SECONDS)

    async def _send_next_live_prompt(self, user_id: str, lived_channel_id: str) -> None:
        ctv_data = db.load("ctv")
        own = [str(x) for x in (ctv_data.get(user_id) or [])]
        if user_id not in ctv_data:
            return
        # Link "Live Ngay" trong tin nhắc: kênh vừa live nếu là kênh mình, không thì kênh mình đầu tiên
        link_cid = lived_channel_id if (lived_channel_id in own or not own) else own[0]

        user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        embed = get_custom_embed(
            EMBED_CTV_NEXT_LIVE,
            title="🕐 Đặt lịch Live tiếp theo",
            desc=(
                f"Cảm ơn bạn đã live tại kênh **{channel_name(lived_channel_id)}**!\n"
                "Vui lòng chọn giờ Live tiếp theo để Bot nhắc bạn đúng lúc."
            ),
            color=discord.Color.blue(),
        )
        try:
            await user.send(embed=embed, view=NextLiveView(link_cid))
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning(f"Không DM được CTV {user_id} về lịch live tiếp theo: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveTrackerCog(bot))
