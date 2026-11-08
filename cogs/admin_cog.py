"""
Admin commands cog.

Lệnh chính: !setup — refresh cả 3 panel (Admin / User / Check CTV).
Dùng helpers/panels.refresh_panel() để atomic: xóa msg cũ + gửi msg mới + lưu ID.
"""
import logging

from discord.ext import commands

from helpers.panels import refresh_panel, PANEL_CHANNELS

log = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="setup")
    @commands.has_permissions(administrator=True)
    async def setup_cmd(self, ctx: commands.Context):
        """Post/refresh lại toàn bộ 3 panel."""
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

        await ctx.send("**Setup kết quả:**\n" + "\n".join(results))

    @setup_cmd.error
    async def _setup_err(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("⛔ Bạn cần quyền **Administrator** để dùng lệnh này.")
        else:
            log.exception("!setup command error")
            await ctx.send(f"⚠️ Lỗi: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
