"""
Views cho DM:
- NotifyView: gửi khi có LIVE cho subscribers (Xem Ngay + Tắt Thông Báo).
- ReminderView: gửi khi đến giờ Live cho CTV (Live Ngay + 3 mức Hoãn).

FEATURE MỚI: ReminderView có 3 nút hoãn (5 phút / 30 phút / 1 tiếng).
Khi CTV bấm hoãn → update time_str trong data_next_live.json → khách bấm chọn kênh
sẽ thấy giờ mới ngay (do CustomerView đọc live từ file).

Persistent qua DynamicItem — sau bot restart bấm nút vẫn work.
Cần đăng ký trong main.py: bot.add_dynamic_items(NotifyOffButton, ReminderDelayButton)
"""
import datetime
import logging
import re

import discord
from discord import ui

from config import DELAY_OPTIONS
from core.data_manager import db
from helpers.time_utils import now_vn

log = logging.getLogger(__name__)


# =========================================================
#  DYNAMIC BUTTON: Tắt Thông Báo (persistent qua custom_id)
# =========================================================
class NotifyOffButton(
    ui.DynamicItem[ui.Button],
    template=r"notify_off:(?P<cid>\d+)",
):
    """Nút Tắt Thông Báo trong DM báo Live cho subscriber."""

    def __init__(self, channel_id):
        super().__init__(
            ui.Button(
                style=discord.ButtonStyle.danger,
                label="Tắt Thông Báo 🔕",
                custom_id=f"notify_off:{channel_id}",
            )
        )
        self.channel_id = str(channel_id)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str]
    ) -> "NotifyOffButton":
        return cls(match["cid"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        uid_str = str(interaction.user.id)
        removed = False

        def _save(subs):
            nonlocal removed
            if self.channel_id in subs:
                lst = subs[self.channel_id]
                to_remove = [u for u in lst if str(u) == uid_str]
                for u in to_remove:
                    lst.remove(u)
                    removed = True

        await db.aupdate("subscribers", _save)

        if removed:
            await interaction.followup.send("✅ Đã tắt thông báo cho kênh này.", ephemeral=True)
        else:
            await interaction.followup.send("Bạn chưa đăng ký hoặc đã tắt rồi.", ephemeral=True)


# =========================================================
#  NOTIFY VIEW — DM cho subscriber khi kênh Live
# =========================================================
class NotifyView(ui.View):
    def __init__(self, channel_id, guild_id):
        super().__init__(timeout=None)

        # Link nhảy vào kênh voice (persistent tự động)
        link_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        self.add_item(ui.Button(
            label="Xem Ngay 🔴",
            style=discord.ButtonStyle.link,
            url=link_url,
        ))

        # Nút tắt thông báo (dynamic, persistent qua custom_id)
        self.add_item(NotifyOffButton(channel_id))


# =========================================================
#  DYNAMIC BUTTON: Hoãn Reminder (5p / 30p / 1h)
# =========================================================
class ReminderDelayButton(
    ui.DynamicItem[ui.Button],
    template=r"reminder_delay:(?P<cid>\d+):(?P<sec>\d+)",
):
    """
    Nút Hoãn báo thức Live — feature mới.

    Khi bấm: cộng thời gian hoãn từ NOW, reset reminders_sent → task sẽ báo thức
    lại sau khoảng hoãn. Đồng thời update time_str → khách bấm chọn kênh thấy giờ mới.
    """

    def __init__(self, channel_id, delay_seconds: int, label: str):
        super().__init__(
            ui.Button(
                style=discord.ButtonStyle.secondary,
                label=label,
                custom_id=f"reminder_delay:{channel_id}:{delay_seconds}",
            )
        )
        self.channel_id = str(channel_id)
        self.delay_seconds = int(delay_seconds)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str]
    ) -> "ReminderDelayButton":
        cid = match["cid"]
        sec = int(match["sec"])
        # Resolve label từ config
        label_map = {s: lbl for lbl, s in DELAY_OPTIONS}
        label = label_map.get(sec, f"Hoãn {sec // 60} phút")
        return cls(cid, sec, label)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        nl_data = db.load("next_live")
        if self.channel_id not in nl_data:
            await interaction.followup.send(
                "⚠️ Lịch live này đã bị hủy (do bạn đã vào kênh hoặc đã quá hạn 30 phút).",
                ephemeral=True,
            )
            return

        # Cộng delay từ NOW (không cộng từ giờ cũ)
        now = now_vn()
        new_dt = now + datetime.timedelta(seconds=self.delay_seconds)
        new_time_str = new_dt.strftime("%H:%M")

        def _save(data):
            if self.channel_id in data and isinstance(data[self.channel_id], dict):
                data[self.channel_id]["time"] = new_dt.timestamp()
                data[self.channel_id]["time_str"] = new_time_str
                data[self.channel_id]["reminders_sent"] = 0

        await db.aupdate("next_live", _save)

        # Disable tất cả nút không phải Link trong view
        try:
            view = self.view
            if view is not None:
                for child in view.children:
                    if hasattr(child, "style") and child.style != discord.ButtonStyle.link:
                        child.disabled = True
                await interaction.message.edit(view=view)
        except Exception as e:
            log.warning(f"Không disable được button reminder: {e}")

        minutes = self.delay_seconds // 60
        await interaction.followup.send(
            f"✅ Đã hoãn báo thức thêm **{minutes} phút**. "
            f"Giờ Live mới: **{new_time_str}**. Chuẩn bị nhé!",
            ephemeral=True,
        )
        log.info(
            f"CTV {interaction.user.id} hoãn reminder kênh {self.channel_id} "
            f"thêm {minutes} phút → {new_time_str}"
        )


# =========================================================
#  REMINDER VIEW — DM báo thức CTV khi đến giờ Live
# =========================================================
class ReminderView(ui.View):
    """
    View báo thức có:
    1. Nút Live Ngay 🔴 (Link)
    2-4. Ba nút hoãn: 5 phút / 30 phút / 1 tiếng (từ DELAY_OPTIONS trong config)
    """

    def __init__(self, channel_id, guild_id):
        super().__init__(timeout=None)

        # Link vào kênh voice
        link_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        self.add_item(ui.Button(
            label="Live Ngay 🔴",
            style=discord.ButtonStyle.link,
            url=link_url,
        ))

        # 3 nút hoãn từ DELAY_OPTIONS
        for label, sec in DELAY_OPTIONS:
            self.add_item(ReminderDelayButton(channel_id, sec, label))
