"""
Entry point cho Gachaz Live Bot.

Chạy: python main.py
"""
import logging

import discord
from discord.ext import commands

from config import TOKEN
from core.logger import setup_logging
from helpers.migration import run_all_migrations
from views.admin_view import AdminPanelView
from views.user_view import UserSelectView
from views.check_ctv_view import CheckCTVPanelView
from views.notify_view import NotifyOffButton, ReminderDelayButton
from views.ctv_view import OpenNextLiveModalButton


# ============ INIT LOGGING ============
setup_logging(level=logging.INFO)
log = logging.getLogger("main")


# ============ INTENTS ============
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True


# ============ BOT CLASS ============
class GachazBot(commands.Bot):
    async def setup_hook(self) -> None:
        log.info("=" * 60)
        log.info("SETUP HOOK bắt đầu...")

        # 0. Migrations (chạy trước mọi thứ khác)
        run_all_migrations()

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

        log.info("SETUP HOOK xong.")
        log.info("=" * 60)


bot = GachazBot(command_prefix="!", intents=intents, help_command=None)


# ============ GLOBAL ERROR HANDLER ============
@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    log.exception(f"Command error trong '{ctx.command}': {error}")


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
