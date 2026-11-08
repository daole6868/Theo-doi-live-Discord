"""
Embed Editor v2 — mỗi field 1 nút sửa, live preview.

Flow (yêu cầu #5 của bạn):
1. Admin chọn embed_key qua dropdown ở AdminPanelView
2. Bot gửi ephemeral: preview embed (y hệt user sẽ thấy) + 5 nút sửa từng phần
3. Bấm 1 nút → mở modal chỉ chứa 1 field
4. Submit → save + refresh preview ngay (không cần đóng mở lại)

Support (yêu cầu #6):
- Mọi embed đều có thể setup CẢ image (ảnh lớn) + thumbnail (ảnh nhỏ).
- Nếu setup cả 2 → hiển thị cả 2. Nếu chỉ 1 → chỉ 1.
"""
import logging

import discord
from discord import ui

from core.data_manager import db
from helpers.panels import refresh_panel

log = logging.getLogger(__name__)


# Embed keys sẽ trigger refresh panel tương ứng sau khi sửa
PANEL_EMBED_MAP = {
    "embed_main_select":     "user",
    "embed_check_ctv_panel": "check_ctv",
}


# =========================================================
#  FIELD CONFIG — mỗi field có label, modal_title, style
# =========================================================
FIELD_CONFIG: dict[str, dict] = {
    "title": {
        "btn_label": "📝 Sửa Tiêu Đề",
        "modal_title": "Sửa Tiêu Đề",
        "input_label": "Tiêu đề",
        "max_length": 256,
        "style": discord.TextStyle.short,
        "placeholder": "Nhập tiêu đề mới...",
    },
    "description": {
        "btn_label": "📄 Sửa Nội Dung",
        "modal_title": "Sửa Nội Dung",
        "input_label": "Nội dung",
        "max_length": 4000,
        "style": discord.TextStyle.paragraph,
        "placeholder": "Nhập nội dung mới...",
    },
    "footer": {
        "btn_label": "📎 Sửa Footer",
        "modal_title": "Sửa Footer",
        "input_label": "Footer (chữ dưới cùng)",
        "max_length": 2048,
        "style": discord.TextStyle.short,
        "placeholder": "Nhập footer mới...",
    },
    "image": {
        "btn_label": "🖼️ Sửa Ảnh Lớn",
        "modal_title": "Sửa Ảnh Lớn (Image)",
        "input_label": "URL Ảnh Lớn",
        "max_length": 500,
        "style": discord.TextStyle.short,
        "placeholder": "https://... (bỏ trống để xóa)",
    },
    "thumbnail": {
        "btn_label": "🎴 Sửa Ảnh Nhỏ",
        "modal_title": "Sửa Ảnh Nhỏ (Thumbnail)",
        "input_label": "URL Ảnh Nhỏ",
        "max_length": 500,
        "style": discord.TextStyle.short,
        "placeholder": "https://... (bỏ trống để xóa)",
    },
}


# =========================================================
#  BUILD PREVIEW EMBED (giống 100% embed sẽ gửi)
# =========================================================
def build_preview_embed(embed_key: str) -> discord.Embed:
    """
    Build embed EXACTLY như user sẽ thấy, với placeholder cho field trống.

    Áp dụng cả image + thumbnail nếu có (support yêu cầu #6).
    """
    data = db.load("embeds").get(embed_key, {})

    embed = discord.Embed(
        title=data.get("title") or "*(Chưa có tiêu đề — bấm 📝 để sửa)*",
        description=data.get("description") or "*(Chưa có nội dung — bấm 📄 để sửa)*",
        color=discord.Color.blurple(),
    )

    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])
    if data.get("image"):
        embed.set_image(url=data["image"])

    embed.set_footer(text=data.get("footer") or "(Chưa có footer — bấm 📎 để sửa)")
    return embed


# =========================================================
#  MODAL: sửa 1 field duy nhất
# =========================================================
class EditSingleFieldModal(ui.Modal):
    def __init__(
        self,
        embed_key: str,
        field: str,
        current_value: str,
        preview_message: discord.Message,
    ):
        cfg = FIELD_CONFIG[field]
        super().__init__(title=cfg["modal_title"])
        self.embed_key = embed_key
        self.field = field
        self.preview_message = preview_message

        self.value_input = ui.TextInput(
            label=cfg["input_label"],
            style=cfg["style"],
            default=current_value or "",
            required=False,  # cho phép xóa (bỏ trống)
            max_length=cfg["max_length"],
            placeholder=cfg["placeholder"],
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Save DB
        new_value = self.value_input.value

        def _save(embeds):
            embeds.setdefault(self.embed_key, {})[self.field] = new_value

        await db.aupdate("embeds", _save)
        log.info(
            f"Admin {interaction.user} sửa `{self.embed_key}.{self.field}` "
            f"(len={len(new_value)})"
        )

        # 2. Refresh preview qua response.edit_message (fix ephemeral edit)
        new_preview = build_preview_embed(self.embed_key)
        new_view = EmbedEditorView(self.embed_key)
        try:
            await interaction.response.edit_message(embed=new_preview, view=new_view)
        except Exception:
            log.exception("Refresh preview lỗi")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

        # 3. Panel auto-refresh nếu embed liên quan
        panel_name = PANEL_EMBED_MAP.get(self.embed_key)
        refreshed = False
        if panel_name:
            try:
                refreshed = await refresh_panel(interaction.client, panel_name)
            except Exception:
                log.exception(f"Auto-refresh panel '{panel_name}' lỗi")

        # 4. Followup message xác nhận
        cfg = FIELD_CONFIG[self.field]
        extra = f"\n🔄 Panel **{panel_name}** đã tự cập nhật." if refreshed else ""
        await interaction.followup.send(
            f"✅ Đã cập nhật **{cfg['modal_title']}**.{extra}",
            ephemeral=True,
        )


# =========================================================
#  BUTTON: 1 nút = 1 field
# =========================================================
class EditFieldButton(ui.Button):
    def __init__(self, embed_key: str, field: str, row: int):
        cfg = FIELD_CONFIG[field]
        super().__init__(
            label=cfg["btn_label"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.embed_key = embed_key
        self.field = field

    async def callback(self, interaction: discord.Interaction):
        current = db.load("embeds").get(self.embed_key, {}).get(self.field, "")
        modal = EditSingleFieldModal(
            embed_key=self.embed_key,
            field=self.field,
            current_value=current,
            preview_message=interaction.message,
        )
        await interaction.response.send_modal(modal)


# =========================================================
#  MAIN EDITOR VIEW — 5 nút sửa + 1 nút đóng
# =========================================================
class EmbedEditorView(ui.View):
    def __init__(self, embed_key: str):
        super().__init__(timeout=600)  # 10 phút
        self.embed_key = embed_key

        # Row 0: nội dung text (3 nút)
        self.add_item(EditFieldButton(embed_key, "title", row=0))
        self.add_item(EditFieldButton(embed_key, "description", row=0))
        self.add_item(EditFieldButton(embed_key, "footer", row=0))

        # Row 1: ảnh (2 nút)
        self.add_item(EditFieldButton(embed_key, "image", row=1))
        self.add_item(EditFieldButton(embed_key, "thumbnail", row=1))

        # Row 2: nút đóng
        close_btn = ui.Button(label="❌ Đóng", style=discord.ButtonStyle.danger, row=2)
        close_btn.callback = self._close
        self.add_item(close_btn)

    async def _close(self, interaction: discord.Interaction):
        try:
            await interaction.response.edit_message(
                content="🔒 Đã đóng trình sửa Embed.",
                embed=None,
                view=None,
            )
        except Exception:
            pass
