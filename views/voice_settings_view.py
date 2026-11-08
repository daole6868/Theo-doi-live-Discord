"""
Voice Alert Settings — UI cấu hình từng field.

Mỗi field 1 nút riêng để sửa:
- 📝 Nội dung tin nhắc (đọc TTS)
- ⏱️ Số phút chờ trước khi nhắc
- 🔁 Số phút cửa sổ nhắc
- 🔢 Số lần nhắc trong cửa sổ
- 📤 ID kênh log
- 🎤 Giọng đọc (nữ/nam)
- 🔊 Âm lượng phát (0-200%)
- 🎧 Nghe thử (gen TTS + gửi attachment)
- 🟢/🔴 Bật/Tắt tính năng
"""
import logging

import discord
from discord import ui

from core.data_manager import db
from helpers.tts import (
    DEFAULT_VOICE_FEMALE,
    DEFAULT_VOICE_MALE,
    generate_tts_file,
    cleanup_tts_file,
)

log = logging.getLogger(__name__)


# =========================================================
#  FIELD CONFIG
# =========================================================
FIELD_CONFIG: dict[str, dict] = {
    "message": {
        "btn_label": "📝 Nội dung tin nhắc",
        "modal_title": "Sửa Nội Dung Tin Nhắc",
        "input_label": "Nội dung TTS (đọc bằng giọng nói)",
        "style": discord.TextStyle.paragraph,
        "max_length": 500,
        "placeholder": "Vd: Xin chào {ten_ctv}, vui lòng bật Go Live...",
        "type": "text",
    },
    "delay_before_alert_minutes": {
        "btn_label": "⏱️ Phút chờ",
        "modal_title": "Số phút chờ trước khi nhắc",
        "input_label": "Số phút chờ (0-120)",
        "style": discord.TextStyle.short,
        "max_length": 5,
        "placeholder": "15",
        "type": "int",
        "min_val": 0,
        "max_val": 120,
    },
    "alert_window_minutes": {
        "btn_label": "🔁 Cửa sổ nhắc",
        "modal_title": "Cửa sổ thời gian nhắc",
        "input_label": "Số phút cửa sổ nhắc (1-120)",
        "style": discord.TextStyle.short,
        "max_length": 5,
        "placeholder": "15",
        "type": "int",
        "min_val": 1,
        "max_val": 120,
    },
    "alert_count": {
        "btn_label": "🔢 Số lần nhắc",
        "modal_title": "Số lần nhắc trong cửa sổ",
        "input_label": "Số lần nhắc (1-20)",
        "style": discord.TextStyle.short,
        "max_length": 3,
        "placeholder": "5",
        "type": "int",
        "min_val": 1,
        "max_val": 20,
    },
    "log_channel_id": {
        "btn_label": "📤 Kênh Log",
        "modal_title": "ID Kênh ghi log disconnect",
        "input_label": "ID kênh text (0 = tắt log)",
        "style": discord.TextStyle.short,
        "max_length": 25,
        "placeholder": "Copy ID kênh text bạn muốn nhận log...",
        "type": "channel_id",
    },
    "alert_volume": {
        "btn_label": "🔊 Âm lượng",
        "modal_title": "Chỉnh âm lượng phát",
        "input_label": "Âm lượng % (0-200, 100=bình thường)",
        "style": discord.TextStyle.short,
        "max_length": 4,
        "placeholder": "100",
        "type": "volume",
    },
}


def build_settings_preview() -> discord.Embed:
    """Build embed hiển thị settings hiện tại."""
    settings = db.load("voice_settings")

    enabled = settings.get("enabled", True)
    delay = int(settings.get("delay_before_alert_minutes", 15))
    window = int(settings.get("alert_window_minutes", 15))
    count = max(1, int(settings.get("alert_count", 5)))
    interval = window / count
    total = delay + window
    voice = settings.get("voice", DEFAULT_VOICE_FEMALE)
    voice_label = "Nữ (HoaiMy)" if voice == DEFAULT_VOICE_FEMALE else "Nam (NamMinh)"
    log_ch = settings.get("log_channel_id", 0)
    message = settings.get("message", "*(chưa có)*")
    volume = float(settings.get("alert_volume", 1.0))

    embed = discord.Embed(
        title="🔊 Cài đặt Voice Alert — CTV vào kênh chưa Live",
        color=discord.Color.orange() if enabled else discord.Color.dark_grey(),
    )

    embed.add_field(
        name="Trạng thái",
        value="🟢 **BẬT**" if enabled else "🔴 **TẮT**",
        inline=True,
    )
    embed.add_field(name="🎤 Giọng đọc", value=voice_label, inline=True)
    embed.add_field(name="🔊 Âm lượng", value=f"**{int(volume*100)}%**", inline=True)

    embed.add_field(name="⏱️ Phút chờ", value=f"**{delay}** phút", inline=True)
    embed.add_field(name="🔁 Cửa sổ nhắc", value=f"**{window}** phút", inline=True)
    embed.add_field(name="🔢 Số lần nhắc", value=f"**{count}** lần", inline=True)

    embed.add_field(
        name="🕐 Bot tự tính",
        value=(
            f"• Cách nhau mỗi: **{interval:.1f}** phút\n"
            f"• Tổng đến disconnect: **{total}** phút"
        ),
        inline=False,
    )

    embed.add_field(
        name="📤 Kênh log disconnect",
        value=f"<#{log_ch}>" if log_ch else "*(chưa cài — dùng kênh báo CTV đi trễ)*",
        inline=False,
    )

    msg_display = message[:400] + ("..." if len(message) > 400 else "")
    embed.add_field(
        name="📝 Nội dung tin nhắc (đọc TTS)",
        value=f"```{msg_display}```",
        inline=False,
    )

    embed.set_footer(text="Bấm nút bên dưới để chỉnh từng phần")
    return embed


# =========================================================
#  MODAL: Sửa 1 field
# =========================================================
class EditVoiceSettingModal(ui.Modal):
    def __init__(self, field: str, current_value, preview_message: discord.Message):
        cfg = FIELD_CONFIG[field]
        super().__init__(title=cfg["modal_title"][:45])
        self.field = field
        self.preview_message = preview_message
        self.cfg = cfg

        # Với volume: hiển thị % (chuyển từ float 0.0-2.0 → int 0-200)
        default_val = current_value
        if cfg.get("type") == "volume":
            default_val = str(int(float(current_value or 1.0) * 100))
        elif current_value in (None, ""):
            default_val = ""
        else:
            default_val = str(current_value)

        self.value_input = ui.TextInput(
            label=cfg["input_label"],
            style=cfg["style"],
            default=default_val,
            required=True,
            max_length=cfg["max_length"],
            placeholder=cfg.get("placeholder"),
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        field_type = self.cfg.get("type")
        new_value = None
        error = None

        if field_type == "int":
            if not raw.isdigit():
                error = "Giá trị phải là số nguyên dương."
            else:
                n = int(raw)
                min_v = self.cfg.get("min_val", 0)
                max_v = self.cfg.get("max_val", 99999)
                if n < min_v or n > max_v:
                    error = f"Giá trị phải trong khoảng {min_v}-{max_v}."
                else:
                    new_value = n

        elif field_type == "channel_id":
            raw = raw.replace("<#", "").replace(">", "").strip()
            if raw in ("0", ""):
                new_value = 0
            elif raw.isdigit():
                ch = interaction.guild.get_channel(int(raw))
                if not ch:
                    error = f"Không tìm thấy kênh ID `{raw}` trong server."
                elif not isinstance(ch, discord.TextChannel):
                    error = "Kênh log phải là Text Channel."
                else:
                    new_value = int(raw)
            else:
                error = "ID phải là số."

        elif field_type == "volume":
            # % → float 0.0-2.0
            if not raw.isdigit():
                error = "Âm lượng phải là số 0-200."
            else:
                n = int(raw)
                if n < 0 or n > 200:
                    error = "Âm lượng phải trong khoảng 0-200 (%)."
                else:
                    new_value = n / 100.0  # 100% → 1.0

        elif field_type == "text":
            if not raw:
                error = "Nội dung không được để trống."
            else:
                new_value = raw

        if error:
            await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)
            return

        # Save
        def _save(data):
            data[self.field] = new_value

        await db.aupdate("voice_settings", _save)
        log.info(f"Admin {interaction.user} update voice_settings.{self.field} = {new_value}")

        # Refresh preview qua response.edit_message
        new_preview = build_settings_preview()
        new_view = VoiceAlertSettingsView()
        try:
            await interaction.response.edit_message(embed=new_preview, view=new_view)
        except Exception:
            log.exception("Refresh voice settings preview lỗi")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

        await interaction.followup.send(
            f"✅ Đã cập nhật **{self.cfg['modal_title']}**.",
            ephemeral=True,
        )


# =========================================================
#  BUTTON: 1 nút = 1 field
# =========================================================
class VoiceSettingFieldButton(ui.Button):
    def __init__(self, field: str, row: int):
        cfg = FIELD_CONFIG[field]
        super().__init__(
            label=cfg["btn_label"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.field = field

    async def callback(self, interaction: discord.Interaction):
        settings = db.load("voice_settings")
        current = settings.get(self.field, "")
        modal = EditVoiceSettingModal(self.field, current, interaction.message)
        await interaction.response.send_modal(modal)


# =========================================================
#  MAIN VIEW
# =========================================================
class VoiceAlertSettingsView(ui.View):
    def __init__(self):
        super().__init__(timeout=600)

        # Row 0: nội dung + âm lượng
        self.add_item(VoiceSettingFieldButton("message", row=0))
        self.add_item(VoiceSettingFieldButton("alert_volume", row=0))

        # Row 1: 3 field time
        self.add_item(VoiceSettingFieldButton("delay_before_alert_minutes", row=1))
        self.add_item(VoiceSettingFieldButton("alert_window_minutes", row=1))
        self.add_item(VoiceSettingFieldButton("alert_count", row=1))

        # Row 2: log channel + toggle voice + toggle enabled
        self.add_item(VoiceSettingFieldButton("log_channel_id", row=2))

        settings = db.load("voice_settings")
        cur_voice = settings.get("voice", DEFAULT_VOICE_FEMALE)
        is_female = cur_voice == DEFAULT_VOICE_FEMALE

        voice_btn = ui.Button(
            label=f"🎤 Giọng: {'Nữ' if is_female else 'Nam'}",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        voice_btn.callback = self._toggle_voice
        self.add_item(voice_btn)

        enabled = settings.get("enabled", True)
        toggle = ui.Button(
            label="🟢 Đang BẬT" if enabled else "🔴 Đang TẮT",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger,
            row=2,
        )
        toggle.callback = self._toggle_enabled
        self.add_item(toggle)

        # Row 3: Nghe thử + Đóng
        test_btn = ui.Button(label="🎧 Nghe thử", style=discord.ButtonStyle.primary, row=3)
        test_btn.callback = self._test_listen
        self.add_item(test_btn)

        close = ui.Button(label="❌ Đóng", style=discord.ButtonStyle.secondary, row=3)
        close.callback = self._close
        self.add_item(close)

    async def _toggle_voice(self, interaction: discord.Interaction):
        settings = db.load("voice_settings")
        cur = settings.get("voice", DEFAULT_VOICE_FEMALE)
        new_voice = DEFAULT_VOICE_MALE if cur == DEFAULT_VOICE_FEMALE else DEFAULT_VOICE_FEMALE

        def _save(data):
            data["voice"] = new_voice

        await db.aupdate("voice_settings", _save)

        new_preview = build_settings_preview()
        new_view = VoiceAlertSettingsView()
        await interaction.response.edit_message(embed=new_preview, view=new_view)

    async def _toggle_enabled(self, interaction: discord.Interaction):
        settings = db.load("voice_settings")
        cur = settings.get("enabled", True)

        def _save(data):
            data["enabled"] = not cur

        await db.aupdate("voice_settings", _save)
        log.info(f"Admin {interaction.user} toggled voice_settings.enabled → {not cur}")

        new_preview = build_settings_preview()
        new_view = VoiceAlertSettingsView()
        await interaction.response.edit_message(embed=new_preview, view=new_view)

    async def _test_listen(self, interaction: discord.Interaction):
        """Generate TTS + gửi file mp3 ephemeral để admin nghe thử."""
        settings = db.load("voice_settings")
        message = settings.get("message", "Test TTS")
        voice = settings.get("voice", DEFAULT_VOICE_FEMALE)

        # Personalize {ten_ctv} thành tên admin để test
        preview_msg = message.replace("{ten_ctv}", interaction.user.display_name)

        await interaction.response.defer(ephemeral=True)

        audio_path = await generate_tts_file(preview_msg, voice=voice)
        if not audio_path:
            await interaction.followup.send(
                "⚠️ Không generate được TTS. Kiểm tra kết nối internet.",
                ephemeral=True,
            )
            return

        try:
            file = discord.File(audio_path, filename="nghe_thu.mp3")
            await interaction.followup.send(
                content=(
                    f"🎧 **Nghe thử nội dung TTS:**\n"
                    f"> {preview_msg[:300]}\n\n"
                    "*(Bấm play trong file bên dưới để nghe)*"
                ),
                file=file,
                ephemeral=True,
            )
        except Exception as e:
            log.exception("Gửi file nghe thử lỗi")
            await interaction.followup.send(f"⚠️ Lỗi: {e}", ephemeral=True)
        finally:
            cleanup_tts_file(audio_path)

    async def _close(self, interaction: discord.Interaction):
        try:
            await interaction.response.edit_message(
                content="🔒 Đã đóng trình cài đặt Voice Alert.",
                embed=None,
                view=None,
            )
        except Exception:
            pass
