"""
Admin Dashboard: Thêm/Quản lý CTV/Kênh + Sửa Embed.

Persistent view — sau restart vẫn hoạt động (nhờ custom_id cố định).

Layout:
  Row 0: [➕ Thêm CTV]   [➕ Thêm Kênh]
  Row 1: [🔧 Quản lý CTV] [🔧 Quản lý Kênh]
  Row 2: Dropdown chọn Embed để sửa (8 options)
"""
import logging

import discord
from discord import ui

from helpers.permissions import deny_if_not_admin
from views.embed_editor import EmbedEditorView, build_preview_embed

log = logging.getLogger(__name__)


class AdminPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Chỉ Administrator hoặc role quản lý (ADMIN_ROLE_ID) mới bấm được."""
        if await deny_if_not_admin(interaction):
            return False
        return True

    # =====================================================
    #  ROW 0: THÊM
    # =====================================================
    @ui.button(
        label="➕ Thêm CTV",
        style=discord.ButtonStyle.green,
        custom_id="admin_add_ctv",
        row=0,
    )
    async def add_ctv(self, interaction: discord.Interaction, button: ui.Button):
        from views.add_flow_views import AddCTVChannelSelectView

        embed = discord.Embed(
            title="➕ Thêm CTV",
            description=(
                "**Bước 1/2:** Chọn kênh mà bạn muốn thêm CTV (gõ tên để tìm).\n\n"
                "Sau đó chọn thành viên làm CTV."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=AddCTVChannelSelectView(),
            ephemeral=True,
        )

    @ui.button(
        label="➕ Thêm Kênh",
        style=discord.ButtonStyle.blurple,
        custom_id="admin_add_channel",
        row=0,
    )
    async def add_channel(self, interaction: discord.Interaction, button: ui.Button):
        from views.add_flow_views import AddChannelChoiceView

        embed = discord.Embed(
            title="➕ Thêm Kênh",
            description=(
                "Bạn muốn thêm kênh bằng cách nào?\n\n"
                "🆕 **Tạo Kênh Mới** — Bot sẽ tạo Voice Channel + Role tự động\n"
                "📎 **Thêm Kênh Sẵn Có** — Nhập ID kênh voice đã tồn tại, Bot tự lấy tên"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=AddChannelChoiceView(),
            ephemeral=True,
        )

    # =====================================================
    #  ROW 1: QUẢN LÝ (xóa/sửa)
    # =====================================================
    @ui.button(
        label="🔧 Quản lý CTV",
        style=discord.ButtonStyle.secondary,
        custom_id="admin_manage_ctv",
        row=1,
    )
    async def manage_ctv(self, interaction: discord.Interaction, button: ui.Button):
        from views.manage_flow_views import ManageCTVSelectView

        embed = discord.Embed(
            title="🔧 Quản lý CTV",
            description=(
                "Chọn CTV cần quản lý.\n\n"
                "Với mỗi CTV bạn có thể:\n"
                "• **Bỏ khỏi 1 kênh cụ thể** (thu hồi role kênh đó)\n"
                "• **Xóa CTV hoàn toàn** (bỏ khỏi tất cả kênh + thu hồi mọi role)"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ManageCTVSelectView(),
            ephemeral=True,
        )

    @ui.button(
        label="🔧 Quản lý Kênh",
        style=discord.ButtonStyle.secondary,
        custom_id="admin_manage_channel",
        row=1,
    )
    async def manage_channel(self, interaction: discord.Interaction, button: ui.Button):
        from views.manage_flow_views import ManageChannelSelectView

        embed = discord.Embed(
            title="🔧 Quản lý Kênh",
            description=(
                "Chọn kênh cần quản lý.\n\n"
                "Với mỗi kênh bạn có thể:\n"
                "• **✏️ Đổi tên** (đồng bộ cả voice channel Discord)\n"
                "• **🗑️ Xóa khỏi Bot** (giữ voice channel + role Discord)\n"
                "• **❌ Xóa hoàn toàn** (xóa cả voice channel + role Discord)"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ManageChannelSelectView(),
            ephemeral=True,
        )

    @ui.button(
        label="🔊 Cài đặt Voice Alert",
        style=discord.ButtonStyle.primary,
        custom_id="admin_voice_alert_settings",
        row=2,
    )
    async def voice_alert_settings(self, interaction: discord.Interaction, button: ui.Button):
        from views.voice_settings_view import (
            VoiceAlertSettingsView, build_settings_preview,
        )

        preview = build_settings_preview()
        view = VoiceAlertSettingsView()
        await interaction.response.send_message(
            content=(
                "**🔊 Cài đặt Nhắc CTV vào Voice chưa Live**\n"
                "Bot sẽ tự vào kênh voice phát giọng nói nhắc CTV, "
                "nếu quá hạn vẫn chưa Live → tự kick khỏi voice."
            ),
            embed=preview,
            view=view,
            ephemeral=True,
        )

    @ui.button(
        label="📢 Gửi Voice",
        style=discord.ButtonStyle.primary,
        custom_id="admin_broadcast_voice",
        row=2,
    )
    async def broadcast_voice(self, interaction: discord.Interaction, button: ui.Button):
        from views.broadcast_view import BroadcastView, build_broadcast_preview

        view = BroadcastView()
        preview = build_broadcast_preview(view.state)
        await interaction.response.send_message(
            content=(
                "**📢 Broadcast Voice — Phát thông báo TTS vào 1 kênh voice**\n"
                "Chỉnh nội dung + âm lượng + chọn kênh, sau đó bấm **Gửi ngay**.\n"
                "Bấm **Lưu cấu hình** để giữ nội dung + âm lượng cho lần sau."
            ),
            embed=preview,
            view=view,
            ephemeral=True,
        )

    # =====================================================
    #  ROW 3: DROPDOWN SỬA EMBED
    # =====================================================
    @ui.select(
        placeholder="Chọn Embed để sửa nội dung...",
        custom_id="admin_edit_embed_select",
        row=3,
        options=[
            discord.SelectOption(
                label="1. Bảng chọn kênh (khách)",
                value="embed_main_select",
                description="Embed có dropdown chọn kênh",
            ),
            discord.SelectOption(
                label="2. Khi khách chọn — LIVE 🔴",
                value="embed_customer_live",
                emoji="🔴",
                description="Hiển thị khi CTV đang LIVE",
            ),
            discord.SelectOption(
                label="3. Khi khách chọn — OFFLINE ⚫",
                value="embed_customer_offline",
                emoji="⚫",
                description="Hiển thị khi kênh OFF",
            ),
            discord.SelectOption(
                label="4. Thông báo Live gửi khách",
                value="embed_live_notif",
                description="DM khi CTV bắt đầu live",
            ),
            discord.SelectOption(
                label="5. Khu vực CTV",
                value="embed_ctv_info",
                description="Hiện khi CTV chọn kênh mình",
            ),
            discord.SelectOption(
                label="6. Nhắc CTV chọn giờ",
                value="embed_ctv_next_live",
                description="DM sau khi CTV tắt live",
            ),
            discord.SelectOption(
                label="7. Báo thức CTV đến giờ live",
                value="embed_ctv_reminder",
                description="DM khi đến giờ hẹn",
            ),
            discord.SelectOption(
                label="8. Bảng Check CTV",
                value="embed_check_ctv_panel",
                description="Dashboard admin check thời gian",
            ),
        ],
    )
    async def edit_embed_select(self, interaction: discord.Interaction, select: ui.Select):
        embed_key = select.values[0]

        preview = build_preview_embed(embed_key)
        view = EmbedEditorView(embed_key)

        await interaction.response.send_message(
            content=(
                f"**📝 Đang sửa Embed:** `{embed_key}`\n"
                "👇 Xem preview và bấm nút để sửa từng phần. "
                "Preview sẽ tự cập nhật ngay sau khi bạn lưu."
            ),
            embed=preview,
            view=view,
            ephemeral=True,
        )

        try:
            await interaction.message.edit(view=AdminPanelView())
        except Exception:
            log.exception("Reset admin dropdown lỗi")
