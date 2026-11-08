"""
Broadcast View — flow "Gửi Voice" cho Admin.

Admin bấm nút "📢 Gửi Voice" ở admin panel → mở view có:
- 📝 Nội dung  — modal nhập text
- 🔊 Âm lượng  — modal nhập %
- Dropdown chọn kênh voice
- 🚀 Gửi ngay   — bot vào kênh + phát TTS
- 💾 Lưu cấu hình — lưu nội dung + âm lượng (KHÔNG lưu kênh)
- ❌ Hủy

State (message, volume, selected_channel) lưu in-memory per view instance.
Load initial value từ voice_settings.json khi mở view.
"""
import logging

import discord
from discord import ui

from core.data_manager import db
from helpers.tts import (
    generate_tts_file,
    cleanup_tts_file,
    DEFAULT_VOICE_FEMALE,
)
from helpers.voice_manager import play_audio_in_channel

log = logging.getLogger(__name__)


def build_broadcast_preview(state: "BroadcastState") -> discord.Embed:
    """Preview embed hiển thị state hiện tại."""
    embed = discord.Embed(
        title="📢 Gửi Voice (Broadcast) — Admin",
        description="Cấu hình rồi bấm **Gửi ngay** hoặc **Lưu cấu hình**.",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="🔊 Âm lượng",
        value=f"**{int(state.volume * 100)}%**",
        inline=True,
    )

    channel_display = f"<#{state.channel_id}>" if state.channel_id else "*(chưa chọn)*"
    embed.add_field(
        name="🎯 Kênh sẽ phát",
        value=channel_display,
        inline=True,
    )

    msg = state.message
    msg_display = msg[:400] + ("..." if len(msg) > 400 else "")
    embed.add_field(
        name="📝 Nội dung (đọc TTS)",
        value=f"```{msg_display}```",
        inline=False,
    )

    embed.set_footer(text="Chỉ Admin thao tác được với view này")
    return embed


class BroadcastState:
    """In-memory state của 1 view instance."""

    def __init__(self):
        settings = db.load("voice_settings")
        self.message: str = settings.get("broadcast_message", "Xin chào các bạn.")
        self.volume: float = float(settings.get("broadcast_volume", 1.0))
        self.voice: str = settings.get("voice", DEFAULT_VOICE_FEMALE)
        self.channel_id: int | None = None  # KHÔNG lưu — chọn mỗi lần


# =========================================================
#  MODAL: Sửa Nội Dung
# =========================================================
class EditBroadcastMessageModal(ui.Modal, title="Sửa Nội Dung Phát"):
    def __init__(self, state: BroadcastState, view: "BroadcastView"):
        super().__init__()
        self.state = state
        self.view_ref = view

        self.value_input = ui.TextInput(
            label="Nội dung cần phát (đọc TTS)",
            style=discord.TextStyle.paragraph,
            default=state.message,
            required=True,
            max_length=1000,
            placeholder="Vd: Thông báo cho các bạn, buổi live tối nay bắt đầu lúc 20:00...",
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.state.message = self.value_input.value.strip()
        log.info(f"Admin {interaction.user} update broadcast message (len={len(self.state.message)})")

        try:
            await interaction.response.edit_message(
                embed=build_broadcast_preview(self.state),
                view=self.view_ref,
            )
        except Exception:
            log.exception("Refresh broadcast preview lỗi")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

        await interaction.followup.send("✅ Đã cập nhật nội dung.", ephemeral=True)


# =========================================================
#  MODAL: Sửa Âm Lượng
# =========================================================
class EditBroadcastVolumeModal(ui.Modal, title="Chỉnh Âm Lượng Phát"):
    def __init__(self, state: BroadcastState, view: "BroadcastView"):
        super().__init__()
        self.state = state
        self.view_ref = view

        self.value_input = ui.TextInput(
            label="Âm lượng % (0-200, 100=bình thường)",
            style=discord.TextStyle.short,
            default=str(int(state.volume * 100)),
            required=True,
            max_length=4,
            placeholder="100",
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("⚠️ Âm lượng phải là số 0-200.", ephemeral=True)
            return

        n = int(raw)
        if n < 0 or n > 200:
            await interaction.response.send_message("⚠️ Âm lượng phải trong khoảng 0-200 (%).", ephemeral=True)
            return

        self.state.volume = n / 100.0
        log.info(f"Admin {interaction.user} update broadcast volume = {n}%")

        try:
            await interaction.response.edit_message(
                embed=build_broadcast_preview(self.state),
                view=self.view_ref,
            )
        except Exception:
            log.exception("Refresh broadcast preview lỗi")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

        await interaction.followup.send(f"✅ Âm lượng đã set: **{n}%**.", ephemeral=True)


# =========================================================
#  SELECT: Chọn kênh voice
# =========================================================
class BroadcastChannelSelect(ui.Select):
    def __init__(self, state: BroadcastState, view: "BroadcastView"):
        self.state = state
        self.view_ref = view

        channels = db.load("channels")
        options: list[discord.SelectOption] = []
        if not channels:
            options.append(discord.SelectOption(label="Chưa có kênh nào", value="none"))
        else:
            for cid, data in channels.items():
                options.append(discord.SelectOption(
                    label=data.get("name", f"Kênh {cid}"),
                    value=cid,
                    description=f"ID: {cid}",
                ))
        options = options[:25]

        super().__init__(
            placeholder="🎯 Chọn kênh voice để phát...",
            min_values=1,
            max_values=1,
            options=options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        cid = self.values[0]
        if cid == "none":
            await interaction.response.send_message("Chưa có kênh nào trong hệ thống.", ephemeral=True)
            return

        self.state.channel_id = int(cid)
        log.info(f"Admin {interaction.user} chọn kênh broadcast = {cid}")

        try:
            await interaction.response.edit_message(
                embed=build_broadcast_preview(self.state),
                view=self.view_ref,
            )
        except Exception:
            log.exception("Refresh broadcast preview lỗi")


# =========================================================
#  BUTTONS
# =========================================================
class EditMessageButton(ui.Button):
    def __init__(self, state: BroadcastState, view: "BroadcastView"):
        super().__init__(label="📝 Nội dung", style=discord.ButtonStyle.secondary, row=0)
        self.state = state
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditBroadcastMessageModal(self.state, self.view_ref))


class EditVolumeButton(ui.Button):
    def __init__(self, state: BroadcastState, view: "BroadcastView"):
        super().__init__(label="🔊 Âm lượng", style=discord.ButtonStyle.secondary, row=0)
        self.state = state
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditBroadcastVolumeModal(self.state, self.view_ref))


class SendNowButton(ui.Button):
    def __init__(self, state: BroadcastState):
        super().__init__(label="🚀 Gửi ngay", style=discord.ButtonStyle.success, row=3)
        self.state = state

    async def callback(self, interaction: discord.Interaction):
        # Validate
        if not self.state.channel_id:
            await interaction.response.send_message(
                "⚠️ Bạn chưa chọn kênh voice.",
                ephemeral=True,
            )
            return

        if not self.state.message.strip():
            await interaction.response.send_message(
                "⚠️ Nội dung không được để trống.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(self.state.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "⚠️ Kênh đã chọn không phải VoiceChannel (có thể đã bị xóa).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Generate TTS
        audio_path = await generate_tts_file(self.state.message, voice=self.state.voice)
        if not audio_path:
            await interaction.followup.send(
                "⚠️ Không generate được TTS. Kiểm tra kết nối internet.",
                ephemeral=True,
            )
            return

        try:
            success = await play_audio_in_channel(
                channel,
                audio_path,
                volume=self.state.volume,
            )
        except Exception as e:
            log.exception("Broadcast play lỗi")
            await interaction.followup.send(f"⚠️ Lỗi phát audio: {e}", ephemeral=True)
            cleanup_tts_file(audio_path)
            return

        cleanup_tts_file(audio_path)

        if success:
            log.info(
                f"📢 Admin {interaction.user} broadcast vào #{channel.name}, "
                f"vol={int(self.state.volume*100)}%, msg_len={len(self.state.message)}"
            )
            await interaction.followup.send(
                f"✅ Đã phát tại **#{channel.name}** với âm lượng **{int(self.state.volume*100)}%**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Phát fail. Kiểm tra quyền bot (Connect/Speak) và FFmpeg.",
                ephemeral=True,
            )


class SaveConfigButton(ui.Button):
    def __init__(self, state: BroadcastState):
        super().__init__(label="💾 Lưu cấu hình", style=discord.ButtonStyle.primary, row=3)
        self.state = state

    async def callback(self, interaction: discord.Interaction):
        """Chỉ lưu message + volume, KHÔNG lưu channel_id."""

        def _save(data):
            data["broadcast_message"] = self.state.message
            data["broadcast_volume"] = self.state.volume

        await db.aupdate("voice_settings", _save)
        log.info(f"Admin {interaction.user} save broadcast config")

        await interaction.response.send_message(
            f"✅ Đã lưu cấu hình:\n"
            f"• 📝 Nội dung ({len(self.state.message)} ký tự)\n"
            f"• 🔊 Âm lượng: **{int(self.state.volume*100)}%**\n"
            f"*(Kênh KHÔNG được lưu — chọn mỗi lần gửi)*",
            ephemeral=True,
        )


class CancelButton(ui.Button):
    def __init__(self):
        super().__init__(label="❌ Hủy", style=discord.ButtonStyle.danger, row=3)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.edit_message(
                content="🚫 Đã hủy Broadcast.",
                embed=None,
                view=None,
            )
        except Exception:
            pass


# =========================================================
#  MAIN VIEW
# =========================================================
class BroadcastView(ui.View):
    def __init__(self):
        super().__init__(timeout=900)  # 15 phút
        self.state = BroadcastState()

        # Row 0: Nội dung + Âm lượng
        self.add_item(EditMessageButton(self.state, self))
        self.add_item(EditVolumeButton(self.state, self))

        # Row 2: Dropdown chọn kênh (chiếm full row)
        self.add_item(BroadcastChannelSelect(self.state, self))

        # Row 3: 3 nút action
        self.add_item(SendNowButton(self.state))
        self.add_item(SaveConfigButton(self.state))
        self.add_item(CancelButton())
