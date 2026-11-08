"""
View cho khách hàng: dropdown chọn kênh + hiển thị trạng thái Live/Off.

FIX (batch 4A):
- Dùng 2 embed key khác nhau cho CTV đang LIVE và OFFLINE (theo yêu cầu #4).
- Thông báo theo CTV: "Bật Thông Báo" ở 1 kênh = theo dõi các CTV chủ kênh đó;
  CTV live ở bất kỳ kênh nào → khách theo dõi nhận DM. Nút Tắt ở đây và nút Tắt
  trong DM dùng chung helpers/follows.py nên luôn giống nhau.
"""
import logging

import discord
from discord import ui

from config import (
    EMBED_CUSTOMER_LIVE,
    EMBED_CUSTOMER_OFFLINE,
    EMBED_CTV_INFO,
)
from core.data_manager import db
from core.state import state
from helpers import follows
from helpers.embeds import clamp_embed, get_custom_embed
from helpers.permissions import is_admin
from helpers.stats import (
    channel_name,
    create_channel_report,
    get_channel_owners,
    get_last_stream_session,
)
from helpers.time_utils import from_ts, fmt_dur, now_vn

log = logging.getLogger(__name__)


# =========================================================
#  CUSTOMER VIEW — hiện sau khi khách chọn kênh
# =========================================================
class CustomerView(ui.View):
    def __init__(self, channel_id, guild_id, is_live: bool, user_id, live_link_channel_id=None):
        super().__init__(timeout=600)
        self.channel_id = str(channel_id)
        self.user_id = user_id

        if is_live:
            # Link tới kênh đang live thực tế (nếu live ở kênh khác thì link kênh khác)
            link_cid = live_link_channel_id or channel_id
            link_url = f"https://discord.com/channels/{guild_id}/{link_cid}"
            self.add_item(ui.Button(
                label="Xem Ngay 🔴",
                style=discord.ButtonStyle.link,
                url=link_url,
            ))

        # Thông báo theo CTV chủ kênh (giống hệt nút Tắt trong DM báo live)
        self.owners = get_channel_owners(self.channel_id)
        if not self.owners:
            self.notify_btn = ui.Button(
                label="Kênh chưa có CTV", style=discord.ButtonStyle.secondary, disabled=True,
            )
        elif follows.following(user_id, self.owners):
            self.notify_btn = ui.Button(label="Tắt Thông Báo", style=discord.ButtonStyle.danger)
        else:
            self.notify_btn = ui.Button(label="Bật Thông Báo", style=discord.ButtonStyle.success)
        self.notify_btn.callback = self.toggle_notify
        self.add_item(self.notify_btn)

        self.check_btn = ui.Button(label="Check Phiên Live", style=discord.ButtonStyle.secondary)
        self.check_btn.callback = self.check_stats
        self.add_item(self.check_btn)

    @staticmethod
    def _names(guild, ctv_ids) -> str:
        out = []
        for x in ctv_ids:
            m = guild.get_member(int(x)) if guild is not None else None
            out.append(f"**{m.display_name}**" if m else f"<@{x}>")
        return ", ".join(out)

    async def toggle_notify(self, interaction: discord.Interaction):
        """Bật = theo dõi các CTV chủ kênh; Tắt = bỏ theo dõi các CTV đó (ở mọi kênh)."""
        owners = get_channel_owners(self.channel_id)  # đọc lại phòng admin vừa đổi CTV
        if not owners:
            await interaction.response.send_message("ℹ️ Kênh này chưa có CTV để theo dõi.", ephemeral=True)
            return

        if follows.following(interaction.user.id, owners):
            changed = await follows.unfollow(interaction.user.id, owners)
            self.notify_btn.label = "Bật Thông Báo"
            self.notify_btn.style = discord.ButtonStyle.success
            msg = (
                f"🔕 Đã TẮT thông báo CTV {self._names(interaction.guild, changed or owners)}. "
                "Bạn sẽ không nhận tin khi CTV này live ở bất kỳ kênh nào."
            )
        else:
            await follows.follow(interaction.user.id, owners)
            self.notify_btn.label = "Tắt Thông Báo"
            self.notify_btn.style = discord.ButtonStyle.danger
            msg = (
                f"✅ Đã BẬT thông báo CTV {self._names(interaction.guild, owners)}. "
                "Bạn sẽ nhận DM mỗi khi CTV này live (ở bất kỳ kênh nào)."
            )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

    async def check_stats(self, interaction: discord.Interaction):
        embed = create_channel_report(self.channel_id, 1, "Hôm Nay")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
#  DROPDOWN CHỌN KÊNH — PERSISTENT
# =========================================================
# Discord: mỗi menu tối đa 25 lựa chọn, mỗi tin nhắn tối đa 5 menu → tối đa 125 kênh
OPTIONS_PER_SELECT = 25
MAX_SELECTS = 5


def _select_custom_id(index: int) -> str:
    # Menu đầu giữ custom_id cũ để panel đã gửi trước đây vẫn bấm được
    return "user_channel_select" if index == 0 else f"user_channel_select_{index + 1}"


class UserChannelSelect(ui.Select):
    def __init__(self, options: list[discord.SelectOption], index: int, total_groups: int):
        if total_groups > 1:
            first = index * OPTIONS_PER_SELECT + 1
            last = first + len(options) - 1
            placeholder = f"Chọn kênh để theo dõi... ({first}–{last})"
        else:
            placeholder = "Chọn kênh để theo dõi..."
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            custom_id=_select_custom_id(index),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        channel_id = self.values[0]
        if channel_id == "none":
            await interaction.followup.send("Hiện chưa có kênh nào.", ephemeral=True)
            return

        user = interaction.user
        guild = interaction.guild
        user_id = str(user.id)

        channels_data = db.load("channels")
        ctv_data = db.load("ctv")

        if channel_id not in channels_data:
            await interaction.followup.send(
                "⚠️ Kênh này không còn trong hệ thống. Bảng chọn sẽ tự cập nhật.",
                ephemeral=True,
            )
            return

        # ==== [1] KIỂM TRA QUYỀN TRUY CẬP CỦA CTV ====
        if user_id in ctv_data and channel_id not in ctv_data[user_id]:
            await interaction.followup.send(
                "⛔ **TRUY CẬP BỊ TỪ CHỐI**\n"
                "Bạn là CTV, chỉ được truy cập vào kênh do mình quản lý.",
                ephemeral=True,
            )
            return

        # ==== [2] XỬ LÝ ROLE (skip nếu kênh Công khai — không có role) ====
        target_role_id = channels_data.get(channel_id, {}).get("role_id", 0)

        # Không gỡ role của những kênh mà người này là CTV (tránh mất quyền vào kênh của mình),
        # và không gỡ role nào của admin.
        protected_role_ids = {
            channels_data.get(c, {}).get("role_id")
            for c in (ctv_data.get(user_id) or [])
            if channels_data.get(c, {}).get("role_id")
        }
        skip_role_removal = is_admin(user)

        if target_role_id:
            # PRIVATE mode: gán role kênh này, thu hồi role các kênh khác
            try:
                target_role = guild.get_role(target_role_id)
                # Chỉ tính các kênh CÓ role (bỏ public channels)
                all_managed_role_ids = {
                    d["role_id"] for d in channels_data.values()
                    if d.get("role_id")
                }

                roles_to_remove = [
                    r for r in user.roles
                    if r.id in all_managed_role_ids and r.id != target_role_id
                    and r.id not in protected_role_ids
                ]
                if roles_to_remove and not skip_role_removal:
                    await user.remove_roles(*roles_to_remove)

                if target_role and target_role not in user.roles:
                    await user.add_roles(target_role)
                elif not target_role:
                    await interaction.followup.send(
                        "⚠️ Không tìm thấy Role của kênh này. Kiểm tra cấu hình.",
                        ephemeral=True,
                    )
                    return
            except discord.Forbidden:
                await interaction.followup.send(
                    "⚠️ Bot không đủ quyền set Role. Kéo Role Bot lên cao hơn Role kênh.",
                    ephemeral=True,
                )
                return
            except Exception:
                log.exception("Set role lỗi")
        else:
            # PUBLIC mode: không cần role, nhưng vẫn thu hồi role kênh khác nếu có
            try:
                all_managed_role_ids = {
                    d["role_id"] for d in channels_data.values()
                    if d.get("role_id")
                }
                roles_to_remove = [
                    r for r in user.roles
                    if r.id in all_managed_role_ids and r.id not in protected_role_ids
                ]
                if roles_to_remove and not skip_role_removal:
                    await user.remove_roles(*roles_to_remove)
            except discord.Forbidden:
                pass
            except Exception:
                log.exception("Thu hồi role kênh khác lỗi")

        # ==== [3] XÂY DỰNG TEXT TRẠNG THÁI ====
        # CTV chính (chủ) của kênh này
        owners = set(get_channel_owners(channel_id, ctv_data))
        live_now = state.streams()  # {user_id: info} — mọi CTV đang LIVE
        now_check = now_vn()

        # Chủ kênh đang live TẠI kênh này / ở kênh KHÁC
        live_here = {uid: i for uid, i in live_now.items() if uid in owners and i["channel_id"] == channel_id}
        live_elsewhere = {uid: i for uid, i in live_now.items() if uid in owners and i["channel_id"] != channel_id}
        real_live = bool(live_here)

        def _live_line(uid: str, info: dict, show_channel: bool) -> str:
            start_dt = state.start_dt(info)
            dur = fmt_dur((now_check - start_dt).total_seconds())
            where = f" tại **{channel_name(info['channel_id'], channels_data)}**" if show_channel else ""
            return (
                f" - <@{uid}>{where}\n"
                f"   Bắt đầu: `{start_dt.strftime('%H:%M:%S')}` | Đã live: `{dur}`"
            )

        if real_live:
            status_text = "🔴 ĐANG LIVE (Có Stream)"
        elif live_elsewhere:
            status_text = "🔴 ĐANG LIVE Ở KÊNH KHÁC"
        else:
            status_text = "⚫ OFFLINE (Chưa Live)"

        if real_live or live_elsewhere:
            lines = [_live_line(uid, i, False) for uid, i in live_here.items()]
            lines += [_live_line(uid, i, True) for uid, i in live_elsewhere.items()]
            section_title = "Phiên Live hiện tại:" if real_live else "CTV chủ đang live ở kênh khác:"
            section_text = "\n".join(lines)
        else:
            section_title = "Phiên Live gần nhất:"
            last = get_last_stream_session(owners) if owners else None
            section_text = "Chưa có dữ liệu"
            if last:
                dt_s = from_ts(last["start_ts"])
                dt_e = from_ts(last["end_ts"])
                section_text = (
                    f" - **CTV:** <@{last['user_id']}>\n"
                    f" - **Ngày:** `{dt_s.strftime('%d/%m/%Y')}`\n"
                    f" - **Khung giờ:** `{dt_s.strftime('%H:%M')}` đến `{dt_e.strftime('%H:%M')}`\n"
                    f" - **Thời lượng:** `{fmt_dur(last.get('duration', 0))}`"
                )

        # ==== [4] LỊCH LIVE TIẾP THEO ====
        # Lịch gắn theo từng CTV → lấy lịch sớm nhất của các CTV chủ kênh
        nl_data = db.load("next_live")
        next_live_text = ""
        if not real_live and not live_elsewhere:
            upcoming = [
                nl_data[uid] for uid in owners
                if isinstance(nl_data.get(uid), dict) and nl_data[uid].get("time")
            ]
            if upcoming:
                info = min(upcoming, key=lambda x: x["time"])
                when = from_ts(info["time"])
                day_note = "" if when.date() == now_check.date() else f" ngày {when.strftime('%d/%m')}"
                next_live_text = (
                    f"\n**🎀 Phiên Live tiếp theo:** `{info.get('time_str', when.strftime('%H:%M'))}`{day_note}"
                )

        status_line = f"**Trạng thái:** {status_text}{next_live_text}"

        # ==== [5] RENDER EMBED (dùng 2 template khác nhau live/offline) ====
        is_ctv_user = user_id in ctv_data and channel_id in ctv_data[user_id]
        c_name = channels_data[channel_id]["name"]

        # Case treat as "live" cho template chọn: real_live HOẶC live_elsewhere
        show_as_live = real_live or bool(live_elsewhere)

        # Link "Xem Ngay" — kênh đang live thực tế
        if real_live:
            live_link_cid = channel_id
        elif live_elsewhere:
            live_link_cid = next(iter(live_elsewhere.values()))["channel_id"]
        else:
            live_link_cid = channel_id  # fallback

        if is_ctv_user:
            from views.ctv_view import CTVView
            view = CTVView(channel_id, guild.id)
            embed = get_custom_embed(EMBED_CTV_INFO, title="Khu vực CTV", desc=f"Xin chào <@{user.id}>")

            if embed.title:
                embed.title = embed.title.replace("{ten_kenh}", c_name)
            if embed.description:
                embed.description = embed.description.replace("{ten_kenh}", c_name)

            current_desc = embed.description or ""
            embed.description = (
                f"{current_desc}\n\n{status_line}\n\n"
                f"**{section_title}**\n{section_text}"
            )
            await interaction.followup.send(embed=clamp_embed(embed), view=view, ephemeral=True)
        else:
            view = CustomerView(
                channel_id, guild.id, show_as_live, user.id,
                live_link_channel_id=live_link_cid,
            )

            # MỚI: dùng template LIVE nếu live tại đây HOẶC live ở kênh khác
            if show_as_live:
                embed = get_custom_embed(
                    EMBED_CUSTOMER_LIVE,
                    title=f"🔴 {c_name} — Đang LIVE",
                    desc=f"CTV của kênh **{c_name}** đang phát trực tiếp!",
                    color=discord.Color.red(),
                )
            else:
                embed = get_custom_embed(
                    EMBED_CUSTOMER_OFFLINE,
                    title=f"⚫ {c_name} — Offline",
                    desc=f"Kênh **{c_name}** hiện chưa có ai Live.",
                    color=discord.Color.dark_grey(),
                )

            if embed.title:
                embed.title = embed.title.replace("{ten_kenh}", c_name)
            if embed.description:
                embed.description = embed.description.replace("{ten_kenh}", c_name)

            current_desc = embed.description or ""
            embed.description = (
                f"{current_desc}\n\n{status_line}\n\n"
                f"**{section_title}**\n{section_text}"
            )
            await interaction.followup.send(embed=clamp_embed(embed), view=view, ephemeral=True)

        # ==== [6] RESET DROPDOWN ====
        try:
            await interaction.message.edit(view=self.view)
        except Exception:
            pass


class UserSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        channels = db.load("channels")
        options = [
            discord.SelectOption(label=(data.get("name") or f"Kênh {cid}")[:100], value=cid)
            for cid, data in channels.items()
        ]
        if not options:
            options = [discord.SelectOption(label="Chưa có kênh nào", value="none")]

        limit = OPTIONS_PER_SELECT * MAX_SELECTS
        if len(options) > limit:
            log.warning(f"Có {len(options)} kênh — panel khách chỉ hiện được {limit} kênh đầu")
            options = options[:limit]

        groups = [options[i:i + OPTIONS_PER_SELECT] for i in range(0, len(options), OPTIONS_PER_SELECT)]
        for idx, group in enumerate(groups):
            self.add_item(UserChannelSelect(group, idx, len(groups)))
