"""
Admin commands cog.

Lệnh chính: !setup — refresh cả 3 panel (Admin / User / Check CTV).
Dùng helpers/panels.refresh_panel() để atomic: xóa msg cũ + gửi msg mới + lưu ID.
"""
import logging

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
