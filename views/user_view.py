"""
View cho khách hàng: dropdown chọn kênh + hiển thị trạng thái Live/Off.

FIX (batch 4A):
- Dùng 2 embed key khác nhau cho CTV đang LIVE và OFFLINE (theo yêu cầu #4).
- Cấu trúc subscribers giữ nguyên keyed by channel_id (không breaking),
  nhưng logic notify đã đổi trong live_tracker_cog: khi CTV Y live ở kênh nào đó,
  bot sẽ notify tất cả subs của các kênh Y quản lý (yêu cầu #3).
"""
import datetime
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
from helpers.embeds import get_custom_embed
from helpers.stats import create_channel_report, get_last_session
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

        subs = db.load("subscribers")
        is_subscribed = self._user_in_subs(subs.get(self.channel_id, []))

        if is_subscribed:
            self.notify_btn = ui.Button(label="Tắt Thông Báo", style=discord.ButtonStyle.danger)
        else:
            self.notify_btn = ui.Button(label="Bật Thông Báo", style=discord.ButtonStyle.success)
        self.notify_btn.callback = self.toggle_notify
        self.add_item(self.notify_btn)

        self.check_btn = ui.Button(label="Check Phiên Live", style=discord.ButtonStyle.secondary)
        self.check_btn.callback = self.check_stats
        self.add_item(self.check_btn)

    def _user_in_subs(self, subs_list) -> bool:
        uid_str = str(self.user_id)
        return any(str(u) == uid_str for u in subs_list)

    async def toggle_notify(self, interaction: discord.Interaction):
        uid_str = str(interaction.user.id)
        turned_on = None

        def _save(subs):
            nonlocal turned_on
            if self.channel_id not in subs:
                subs[self.channel_id] = []
            lst = subs[self.channel_id]
            existing = [u for u in lst if str(u) == uid_str]
            if existing:
                for e in existing:
                    lst.remove(e)
                turned_on = False
            else:
                lst.append(uid_str)
                turned_on = True

        await db.aupdate("subscribers", _save)

        if turned_on:
            self.notify_btn.label = "Tắt Thông Báo"
            self.notify_btn.style = discord.ButtonStyle.danger
            msg = "✅ Đã BẬT thông báo. Bạn sẽ nhận DM khi CTV của kênh này Live (kể cả họ live ở kênh khác)."
        else:
            self.notify_btn.label = "Bật Thông Báo"
            self.notify_btn.style = discord.ButtonStyle.success
            msg = "🔕 Đã TẮT thông báo kênh này."

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

    async def check_stats(self, interaction: discord.Interaction):
        embed = create_channel_report(self.channel_id, 1, "Hôm Nay")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
#  DROPDOWN CHỌN KÊNH — PERSISTENT
# =========================================================
class UserChannelSelect(ui.Select):
    def __init__(self):
        channels = db.load("channels")
        options: list[discord.SelectOption] = []
        if not channels:
            options.append(discord.SelectOption(label="Chưa có kênh nào", value="none"))
        else:
            for cid, data in channels.items():
                options.append(discord.SelectOption(
                    label=data.get("name", f"Kênh {cid}"),
                    value=cid,
                ))
        options = options[:25]

        super().__init__(
            placeholder="Chọn kênh để theo dõi...",
            min_values=1,
            max_values=1,
            custom_id="user_channel_select",
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
                ]
                if roles_to_remove:
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
                    if r.id in all_managed_role_ids
                ]
                if roles_to_remove:
                    await user.remove_roles(*roles_to_remove)
            except discord.Forbidden:
                pass
            except Exception:
                log.exception("Thu hồi role kênh khác lỗi")

        # ==== [3] XÂY DỰNG TEXT TRẠNG THÁI ====
        is_live = state.is_live(channel_id)
        current_streamer_id = state.get_user(channel_id) if is_live else None

        # CTV chính (chủ) của kênh này
        owners = {uid for uid, chans in ctv_data.items() if isinstance(chans, list) and channel_id in chans}

        # Real live TẠI kênh này = có streamer VÀ streamer là chủ kênh
        real_live = is_live and str(current_streamer_id) in owners

        # MỚI: Nếu không live TẠI kênh này → tìm CTV chủ đang live ở kênh KHÁC
        live_elsewhere = []  # list of {cid, user_id, channel_name, start_dt, duration_sec}
        if not real_live and owners:
            now_check = now_vn()
            for cid_other, info in state.snapshot().items():
                if cid_other == channel_id:
                    continue
                streamer_uid = str(info.get("user_id", ""))
                if streamer_uid in owners:
                    start_dt = from_ts(info["start_ts"])
                    duration_sec = (now_check - start_dt).total_seconds()
                    c_name_other = channels_data.get(cid_other, {}).get(
                        "name", f"Kênh {cid_other}"
                    )
                    live_elsewhere.append({
                        "cid": cid_other,
                        "user_id": streamer_uid,
                        "channel_name": c_name_other,
                        "start_dt": start_dt,
                        "duration_sec": duration_sec,
                    })

        # Status text
        if real_live:
            status_text = "🔴 ĐANG LIVE (Có Stream)"
        elif live_elsewhere:
            status_text = "🔴 ĐANG LIVE Ở KÊNH KHÁC"
        else:
            status_text = "⚫ OFFLINE (Chưa Live)"

        # Section chi tiết
        if real_live:
            start = state.get_start(channel_id)
            now = now_vn()
            current_duration = fmt_dur((now - start).total_seconds())
            section_title = "Phiên Live hiện tại:"
            section_text = (
                f" - **Đang phát trực tiếp từ:** `{start.strftime('%H:%M:%S')}`\n"
                f" - **Thời gian đã Live:** `{current_duration}`\n"
                f" - **CTV:** <@{current_streamer_id}>\n"
                f" - **Kênh:** {channels_data[channel_id]['name']} (chính là kênh này)"
            )
        elif live_elsewhere:
            section_title = "CTV chủ đang live ở kênh khác:"
            lines = []
            for item in live_elsewhere:
                lines.append(
                    f" - <@{item['user_id']}> tại **{item['channel_name']}**\n"
                    f"   Bắt đầu: `{item['start_dt'].strftime('%H:%M:%S')}` "
                    f"| Đã live: `{fmt_dur(item['duration_sec'])}`"
                )
            section_text = "\n".join(lines)
        else:
            section_title = "Phiên Live gần nhất:"
            last = get_last_session(channel_id)
            section_text = "Chưa có dữ liệu"
            if last:
                ts_start = last.get("start_time", last.get("start"))
                ts_end = last.get("end_time", last.get("end"))
                if ts_start and ts_end:
                    dt_s = from_ts(ts_start)
                    dt_e = from_ts(ts_end)
                    dur = fmt_dur(last.get("duration", 0))
                    section_text = (
                        f" - **Ngày:** `{dt_s.strftime('%d/%m/%Y')}`\n"
                        f" - **Khung giờ:** `{dt_s.strftime('%H:%M')}` đến `{dt_e.strftime('%H:%M')}`\n"
                        f" - **Tổng thời lượng:** `{dur}`"
                    )

        # ==== [4] LỊCH LIVE TIẾP THEO ====
        nl_data = db.load("next_live")
        next_live_text = ""
        if not real_live and channel_id in nl_data:
            info = nl_data[channel_id]
            if isinstance(info, dict):
                next_live_text = f"\n**🎀 Phiên Live tiếp theo:** `{info.get('time_str', 'Chưa rõ')}`"
            else:
                next_live_text = f"\n**🎀 Phiên Live tiếp theo:** `{info}`"

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
            live_link_cid = live_elsewhere[0]["cid"]
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
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
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
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        # ==== [6] RESET DROPDOWN ====
        try:
            await interaction.message.edit(view=self.view)
        except Exception:
            pass


class UserSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(UserChannelSelect())
