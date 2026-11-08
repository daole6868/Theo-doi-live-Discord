"""
Entry point cho Gachaz Live Bot.

Chạy: python main.py
"""
import asyncio
import logging
import signal

import discord
from discord.ext import commands

from config import GUILD_ID, TOKEN
from core.logger import setup_logging
from helpers.migration import run_all_migrations
from helpers.stats import set_channel_name_resolver
from views.admin_view import AdminPanelView
from views.user_view import UserSelectView
from views.check_ctv_view import CheckCTVPanelView
from views.notify_view import NotifyOffButton, ReminderDelayButton
from views.ctv_view import OpenNextLiveModalButton


# ============ INIT LOGGING ============
setup_logging(level=logging.INFO)
log = logging.getLogger("main")


# ============ INTENTS ============
# Cần bật "Server Members Intent" trong Discord Developer Portal (xem DEPLOY.md).
# Không cần Message Content: lệnh /setup là lệnh gạch chéo.
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True


# ============ BOT CLASS ============
class GachazBot(commands.Bot):
    async def setup_hook(self) -> None:
        log.info("=" * 60)
        log.info("SETUP HOOK bắt đầu...")

        # 0. Migrations (chạy trước mọi thứ khác)
        run_all_migrations()

        # systemctl stop/restart gửi SIGTERM → tắt có kiểm soát (ghi nhịp tim cuối)
        try:
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGTERM, lambda: asyncio.create_task(self.close())
            )
        except (NotImplementedError, RuntimeError):
            pass  # Windows không hỗ trợ — bỏ qua

        # Báo cáo lấy tên kênh từ Discord cho kênh chưa lưu trong hệ thống
        set_channel_name_resolver(self._resolve_channel_name)

        # 1. Persistent views
        self.add_view(AdminPanelView())
        self.add_view(UserSelectView())
        self.add_view(CheckCTVPanelView())
        log.info("✓ Đăng ký 3 persistent view")

        # 2. Dynamic items (custom_id encode channel_id)
        self.add_dynamic_items(
            NotifyOffButton,
            ReminderDelayButton,
            OpenNextLiveModalButton,  # Modal nhập giờ live tiếp theo (persistent)
        )
        log.info("✓ Đăng ký 3 dynamic item (NotifyOff, ReminderDelay, NextLive)")

        # 3. Load cogs
        for ext in (
            "cogs.admin_cog",
            "cogs.live_tracker_cog",
            "cogs.background_cog",
            "cogs.voice_alert_cog",  # MỚI (batch 5): nhắc CTV chưa live
        ):
            try:
                await self.load_extension(ext)
                log.info(f"✓ Load cog: {ext}")
            except Exception:
                log.exception(f"✗ Load cog {ext} thất bại")

        # 4. Đăng ký lệnh gạch chéo (/setup) — đồng bộ vào server để hiện ngay
        try:
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info(f"✓ Đồng bộ {len(synced)} lệnh gạch chéo")
        except Exception:
            log.exception("✗ Đồng bộ lệnh gạch chéo thất bại")

        log.info("SETUP HOOK xong.")
        log.info("=" * 60)

    def _resolve_channel_name(self, channel_id: str) -> str | None:
        try:
            ch = self.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        return getattr(ch, "name", None)

    async def close(self) -> None:
        # Tắt bot có kiểm soát (restart/stop) → ghi nhịp tim lần cuối để
        # lần khởi động sau chốt các phiên đúng thời điểm bot dừng.
        tracker = self.get_cog("LiveTrackerCog")
        if tracker is not None:
            try:
                tracker.write_final_heartbeat()
            except Exception:
                log.exception("Ghi nhịp tim cuối lỗi")
        await super().close()


bot = GachazBot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)


# ============ GLOBAL ERROR HANDLER ============
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    log.exception(f"Lỗi lệnh /{interaction.command.name if interaction.command else '?'}: {error}")
    msg = "⚠️ Có lỗi khi chạy lệnh, xem log của bot."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ============ RUN ============
if __name__ == "__main__":
    try:
        bot.run(TOKEN, log_handler=None)
    except KeyboardInterrupt:
        log.info("Bot dừng bởi người dùng (Ctrl+C).")
    except discord.LoginFailure:
        log.error("❌ Token sai! Kiểm tra DISCORD_TOKEN trong .env.")
    except Exception:
        log.exception("❌ Bot crash không xử lý được.")
