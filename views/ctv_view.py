"""
Views cho CTV:
- CTVView: hiện khi CTV chọn kênh mình (Live Ngay + Thống kê).
- NextLiveView: DM nhắc CTV đặt giờ Live tiếp theo.

FIX (post batch 5): NextLiveView đổi thành 1 button → mở Modal nhập HH:MM.
Persistent qua DynamicItem — sau bot restart button vẫn work.
"""
import datetime
import logging
import re

import discord
from discord import ui

from core.data_manager import db
from helpers.stats import create_channel_report
from helpers.time_utils import now_vn, parse_hhmm

log = logging.getLogger(__name__)


# =========================================================
#  CTV VIEW — hiện khi CTV chọn kênh (ephemeral)
# =========================================================
class CTVView(ui.View):
    def __init__(self, channel_id, guild_id):
        super().__init__(timeout=600)
        self.channel_id = str(channel_id)

        link_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        self.add_item(ui.Button(
            label="Live Ngay 🔴",
            style=discord.ButtonStyle.link,
            url=link_url,
        ))

        stat_btn = ui.Button(label="Thống kê Hôm nay", style=discord.ButtonStyle.secondary)
        stat_btn.callback = self.stat_today
        self.add_item(stat_btn)

    async def stat_today(self, interaction: discord.Interaction):
        embed = create_channel_report(self.channel_id, 1, "Hôm Nay")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
#  MODAL: Nhập giờ Live tiếp theo (HH:MM)
# =========================================================
class NextLiveModal(ui.Modal, title="Đặt Giờ Live Tiếp Theo"):
    def __init__(self, channel_id: str):
        super().__init__(timeout=600)
        self.channel_id = str(channel_id)

        self.time_input = ui.TextInput(
            label="Giờ Live (giờ Việt Nam)",
            placeholder="Ví dụ: 20:00, 20h, 20h30, 8:30",
            max_length=5,
            min_length=1,
            required=True,
        )
        self.add_item(self.time_input)

    async def on_submit(self, interaction: discord.Interaction):
        parsed = parse_hhmm(self.time_input.value)
        if parsed is None:
            await interaction.response.send_message(
                "⚠️ Giờ không hợp lệ. Nhập kiểu `20:00`, `20h`, `20h30` hoặc `8:30` (giờ 0-23, phút 0-59).",
                ephemeral=True,
            )
            return
        hour, minute = parsed

        # Tính target theo giờ VN
        now = now_vn()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Nếu giờ đã qua → sang ngày mai
        if target <= now:
            target += datetime.timedelta(days=1)

        time_str = f"{hour:02d}:{minute:02d}"

        # Lịch gắn theo từng CTV (channel_id chỉ để tạo link "Live Ngay")
        def _save(data):
            data[str(interaction.user.id)] = {
                "time": target.timestamp(),
                "time_str": time_str,
                "channel_id": self.channel_id,
                "reminders_sent": 0,
            }

        await db.aupdate("next_live", _save)

        # Edit DM message — vô hiệu hóa button (không cần đặt lại)
        try:
            await interaction.response.edit_message(
                content=(
                    f"🎉 Đã ghi nhận lịch Live tiếp theo lúc **{time_str}** "
                    f"(ngày {target.strftime('%d/%m/%Y')}).\n"
                    "Hãy nghỉ ngơi và chờ Bot nhắc bạn nhé!"
                ),
                embed=None,
                view=None,
            )
        except Exception:
            log.exception("Edit NextLive DM lỗi")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"🎉 Đã ghi nhận lịch Live tiếp theo lúc **{time_str}**.",
                    ephemeral=True,
                )

        log.info(f"CTV {interaction.user.id} đặt lịch live lúc {time_str} (kênh {self.channel_id})")


# =========================================================
#  DYNAMIC BUTTON: Mở NextLiveModal (persistent qua custom_id)
# =========================================================
class OpenNextLiveModalButton(
    ui.DynamicItem[ui.Button],
    template=r"next_live:(?P<cid>\d+)",
):
    """
    Button trong DM báo tắt live → bấm để mở modal nhập HH:MM.
    Persistent — bot restart button vẫn hoạt động.
    """

    def __init__(self, channel_id):
        super().__init__(
            ui.Button(
                style=discord.ButtonStyle.success,
                label="🕐 Đặt Giờ Live Tiếp Theo",
                custom_id=f"next_live:{channel_id}",
            )
        )
        self.channel_id = str(channel_id)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str]
    ) -> "OpenNextLiveModalButton":
        return cls(match["cid"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NextLiveModal(self.channel_id))


# =========================================================
#  NEXT LIVE VIEW — DM nhắc CTV
# =========================================================
class NextLiveView(ui.View):
    """
    DM cho CTV sau khi tắt live — chỉ chứa 1 button (dynamic item persistent).
    """

    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.add_item(OpenNextLiveModalButton(channel_id))
