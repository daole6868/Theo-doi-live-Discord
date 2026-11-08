"""
Dashboard check thời gian live của CTV.

UX mới:
- Chọn CTV bằng ô chọn thành viên (gõ tên) → default HÔM NAY (chi tiết phiên hôm nay).
  Xem được cả người đã nghỉ (lịch sử vẫn giữ).
- Bấm "7 ngày" hoặc "15 ngày" → summary theo ngày + nút mỗi ngày để drill-down.
- Bấm 1 nút ngày cụ thể → chi tiết phiên của ngày đó.
"""
import datetime
import logging

import discord
from discord import ui

from config import CHECK_RANGE_OPTIONS
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
#  CHECK CTV — ô chọn thành viên (persistent, gõ tên để tìm)
# =========================================================
class CheckCTVSelect(ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="Gõ tên CTV để xem thời gian live...",
            min_values=1,
            max_values=1,
            custom_id="check_ctv_user_select",
        )

    async def callback(self, interaction: discord.Interaction):
        picked = self.values[0]
        value = str(picked.id)

        embed = create_ctv_report(value, 1)  # Mặc định: HÔM NAY
        if embed.title and embed.title.startswith("❌"):
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            view = CheckCTVDetailView(value, current=1, selected_day=None)
            await interaction.response.send_message(
                content=f"📋 Đang xem: **{picked.display_name}** (`{value}`)",
                embed=embed,
                view=view,
                ephemeral=True,
            )

        # Bỏ lựa chọn cũ trên panel để người sau chọn lại được
        try:
            await interaction.message.edit(view=CheckCTVPanelView())
        except Exception:
            log.exception("Rebuild CheckCTV panel lỗi")


class CheckCTVPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CheckCTVSelect())
