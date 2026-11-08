"""
Menu chọn từ dữ liệu đã lưu (kênh / CTV) — hiện sẵn danh sách để bấm chọn.

Discord giới hạn 25 lựa chọn / menu và 5 menu / tin nhắn → tự chia thành nhiều
menu khi có hơn 25 mục (tối đa 125 mục).
"""
from typing import Awaitable, Callable

import discord
from discord import ui

from core.data_manager import db
from core.session_store import store

OPTIONS_PER_SELECT = 25
MAX_SELECTS = 5

PickHandler = Callable[[discord.Interaction, str], Awaitable[None]]


class PickSelect(ui.Select):
    """1 menu trong nhóm; chọn xong gọi `on_pick(interaction, value)`."""

    def __init__(self, options, placeholder: str, on_pick: PickHandler, custom_id: str | None = None, row=None):
        kwargs = {"custom_id": custom_id} if custom_id else {}
        super().__init__(
            placeholder=placeholder[:150],
            options=options,
            min_values=1,
            max_values=1,
            row=row,
            **kwargs,
        )
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction):
        await self.on_pick(interaction, self.values[0])


def add_select_groups(
    view: ui.View,
    options: list[discord.SelectOption],
    placeholder: str,
    on_pick: PickHandler,
    custom_ids: list[str] | None = None,
    start_row: int = 0,
    max_selects: int = MAX_SELECTS,
    empty_label: str = "Chưa có dữ liệu",
) -> int:
    """
    Thêm 1..max_selects menu vào `view`. Trả về số mục bị cắt bớt (vượt giới hạn).

    custom_ids: dùng cho panel cố định (persistent) — menu thứ i lấy custom_ids[i].
    """
    if not options:
        options = [discord.SelectOption(label=empty_label, value="none")]

    limit = OPTIONS_PER_SELECT * max_selects
    truncated = max(0, len(options) - limit)
    options = options[:limit]

    groups = [options[i:i + OPTIONS_PER_SELECT] for i in range(0, len(options), OPTIONS_PER_SELECT)]
    for idx, group in enumerate(groups):
        if len(groups) > 1:
            first = idx * OPTIONS_PER_SELECT + 1
            ph = f"{placeholder} ({first}–{first + len(group) - 1})"
        else:
            ph = placeholder
        cid = custom_ids[idx] if custom_ids and idx < len(custom_ids) else None
        view.add_item(PickSelect(group, ph, on_pick, custom_id=cid, row=start_row + idx))
    return truncated


# =========================================================
#  DỰNG DANH SÁCH TỪ DỮ LIỆU ĐÃ LƯU
# =========================================================
def _member_name(guild, user_id: str) -> str:
    member = guild.get_member(int(user_id)) if guild is not None and str(user_id).isdigit() else None
    return member.display_name if member else f"CTV {user_id}"


def ctv_options(guild=None, include_former: bool = True) -> list[discord.SelectOption]:
    """
    CTV đang có trong hệ thống (sắp xếp theo tên), kèm các kênh họ quản lý.
    include_former: thêm người đã nghỉ nhưng còn lịch sử live (ghi "đã nghỉ").
    """
    ctv_data = db.load("ctv")
    channels_data = db.load("channels")

    current = []
    for uid, chans in ctv_data.items():
        chans = chans if isinstance(chans, list) else []
        names = [channels_data.get(c, {}).get("name", "?") for c in chans]
        desc = f"Quản lý: {', '.join(names)}" if names else "Chưa có kênh"
        current.append(discord.SelectOption(
            label=_member_name(guild, uid)[:100],
            value=str(uid),
            description=desc[:100],
        ))
    current.sort(key=lambda o: o.label.casefold())

    former = []
    if include_former:
        for uid in store.user_ids():
            if uid in ctv_data:
                continue
            former.append(discord.SelectOption(
                label=f"{_member_name(guild, uid)} (đã nghỉ)"[:100],
                value=str(uid),
                description="Không còn là CTV — vẫn còn lịch sử live",
            ))
        former.sort(key=lambda o: o.label.casefold())

    return current + former


def channel_options() -> list[discord.SelectOption]:
    """Kênh đã lưu trong hệ thống (sắp xếp theo tên)."""
    channels_data = db.load("channels")
    ctv_data = db.load("ctv")
    options = []
    for cid, data in channels_data.items():
        n_ctv = sum(1 for chans in ctv_data.values() if isinstance(chans, list) and cid in chans)
        mode = "🌐 Công khai" if not data.get("role_id") else "🔒 Riêng tư"
        options.append(discord.SelectOption(
            label=(data.get("name") or f"Kênh {cid}")[:100],
            value=str(cid),
            description=f"{mode} · {n_ctv} CTV",
        ))
    options.sort(key=lambda o: o.label.casefold())
    return options
