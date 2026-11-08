"""
Admin commands cog.

Lệnh chính: /setup — gửi lại cả 3 panel (Admin / User / Check CTV).
Dùng helpers/panels.refresh_panel() để atomic: xóa msg cũ + gửi msg mới + lưu ID.

Dùng lệnh gạch chéo (slash command) nên bot KHÔNG cần intent Message Content.
Chỉ người có quyền Administrator mới thấy lệnh này.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.session_store import store
from helpers.panels import PANEL_CHANNELS, PANEL_VERSION, refresh_panel

log = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._panels_checked = False

    @commands.Cog.listener()
    async def on_ready(self):
        """Sau khi nâng cấp bot (panel đổi cấu trúc) → tự gửi lại các panel 1 lần."""
        if self._panels_checked:
            return
        self._panels_checked = True
        if store.get_meta("panel_version") == PANEL_VERSION:
            return
        for panel_name, channel_id in PANEL_CHANNELS.items():
            if not channel_id:
                continue
            try:
                await refresh_panel(self.bot, panel_name)
            except Exception:
                log.exception(f"Tự làm mới panel {panel_name} lỗi")
        store.set_meta("panel_version", PANEL_VERSION)
        log.info(f"✓ Đã làm mới panel lên phiên bản {PANEL_VERSION}")

    @app_commands.command(name="setup", description="Gửi lại các panel của bot (Admin / Khách / Check CTV)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_cmd(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "⛔ Bạn cần quyền **Administrator** để dùng lệnh này.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        results: list[str] = []

        for panel_name in ("admin", "user", "check_ctv"):
            channel_id = PANEL_CHANNELS.get(panel_name, 0)
            if not channel_id:
                results.append(f"ℹ️ **{panel_name}**: chưa cấu hình `{panel_name.upper()}_CHANNEL_ID` trong .env")
                continue
            try:
                ok = await refresh_panel(self.bot, panel_name)
                if ok:
                    results.append(f"✅ **{panel_name}** panel → <#{channel_id}>")
                else:
                    results.append(f"⚠️ **{panel_name}** panel: FAIL (kiểm tra log)")
            except Exception as e:
                log.exception(f"Refresh panel {panel_name} lỗi")
                results.append(f"⚠️ **{panel_name}** panel: {e}")

        await interaction.followup.send("**Setup kết quả:**\n" + "\n".join(results), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
