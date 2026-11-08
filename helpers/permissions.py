"""Kiểm tra quyền dùng các chức năng quản trị."""
import discord

from config import ADMIN_ROLE_ID


def is_admin(member) -> bool:
    """Administrator, hoặc có role quản lý ADMIN_ROLE_ID (.env)."""
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    if ADMIN_ROLE_ID:
        return any(getattr(r, "id", None) == ADMIN_ROLE_ID for r in getattr(member, "roles", []) or [])
    return False


async def deny_if_not_admin(interaction: discord.Interaction) -> bool:
    """Trả về True (và báo lỗi cho người bấm) nếu KHÔNG phải admin."""
    if is_admin(interaction.user):
        return False
    msg = "⛔ Chỉ Admin (hoặc role quản lý) mới dùng được chức năng này."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    return True
