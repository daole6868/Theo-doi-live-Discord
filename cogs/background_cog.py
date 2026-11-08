"""
Background Tasks:

1. daily_leaderboard (00:05 VN) — gửi BXH ngày hôm qua vào LEADERBOARD_CHANNEL.
2. check_reminders (mỗi phút) — quét next_live, gửi báo thức, alert admin nếu CTV trễ.

Đã bỏ:
- midnight_cutoff: phiên vắt qua đêm giờ được cắt theo ngày ngay lúc lưu (core/session_store.py).
- cleanup_old_sessions: lịch sử được giữ vĩnh viễn trong SQLite.

Tất cả tasks đều wrap try/except để không crash bot khi lỗi.
"""
import datetime
import logging

import discord
from discord.ext import commands, tasks

from config import (
    VN_TZ,
    GUILD_ID,
    LEADERBOARD_CHANNEL_ID,
    LATE_ALERT_CHANNEL_ID,
    LATE_REMINDER_MAX,
    REMINDER_INTERVAL_MINUTES,
    EMBED_CTV_REMINDER,
)
from core.data_manager import db
from core.state import state
from helpers.embeds import get_custom_embed
from helpers.stats import build_daily_leaderboard_embed
from helpers.time_utils import now_vn
from views.notify_view import ReminderView

log = logging.getLogger(__name__)


# ============ SCHEDULED TIMES (giờ VN) ============
LEADERBOARD_TIME = datetime.time(hour=0, minute=5, tzinfo=VN_TZ)


class BackgroundCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_leaderboard.start()
        self.check_reminders.start()

    def cog_unload(self):
        self.daily_leaderboard.cancel()
        self.check_reminders.cancel()

    # =====================================================
    #  TASK 1: DAILY LEADERBOARD (00:05) — BXH theo CTV
    # =====================================================
    @tasks.loop(time=LEADERBOARD_TIME)
    async def daily_leaderboard(self):
        try:
            log.info("[TASK] daily_leaderboard bắt đầu")
            channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
            if not channel:
                log.warning(f"LEADERBOARD_CHANNEL_ID={LEADERBOARD_CHANNEL_ID} không tìm thấy")
                return

            yesterday = (now_vn() - datetime.timedelta(days=1)).date()
            embed = build_daily_leaderboard_embed(yesterday)

            await channel.send(embed=embed)
            log.info(f"Đã gửi BXH CTV ngày {yesterday}")
        except Exception:
            log.exception("[TASK] daily_leaderboard lỗi")

    @daily_leaderboard.before_loop
    async def _wait_leaderboard(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  TASK 2: CHECK REMINDERS (mỗi phút)
    # =====================================================
    @tasks.loop(minutes=1)
    async def check_reminders(self):
        try:
            nl_data = db.load("next_live")
            if not nl_data:
                return

            now_ts = now_vn().timestamp()
            to_update: dict[str, int] = {}
            to_delete: list[str] = []

            for cid_str, info in nl_data.items():
                if not isinstance(info, dict):
                    continue

                # Đã live → không cần nhắc nữa
                if state.channel_has_stream(cid_str):
                    to_delete.append(cid_str)
                    continue

                target_ts = info.get("time")
                user_id = info.get("user_id")
                reminders_sent = info.get("reminders_sent", 0)

                if not target_ts or not user_id:
                    continue

                # Chưa tới giờ
                if now_ts < target_ts:
                    continue

                minutes_late = int((now_ts - target_ts) / 60)
                expected = min(
                    minutes_late // REMINDER_INTERVAL_MINUTES + 1,
                    LATE_REMINDER_MAX + 1,
                )

                # Chưa cần gửi thêm reminder
                if reminders_sent >= expected:
                    continue

                # Đã gửi đủ max → alert admin và bỏ
                if reminders_sent >= LATE_REMINDER_MAX:
                    await self._send_late_alert(cid_str, user_id, info.get("time_str", "?"))
                    to_delete.append(cid_str)
                    continue

                # Gửi reminder
                await self._send_reminder(
                    cid_str, user_id, info.get("time_str", "?"), reminders_sent + 1
                )
                to_update[cid_str] = reminders_sent + 1

            # Persist thay đổi
            if to_update or to_delete:
                def _apply(data):
                    for cid, new_count in to_update.items():
                        if cid in data and isinstance(data[cid], dict):
                            data[cid]["reminders_sent"] = new_count
                    for cid in to_delete:
                        data.pop(cid, None)

                await db.aupdate("next_live", _apply)
        except Exception:
            log.exception("[TASK] check_reminders lỗi")

    @check_reminders.before_loop
    async def _wait_reminders(self):
        await self.bot.wait_until_ready()

    # ---------- REMINDER SENDERS ----------
    async def _send_reminder(
        self, channel_id: str, user_id, time_str: str, count: int
    ):
        try:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            channel = self.bot.get_channel(int(channel_id))
            channel_name = channel.name if channel else f"Kênh {channel_id}"

            embed = get_custom_embed(
                EMBED_CTV_REMINDER,
                title=f"⏰ Nhắc Live #{count}",
                desc=(
                    f"Đã đến giờ Live kênh **{channel_name}**!\n"
                    f"Lịch: **{time_str}**\n\n"
                    "Vào kênh ngay hoặc chọn hoãn (nếu chưa sẵn sàng)."
                ),
                color=discord.Color.orange(),
            )
            view = ReminderView(channel_id, GUILD_ID)
            await user.send(embed=embed, view=view)
            log.info(f"Nhắc live CTV {user_id} kênh {channel_id} (lần {count}/{LATE_REMINDER_MAX})")
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning(f"Không DM được CTV {user_id}: {e}")
        except Exception:
            log.exception(f"Lỗi gửi reminder tới CTV {user_id}")

    async def _send_late_alert(self, channel_id: str, user_id, time_str: str):
        try:
            channel = self.bot.get_channel(LATE_ALERT_CHANNEL_ID)
            if not channel:
                log.warning(f"LATE_ALERT_CHANNEL_ID={LATE_ALERT_CHANNEL_ID} không tìm thấy")
                return

            live_channel = self.bot.get_channel(int(channel_id))
            channel_name = live_channel.name if live_channel else f"Kênh {channel_id}"

            embed = discord.Embed(
                title="🚨 CTV ĐI TRỄ",
                description=(
                    f"CTV <@{user_id}> đã quá hạn Live kênh **{channel_name}**.\n"
                    f"Lịch: **{time_str}**\n"
                    f"Bot đã nhắc **{LATE_REMINDER_MAX}** lần cách nhau "
                    f"{REMINDER_INTERVAL_MINUTES} phút nhưng không thấy vào."
                ),
                color=discord.Color.red(),
            )
            await channel.send(embed=embed)
            log.warning(f"Alert late CTV {user_id} tại kênh {channel_id}")
        except Exception:
            log.exception("Send late alert lỗi")


async def setup(bot: commands.Bot):
    await bot.add_cog(BackgroundCog(bot))
