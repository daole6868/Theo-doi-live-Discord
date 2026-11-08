"""
Dashboard check thời gian live của CTV.

UX mới:
- Chọn CTV từ dropdown → default HÔM NAY (chi tiết phiên hôm nay).
- Bấm "7 ngày" hoặc "15 ngày" → summary theo ngày + nút mỗi ngày để drill-down.
- Bấm 1 nút ngày cụ thể → chi tiết phiên của ngày đó.
"""
import datetime
import logging

import discord
from discord import ui

from config import CHECK_RANGE_OPTIONS
from core.data_manager import db
from helpers.stats import create_ctv_report
from helpers.time_utils import now_vn

log = logging.getLogger(__name__)


# =========================================================
#  RANGE BUTTON — Hôm nay / 7 ngày / 15 ngày
# =========================================================
class CheckCTVRangeButton(ui.Button):
    def __init__(self, user_id: str, days: int, label: str, current: int):
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.success
                if days == current
                else discord.ButtonStyle.secondary
            ),
            row=0,
        )
        self.user_id = user_id
        self.days = days

    async def callback(self, interaction: discord.Interaction):
        # Reset selected_day khi đổi range
        embed = create_ctv_report(self.user_id, self.days)
        new_view = CheckCTVDetailView(self.user_id, current=self.days, selected_day=None)
        await interaction.response.edit_message(embed=embed, view=new_view)


# =========================================================
#  DAY BUTTON — nút cho 1 ngày cụ thể (khi range > 1)
# =========================================================
class CheckCTVDayButton(ui.Button):
    def __init__(
        self,
        user_id: str,
        day: datetime.date,
        label: str,
        current_range: int,
        is_selected: bool,
        row: int,
    ):
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.primary
                if is_selected
                else discord.ButtonStyle.secondary
            ),
            row=row,
        )
        self.user_id = user_id
        self.day = day
        self.current_range = current_range

    async def callback(self, interaction: discord.Interaction):
        embed = create_ctv_report(self.user_id, self.current_range, target_day=self.day)
        new_view = CheckCTVDetailView(
            self.user_id,
            current=self.current_range,
            selected_day=self.day,
        )
        await interaction.response.edit_message(embed=embed, view=new_view)


# =========================================================
#  DETAIL VIEW — 3 nút range + [7/15 nút ngày] khi range > 1
# =========================================================
class CheckCTVDetailView(ui.View):
    def __init__(
        self,
        user_id: str,
        current: int = 1,
        selected_day: datetime.date | None = None,
    ):
        super().__init__(timeout=600)

        # Row 0: 3 nút khung (Hôm nay / 7 ngày / 15 ngày)
        for label, days in CHECK_RANGE_OPTIONS:
            self.add_item(CheckCTVRangeButton(user_id, days, label, current))

        # Chỉ hiện nút ngày khi chọn khung > 1
        if current > 1:
            today = now_vn().date()
            for i in range(current):
                day = today - datetime.timedelta(days=i)
                if i == 0:
                    label = "Hôm nay"
                elif i == 1:
                    label = "Hôm qua"
                else:
                    label = day.strftime("%d/%m")
                # Row 1: 5 nút đầu, Row 2: 5 nút tiếp, ...
                row = 1 + (i // 5)
                is_selected = selected_day == day
                self.add_item(
                    CheckCTVDayButton(user_id, day, label, current, is_selected, row)
                )


# =========================================================
#  CHECK CTV DROPDOWN (persistent)
# =========================================================
class CheckCTVSelect(ui.Select):
    def __init__(self):
        ctv_data = db.load("ctv")
        channels_data = db.load("channels")
        options: list[discord.SelectOption] = []

        if not ctv_data:
            options.append(discord.SelectOption(label="Chưa có CTV nào", value="none"))
        else:
            for user_id, channel_list in ctv_data.items():
                if not isinstance(channel_list, list):
                    channel_list = []
                names = [channels_data.get(cid, {}).get("name", "?") for cid in channel_list]
                desc = f"Quản lý: {', '.join(names)}" if names else "Chưa có kênh"
                if len(desc) > 100:
                    desc = desc[:97] + "..."

                options.append(discord.SelectOption(
                    label=f"CTV ID: {user_id}",
                    value=str(user_id),
                    description=desc,
                ))
        options = options[:25]

        super().__init__(
            placeholder="Chọn CTV để xem thời gian live...",
            min_values=1,
            max_values=1,
            custom_id="check_ctv_select",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]

        if value == "none":
            await interaction.response.send_message(
                "Chưa có CTV nào trong hệ thống.",
                ephemeral=True,
            )
            return

        member = interaction.guild.get_member(int(value)) if value.isdigit() else None
        display = f"{member.display_name} (`{value}`)" if member else f"CTV `{value}`"

        # Default: HÔM NAY (chi tiết phiên hôm nay)
        embed = create_ctv_report(value, 1)
        view = CheckCTVDetailView(value, current=1, selected_day=None)
        await interaction.response.send_message(
            content=f"📋 Đang xem: **{display}**",
            embed=embed,
            view=view,
            ephemeral=True,
        )

        # Rebuild dropdown panel
        try:
            new_panel = CheckCTVPanelView()
            await interaction.message.edit(view=new_panel)
        except Exception:
            log.exception("Rebuild CheckCTV panel lỗi")


class CheckCTVPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CheckCTVSelect())
