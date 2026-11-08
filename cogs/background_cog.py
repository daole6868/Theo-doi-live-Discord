"""
4 Background Tasks:

1. midnight_cutoff (00:00 VN) — cắt session vắt qua ngày, ghi phần hôm qua vào history.
2. daily_leaderboard (00:05 VN) — gửi BXH ngày hôm qua vào LEADERBOARD_CHANNEL.
3. cleanup_old_sessions (02:00 VN) — xóa session cũ hơn SESSION_RETENTION_DAYS ngày.
4. check_reminders (mỗi phút) — quét next_live, gửi báo thức, alert admin nếu CTV trễ.

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
    SESSION_RETENTION_DAYS,
    LATE_REMINDER_MAX,
    REMINDER_INTERVAL_MINUTES,
    MIN_SESSION_SECONDS,
    EMBED_CTV_REMINDER,
)
from core.data_manager import db
from core.state import state
from helpers.embeds import get_custom_embed
from helpers.stats import build_leaderboard, build_daily_leaderboard_embed
from helpers.time_utils import now_vn, fmt_dur, start_of_day_vn
from views.notify_view import ReminderView

log = logging.getLogger(__name__)


# ============ SCHEDULED TIMES (giờ VN) ============
MIDNIGHT_TIME = datetime.time(hour=0, minute=0, tzinfo=VN_TZ)
LEADERBOARD_TIME = datetime.time(hour=0, minute=5, tzinfo=VN_TZ)
CLEANUP_TIME = datetime.time(hour=2, minute=0, tzinfo=VN_TZ)


class BackgroundCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.midnight_cutoff.start()
        self.daily_leaderboard.start()
        self.cleanup_old_sessions.start()
        self.check_reminders.start()

    def cog_unload(self):
        self.midnight_cutoff.cancel()
        self.daily_leaderboard.cancel()
        self.cleanup_old_sessions.cancel()
        self.check_reminders.cancel()

    # =====================================================
    #  TASK 1: MIDNIGHT CUTOFF (00:00)
    # =====================================================
    @tasks.loop(time=MIDNIGHT_TIME)
    async def midnight_cutoff(self):
        try:
            log.info("[TASK] midnight_cutoff bắt đầu")
            now = now_vn()
            cutoff = start_of_day_vn(now)  # 00:00 hôm nay

            for cid_str in state.snapshot().keys():
                start_dt = state.get_start(cid_str)
                user_id = state.get_user(cid_str) or ""
                if start_dt is None:
                    continue

                if start_dt < cutoff:
                    duration = (cutoff - start_dt).total_seconds()
                    if duration >= MIN_SESSION_SECONDS:
                        # Capture theo default args để tránh closure bug
                        def _save(sessions, cid=cid_str, s=start_dt, e=cutoff,
                                  d=duration, u=user_id):
                            sessions.setdefault(cid, []).append({
                                "start_time": s.timestamp(),
                                "end_time": e.timestamp(),
                                "duration": int(d),
                                "user_id": str(u),
                            })
                        await db.aupdate("sessions", _save)

                    # Reset mốc bắt đầu về 00:00 để tính tiếp cho hôm nay
                    state.reset_start(cid_str, cutoff)
                    log.info(
                        f"Cắt session kênh {cid_str} tại 00:00, "
                        f"{int(duration)}s ghi cho hôm qua"
                    )
        except Exception:
            log.exception("[TASK] midnight_cutoff lỗi")

    @midnight_cutoff.before_loop
    async def _wait_midnight(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  TASK 2: DAILY LEADERBOARD (00:05) — BXH theo CTV
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
    #  TASK 3: CLEANUP OLD SESSIONS (02:00)
    # =====================================================
    @tasks.loop(time=CLEANUP_TIME)
    async def cleanup_old_sessions(self):
        try:
            log.info("[TASK] cleanup_old_sessions bắt đầu")
            cutoff = now_vn() - datetime.timedelta(days=SESSION_RETENTION_DAYS)
            cutoff_ts = cutoff.timestamp()
            removed_total = 0

            def _save(sessions):
                nonlocal removed_total
                for cid, lst in list(sessions.items()):
                    if not isinstance(lst, list):
                        continue
                    kept = []
                    for s in lst:
                        ts = s.get("start_time", s.get("start"))
                        if ts and float(ts) >= cutoff_ts:
                            kept.append(s)
                        else:
                            removed_total += 1
                    sessions[cid] = kept

            await db.aupdate("sessions", _save)
            log.info(f"Cleanup xóa {removed_total} session cũ > {SESSION_RETENTION_DAYS} ngày")
        except Exception:
            log.exception("[TASK] cleanup_old_sessions lỗi")

    @cleanup_old_sessions.before_loop
    async def _wait_cleanup(self):
        await self.bot.wait_until_ready()

    # =====================================================
    #  TASK 4: CHECK REMINDERS (mỗi phút)
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
                if state.is_live(cid_str):
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
