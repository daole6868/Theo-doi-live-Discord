"""
Views cho DM:
- NotifyView: gửi khi có LIVE cho subscribers (Xem Ngay + Tắt Thông Báo).
- ReminderView: gửi khi đến giờ Live cho CTV (Live Ngay + 3 mức Hoãn).

FEATURE MỚI: ReminderView có 3 nút hoãn (5 phút / 30 phút / 1 tiếng).
Khi CTV bấm hoãn → update time_str trong data_next_live.json (key theo CTV) → khách
bấm chọn kênh sẽ thấy giờ mới ngay (do CustomerView đọc live từ file).

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
from helpers import follows
from helpers.time_utils import now_vn

log = logging.getLogger(__name__)


# =========================================================
#  TẮT THÔNG BÁO (trong DM) — tắt theo CTV, giống nút Tắt ở panel server
# =========================================================
def _ctv_names(client, ctv_ids) -> str:
    names = []
    for x in ctv_ids:
        user = client.get_user(int(x)) if str(x).isdigit() else None
        names.append(f"**{user.display_name}**" if user else f"<@{x}>")
    return ", ".join(names)


class NotifyOffCTVButton(
    ui.DynamicItem[ui.Button],
    template=r"notify_off_ctv:(?P<uid>\d+)",
):
    """
    Nút Tắt Thông Báo trong DM báo live: bỏ theo dõi đúng CTV đã gửi tin này,
    bất kể CTV đó đang live ở kênh nào.
    """

    def __init__(self, ctv_id):
        super().__init__(
            ui.Button(
                style=discord.ButtonStyle.danger,
                label="Tắt Thông Báo 🔕",
                custom_id=f"notify_off_ctv:{ctv_id}",
            )
        )
        self.ctv_id = str(ctv_id)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str]
    ) -> "NotifyOffCTVButton":
        return cls(match["uid"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        removed = await follows.unfollow(interaction.user.id, [self.ctv_id])
        if removed:
            await interaction.followup.send(
                f"✅ Đã tắt thông báo CTV {_ctv_names(interaction.client, removed)}. "
                "Bạn sẽ không nhận tin khi CTV này live nữa.",
                ephemeral=True,
            )
            log.info(f"User {interaction.user.id} bỏ theo dõi CTV {self.ctv_id} (qua DM)")
        else:
            await interaction.followup.send(
                "ℹ️ Bạn đã tắt thông báo CTV này rồi.", ephemeral=True,
            )


class NotifyOffButton(
    ui.DynamicItem[ui.Button],
    template=r"notify_off:(?P<cid>\d+)",
):
    """
    Nút Tắt Thông Báo trong các DM CŨ (custom_id chứa kênh đang live, không có CTV).
    Bỏ theo dõi các CTV chủ kênh đó.
    """

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
        owners = [
            str(uid) for uid, chans in db.load("ctv").items()
            if isinstance(chans, list) and self.channel_id in chans
        ]
        removed = await follows.unfollow(interaction.user.id, owners) if owners else []
        if removed:
            await interaction.followup.send(
                f"✅ Đã tắt thông báo CTV {_ctv_names(interaction.client, removed)}.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "ℹ️ Tin nhắn này là bản cũ nên bot không xác định được CTV. "
                "Vào kênh chọn live, chọn kênh của CTV đó rồi bấm **Tắt Thông Báo**.",
                ephemeral=True,
            )


# =========================================================
#  NOTIFY VIEW — DM cho subscriber khi kênh Live
# =========================================================
class NotifyView(ui.View):
    def __init__(self, channel_id, guild_id, ctv_id=None):
        super().__init__(timeout=None)

        # Link nhảy vào kênh voice đang live (persistent tự động)
        link_url = f"https://discord.com/channels/{guild_id}/{channel_id}"
        self.add_item(ui.Button(
            label="Xem Ngay 🔴",
            style=discord.ButtonStyle.link,
            url=link_url,
        ))

        # Nút tắt thông báo (dynamic, persistent qua custom_id) — gắn theo CTV
        if ctv_id is not None:
            self.add_item(NotifyOffCTVButton(ctv_id))
        else:
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

        uid = str(interaction.user.id)
        nl_data = db.load("next_live")
        if uid not in nl_data:
            await interaction.followup.send(
                "⚠️ Lịch live này đã bị hủy (do bạn đã live hoặc đã quá hạn).",
                ephemeral=True,
            )
            return

        # Cộng delay từ NOW (không cộng từ giờ cũ)
        now = now_vn()
        new_dt = now + datetime.timedelta(seconds=self.delay_seconds)
        new_time_str = new_dt.strftime("%H:%M")

        def _save(data):
            if uid in data and isinstance(data[uid], dict):
                data[uid]["time"] = new_dt.timestamp()
                data[uid]["time_str"] = new_time_str
                data[uid]["reminders_sent"] = 0

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
        log.info(f"CTV {uid} hoãn nhắc live thêm {minutes} phút → {new_time_str}")


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
