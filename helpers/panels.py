"""
Panel management — save/refresh panel messages.

Sau khi Admin thêm kênh, thêm CTV, hoặc sửa embed liên quan,
gọi refresh_panel() để xóa msg cũ và gửi lại msg mới với data hiện tại.

Panels:
- admin:     Dashboard admin ở ADMIN_CHANNEL_ID
- user:      Dropdown chọn kênh ở USER_CHANNEL_ID
- check_ctv: Dashboard check CTV ở CHECK_CTV_CHANNEL_ID
"""
import logging

import discord

from config import (
    ADMIN_CHANNEL_ID,
    USER_CHANNEL_ID,
    CHECK_CTV_CHANNEL_ID,
    EMBED_MAIN_SELECT,
    EMBED_CHECK_CTV_PANEL,
)
from core.data_manager import db
from helpers.embeds import get_custom_embed

log = logging.getLogger(__name__)


# Tăng số này khi cấu trúc panel thay đổi → bot tự gửi lại panel 1 lần khi khởi động
# (v2: panel khách chia nhiều menu; v3: Check CTV liệt kê sẵn CTV đã lưu kèm tên)
PANEL_VERSION = "3"

# Panel name → channel_id từ .env
PANEL_CHANNELS = {
    "admin":     ADMIN_CHANNEL_ID,
    "user":      USER_CHANNEL_ID,
    "check_ctv": CHECK_CTV_CHANNEL_ID,
}


def _build_panel(panel_name: str, guild=None):
    """Build (embed, view) cho từng loại panel. Import lazy để tránh circular."""
    from views.admin_view import AdminPanelView
    from views.user_view import UserSelectView
    from views.check_ctv_view import CheckCTVPanelView

    if panel_name == "admin":
        embed = discord.Embed(
            title="⚙️ Admin Dashboard",
            description=(
                "Sử dụng các nút và menu bên dưới để quản lý Kênh, CTV, "
                "và các nội dung Embed."
            ),
            color=discord.Color.orange(),
        )
        return embed, AdminPanelView()

    elif panel_name == "user":
        embed = get_custom_embed(
            EMBED_MAIN_SELECT,
            title="📺 CHỌN KÊNH THEO DÕI",
            desc="Vui lòng chọn kênh bên dưới để xem trạng thái và nhận thông báo.",
        )
        return embed, UserSelectView()

    elif panel_name == "check_ctv":
        embed = get_custom_embed(
            EMBED_CHECK_CTV_PANEL,
            title="📊 CHECK THỜI GIAN LIVE CTV",
            desc=(
                "Chọn CTV từ menu bên dưới để xem lịch sử live theo khung:\n"
                "**Hôm nay / 7 ngày / 15 ngày**"
            ),
            color=discord.Color.purple(),
        )
        return embed, CheckCTVPanelView(guild)

    return None, None


async def refresh_panel(bot, panel_name: str) -> bool:
    """
    Xóa panel message cũ và gửi lại panel mới với data hiện tại.

    Return True nếu refresh thành công, False nếu skip (chưa setup channel_id, kênh không tồn tại, v.v.)
    """
    channel_id = PANEL_CHANNELS.get(panel_name)
    if not channel_id:
        log.debug(f"Panel '{panel_name}' chưa cấu hình channel_id trong .env")
        return False

    channel = bot.get_channel(channel_id)
    if not channel:
        log.warning(f"Không tìm thấy channel {channel_id} cho panel '{panel_name}'")
        return False

    # Load info panel cũ
    panels = db.load("panel_messages")
    old_info = panels.get(panel_name, {})
    old_msg_id = old_info.get("message_id")

    # Xóa msg cũ (nếu có)
    if old_msg_id:
        try:
            old_msg = await channel.fetch_message(int(old_msg_id))
            await old_msg.delete()
            log.info(f"🗑️ Xóa panel cũ '{panel_name}' (msg={old_msg_id})")
        except discord.NotFound:
            pass
        except discord.Forbidden:
            log.warning(f"Bot không có quyền xóa msg {old_msg_id}")
        except Exception:
            log.exception(f"Xóa msg {old_msg_id} lỗi")

    # Build và gửi msg mới
    embed, view = _build_panel(panel_name, getattr(channel, "guild", None))
    if embed is None:
        return False

    try:
        new_msg = await channel.send(embed=embed, view=view)
    except Exception:
        log.exception(f"Gửi panel '{panel_name}' mới lỗi")
        return False

    # Lưu message_id mới
    def _save(data):
        data[panel_name] = {
            "channel_id": channel_id,
            "message_id": new_msg.id,
        }

    await db.aupdate("panel_messages", _save)
    log.info(f"✅ Refresh panel '{panel_name}' → msg {new_msg.id} @ #{channel.name}")
    return True


async def resync_tracking(bot, reason: str) -> None:
    """Danh sách CTV vừa đổi → đối chiếu lại bộ tính giờ ngay (không chờ 5 phút)."""
    cog = bot.get_cog("LiveTrackerCog")
    if cog is None:
        return
    try:
        await cog.sync_all(reason)
    except Exception:
        log.exception("Đối chiếu bộ tính giờ lỗi")
