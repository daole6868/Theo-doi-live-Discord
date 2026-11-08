"""
Flow Quản lý (Xóa/Sửa) Kênh và CTV.

QUẢN LÝ KÊNH:
- Đổi tên (đồng bộ cả voice channel Discord)
- Xóa khỏi Bot (giữ voice channel + role Discord)
- Xóa hoàn toàn (xóa cả voice channel + role Discord)

QUẢN LÝ CTV (chọn bằng ô chọn thành viên của Discord — gõ tên để tìm):
- Bỏ khỏi 1 kênh cụ thể (thu hồi role của kênh đó)
- Xóa hoàn toàn (bỏ khỏi tất cả kênh + thu hồi mọi role liên quan)
- Xóa lịch sử live (cả với người đã nghỉ)

Thao tác nguy hiểm luôn qua ConfirmView (Xác nhận / Hủy).

Lịch sử live KHÔNG bị xóa khi bỏ/xóa CTV hay xóa kênh. Muốn xóa lịch sử của 1 người
phải bấm nút riêng "🗑️ Xóa lịch sử live" và gõ XOA để xác nhận.
"""
import logging

import discord
from discord import ui

from core.data_manager import db
from core.session_store import store
from helpers.panels import refresh_panel, resync_tracking

log = logging.getLogger(__name__)


# =========================================================
#  CONFIRM VIEW (dùng chung)
# =========================================================
class ConfirmView(ui.View):
    def __init__(self, on_confirm):
        super().__init__(timeout=60)
        self.on_confirm = on_confirm

    @ui.button(label="✅ Xác nhận", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: ui.Button):
        # Disable buttons trước (dùng response.edit_message vì đây là ephemeral)
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            log.exception("Disable ConfirmView buttons fail")
        # Sau đó gọi on_confirm — interaction.response đã done, dùng followup
        await self.on_confirm(interaction)

    @ui.button(label="❌ Hủy", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content="🚫 Đã hủy thao tác.",
            embed=None,
            view=None,
        )


# =========================================================
#  QUẢN LÝ KÊNH — dropdown chọn kênh
# =========================================================
class ManageChannelSelect(ui.ChannelSelect):
    """Chọn kênh cần quản lý (gõ để tìm, không giới hạn 25)."""

    def __init__(self):
        super().__init__(
            placeholder="Gõ tên kênh voice cần quản lý...",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        cid = str(self.values[0].id)
        if cid not in db.load("channels"):
            await interaction.response.send_message(
                "⚠️ Kênh này chưa có trong hệ thống bot.", ephemeral=True,
            )
            return

        channels = db.load("channels")
        ctv_data = db.load("ctv")
        subs = db.load("subscribers")

        info = channels.get(cid, {})
        name = info.get("name", "?")
        role_id = info.get("role_id")

        ctv_count = sum(
            1 for uid, chs in ctv_data.items()
            if isinstance(chs, list) and cid in chs
        )
        sub_count = len(subs.get(cid, []))

        embed = discord.Embed(
            title=f"🔧 Quản lý kênh: {name}",
            description=(
                f"**Tên:** {name}\n"
                f"**ID:** `{cid}`\n"
                f"**Role ID:** `{role_id}`\n"
                f"**CTV quản lý:** {ctv_count}\n"
                f"**Subscribers:** {sub_count}\n\n"
                "Chọn thao tác bên dưới:"
            ),
            color=discord.Color.blurple(),
        )
        view = ChannelActionView(cid, name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ManageChannelSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ManageChannelSelect())


# =========================================================
#  KÊNH ACTION VIEW — 3 nút thao tác
# =========================================================
class ChannelActionView(ui.View):
    def __init__(self, channel_id: str, channel_name: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.channel_name = channel_name

    @ui.button(label="✏️ Đổi tên", style=discord.ButtonStyle.primary)
    async def rename_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(
            RenameChannelModal(self.channel_id, self.channel_name)
        )

    @ui.button(label="🗑️ Xóa khỏi Bot", style=discord.ButtonStyle.danger)
    async def remove_bot_only(self, interaction: discord.Interaction, button: ui.Button):
        async def _do(inter: discord.Interaction):
            await self._remove_channel(inter, delete_discord=False)

        embed = discord.Embed(
            title="⚠️ Xác nhận Xóa khỏi Bot",
            description=(
                f"Xóa kênh **{self.channel_name}** khỏi hệ thống Bot?\n\n"
                "**SẼ LÀM:**\n"
                "• Xóa khỏi danh sách kênh của Bot\n"
                "• Xóa tất cả CTV/Subscribers liên kết kênh này\n\n"
                "**KHÔNG động vào:**\n"
                "• Voice Channel Discord (vẫn còn)\n"
                "• Role Discord (vẫn còn)\n"
                "• Lịch sử live (vẫn giữ nguyên)\n"
                "• Phiên đang live (vẫn được tính tiếp)"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed, view=ConfirmView(_do), ephemeral=True,
        )

    @ui.button(label="❌ Xóa HOÀN TOÀN", style=discord.ButtonStyle.danger)
    async def remove_full(self, interaction: discord.Interaction, button: ui.Button):
        async def _do(inter: discord.Interaction):
            await self._remove_channel(inter, delete_discord=True)

        embed = discord.Embed(
            title="🚨 Xác nhận Xóa HOÀN TOÀN",
            description=(
                f"Xóa **HOÀN TOÀN** kênh **{self.channel_name}**?\n\n"
                "**SẼ LÀM:**\n"
                "• Tất cả các mục ở 'Xóa khỏi Bot'\n"
                "• **XÓA VOICE CHANNEL** trong Discord\n"
                "• **XÓA ROLE** trong Discord\n\n"
                "⚠️ **KHÔNG THỂ hoàn tác!**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, view=ConfirmView(_do), ephemeral=True,
        )

    async def _remove_channel(self, interaction: discord.Interaction, delete_discord: bool):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        cid = self.channel_id
        channels = db.load("channels")
        role_id = channels.get(cid, {}).get("role_id")

        done: list[str] = []

        # 1. Xóa khỏi channels
        def _rm_ch(data):
            data.pop(cid, None)
        await db.aupdate("channels", _rm_ch)
        done.append("✓ Xóa khỏi danh sách kênh")

        # 2. Bỏ khỏi CTV (auto-remove CTV nếu list rỗng)
        removed_ctv_count = 0
        def _rm_ctv(data):
            nonlocal removed_ctv_count
            for uid in list(data.keys()):
                if isinstance(data[uid], list) and cid in data[uid]:
                    data[uid].remove(cid)
                    if not data[uid]:
                        del data[uid]
                        removed_ctv_count += 1
        await db.aupdate("ctv", _rm_ctv)
        if removed_ctv_count:
            done.append(f"✓ Xóa {removed_ctv_count} CTV không còn kênh nào")
        else:
            done.append("✓ Bỏ khỏi CTV quản lý")

        # 3. Xóa subscribers
        def _rm_subs(data):
            data.pop(cid, None)
        await db.aupdate("subscribers", _rm_subs)
        done.append("✓ Xóa subscribers")

        # 4. Xóa next_live
        def _rm_nl(data):
            data.pop(cid, None)
        await db.aupdate("next_live", _rm_nl)
        done.append("✓ Xóa lịch live tiếp theo")

        # 5. Xóa Discord voice channel + role (nếu chọn xóa hoàn toàn)
        if delete_discord:
            guild = interaction.guild
            try:
                voice = guild.get_channel(int(cid))
                if voice:
                    await voice.delete(reason=f"Xóa hoàn toàn bởi {interaction.user}")
                    done.append("✓ Xóa Voice Channel Discord")
            except discord.Forbidden:
                done.append("⚠️ Không đủ quyền xóa Voice Channel")
            except Exception as e:
                done.append(f"⚠️ Lỗi xóa Voice Channel: {e}")

            if role_id:
                try:
                    role = guild.get_role(role_id)
                    if role:
                        await role.delete(reason=f"Xóa hoàn toàn bởi {interaction.user}")
                        done.append("✓ Xóa Role Discord")
                except discord.Forbidden:
                    done.append("⚠️ Không đủ quyền xóa Role")
                except Exception as e:
                    done.append(f"⚠️ Lỗi xóa Role: {e}")

        # 6. Auto-refresh panels
        try:
            await refresh_panel(interaction.client, "user")
            await refresh_panel(interaction.client, "check_ctv")
            done.append("🔄 User + Check CTV panel đã cập nhật")
        except Exception:
            log.exception("Refresh panels lỗi")
        await resync_tracking(interaction.client, "xóa kênh")

        result = "\n".join(done)
        await interaction.followup.send(
            f"✅ Đã xóa kênh **{self.channel_name}**.\n\n{result}",
            ephemeral=True,
        )
        log.warning(
            f"Admin {interaction.user} xóa kênh {self.channel_name} "
            f"(id={cid}, delete_discord={delete_discord})"
        )


# =========================================================
#  MODAL: Đổi tên kênh
# =========================================================
class RenameChannelModal(ui.Modal):
    def __init__(self, channel_id: str, current_name: str):
        super().__init__(title=f"Đổi tên: {current_name[:35]}")
        self.channel_id = channel_id
        self.current_name = current_name

        self.new_name_input = ui.TextInput(
            label="Tên mới",
            default=current_name,
            max_length=100,
            required=True,
        )
        self.add_item(self.new_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        new_name = self.new_name_input.value.strip()
        if not new_name:
            await interaction.followup.send("⚠️ Tên không được để trống.", ephemeral=True)
            return

        if new_name == self.current_name:
            await interaction.followup.send("ℹ️ Tên không thay đổi.", ephemeral=True)
            return

        # Update DB
        def _save(data):
            if self.channel_id in data:
                data[self.channel_id]["name"] = new_name
        await db.aupdate("channels", _save)

        # Đổi tên cả voice channel Discord (nếu có quyền)
        renamed_discord = False
        try:
            voice = interaction.guild.get_channel(int(self.channel_id))
            if voice:
                await voice.edit(name=new_name, reason=f"Đổi tên bởi {interaction.user}")
                renamed_discord = True
        except discord.Forbidden:
            log.warning(f"Không đủ quyền đổi tên voice channel {self.channel_id}")
        except Exception:
            log.exception("Đổi tên voice channel lỗi")

        # Auto-refresh panels
        try:
            await refresh_panel(interaction.client, "user")
            await refresh_panel(interaction.client, "check_ctv")
        except Exception:
            log.exception("Refresh panels lỗi")

        extra = "\n✓ Đã đổi tên cả Voice Channel Discord" if renamed_discord else ""
        await interaction.followup.send(
            f"✅ Đã đổi tên: **{self.current_name}** → **{new_name}**{extra}\n"
            "🔄 Panels đã cập nhật.",
            ephemeral=True,
        )
        log.info(
            f"Admin {interaction.user} đổi tên kênh {self.channel_id}: "
            f"{self.current_name} → {new_name}"
        )


# =========================================================
#  QUẢN LÝ CTV — dropdown chọn CTV
# =========================================================
class ManageCTVSelect(ui.UserSelect):
    """Chọn CTV cần quản lý (gõ tên để tìm, không giới hạn 25)."""

    def __init__(self):
        super().__init__(placeholder="Gõ tên CTV cần quản lý...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        picked = self.values[0]
        uid = str(picked.id)
        ctv_data = db.load("ctv")
        channels_data = db.load("channels")
        display = f"{picked.display_name} (`{uid}`)"

        if uid not in ctv_data:
            if store.has_history(uid):
                embed = discord.Embed(
                    title="🔧 Quản lý CTV",
                    description=(
                        f"**{display}** không còn là CTV nhưng vẫn còn lịch sử live.\n"
                        "Bạn có thể xóa lịch sử nếu cần."
                    ),
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(
                    embed=embed, view=HistoryOnlyView(uid), ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"ℹ️ **{display}** không phải CTV.", ephemeral=True,
                )
            return

        channel_list = ctv_data.get(uid, [])
        if not isinstance(channel_list, list):
            channel_list = []
        channels_info = "\n".join(
            f"• **{channels_data.get(cid, {}).get('name', '?')}** (id: `{cid}`)"
            for cid in channel_list
        ) or "*(chưa có kênh nào)*"

        embed = discord.Embed(
            title="🔧 Quản lý CTV",
            description=(
                f"**CTV:** {display}\n"
                f"**Đang quản {len(channel_list)} kênh:**\n{channels_info}\n\n"
                "Chọn thao tác bên dưới:"
            ),
            color=discord.Color.blurple(),
        )
        view = CTVActionView(uid, channel_list, channels_data)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ManageCTVSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ManageCTVSelect())


# =========================================================
#  CTV ACTION VIEW — nút cho từng kênh + nút xóa hoàn toàn
# =========================================================
class RemoveFromChannelSelect(ui.Select):
    """Chọn 1 kênh để bỏ CTV khỏi kênh đó."""

    def __init__(self, action_view: "CTVActionView", channel_list: list, channels_data: dict):
        options = [
            discord.SelectOption(
                label=f"Bỏ khỏi: {channels_data.get(cid, {}).get('name', '?')}"[:100],
                value=str(cid),
                description=f"ID: {cid}",
            )
            for cid in channel_list[:25]
        ]
        super().__init__(placeholder="Bỏ CTV khỏi 1 kênh...", options=options, row=0)
        self.action_view = action_view
        self.names = {str(cid): channels_data.get(cid, {}).get("name", "?") for cid in channel_list}

    async def callback(self, interaction: discord.Interaction):
        cid = self.values[0]
        handler = self.action_view._make_remove_from_channel_handler(cid, self.names.get(cid, "?"))
        await handler(interaction)


class CTVActionView(ui.View):
    def __init__(self, user_id: str, channel_list: list, channels_data: dict):
        super().__init__(timeout=300)
        self.user_id = user_id

        if channel_list:
            self.add_item(RemoveFromChannelSelect(self, channel_list, channels_data))

        full_btn = ui.Button(
            label="❌ Xóa CTV HOÀN TOÀN",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        full_btn.callback = self._remove_full_handler
        self.add_item(full_btn)

        history_btn = ui.Button(
            label="🗑️ Xóa lịch sử live",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        history_btn.callback = self._delete_history_handler
        self.add_item(history_btn)

    async def _delete_history_handler(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DeleteHistoryModal(self.user_id))

    def _make_remove_from_channel_handler(self, channel_id: str, channel_name: str):
        async def _handler(interaction: discord.Interaction):
            async def _do(inter):
                await self._remove_from_channel(inter, channel_id, channel_name)

            embed = discord.Embed(
                title="⚠️ Xác nhận",
                description=(
                    f"Bỏ CTV <@{self.user_id}> khỏi kênh **{channel_name}**?\n\n"
                    "**SẼ LÀM:**\n"
                    "• Bỏ kênh khỏi danh sách quản lý của CTV\n"
                    "• Thu hồi Role của kênh này khỏi user\n"
                    "• Nếu CTV không còn quản kênh nào → tự xóa CTV khỏi hệ thống"
                ),
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(
                embed=embed, view=ConfirmView(_do), ephemeral=True,
            )
        return _handler

    async def _remove_from_channel(
        self,
        interaction: discord.Interaction,
        cid: str,
        cname: str,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # Lookup role_id TRƯỚC khi mutate DB
        role_id = db.load("channels").get(cid, {}).get("role_id")

        # 1. Bỏ khỏi ctv data
        removed_all = False

        def _save(data):
            nonlocal removed_all
            if self.user_id in data and isinstance(data[self.user_id], list):
                if cid in data[self.user_id]:
                    data[self.user_id].remove(cid)
                if not data[self.user_id]:
                    del data[self.user_id]
                    removed_all = True

        await db.aupdate("ctv", _save)

        # 2. Thu hồi role
        role_removed = False
        try:
            member = interaction.guild.get_member(int(self.user_id))
            if member and role_id:
                role = interaction.guild.get_role(role_id)
                if role and role in member.roles:
                    await member.remove_roles(role, reason=f"Bỏ CTV bởi {interaction.user}")
                    role_removed = True
        except discord.Forbidden:
            log.warning("Không đủ quyền thu hồi role")
        except Exception:
            log.exception("Thu hồi role lỗi")

        # 3. Refresh + đối chiếu lại bộ tính giờ (ngừng tính nếu không còn là CTV)
        try:
            await refresh_panel(interaction.client, "check_ctv")
        except Exception:
            log.exception("Refresh check_ctv panel lỗi")
        await resync_tracking(interaction.client, "cập nhật CTV")

        extra = "\n✓ Đã xóa CTV khỏi hệ thống (không còn kênh nào)" if removed_all else ""
        role_text = "\n✓ Đã thu hồi role" if role_removed else "\n⚠️ Không thu hồi được role (thiếu quyền hoặc user không có role)"
        await interaction.followup.send(
            f"✅ Đã bỏ CTV <@{self.user_id}> khỏi kênh **{cname}**.{role_text}{extra}\n"
            "📚 Lịch sử live vẫn được giữ nguyên.\n"
            "🔄 Check CTV panel đã cập nhật.",
            ephemeral=True,
        )
        log.info(f"Admin {interaction.user} bỏ CTV {self.user_id} khỏi kênh {cid}")

    async def _remove_full_handler(self, interaction: discord.Interaction):
        async def _do(inter):
            await self._do_remove_full(inter)

        embed = discord.Embed(
            title="🚨 Xác nhận Xóa HOÀN TOÀN",
            description=(
                f"Xóa **HOÀN TOÀN** CTV <@{self.user_id}>?\n\n"
                "**SẼ LÀM:**\n"
                "• Bỏ CTV khỏi TẤT CẢ kênh đang quản lý\n"
                "• Thu hồi TẤT CẢ role liên quan\n"
                "• Xóa CTV khỏi danh sách bot (bot ngừng tính giờ)\n\n"
                "**KHÔNG động vào:** lịch sử live (vẫn xem được trong Check CTV).\n"
                "Muốn xóa lịch sử → dùng nút **🗑️ Xóa lịch sử live**."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, view=ConfirmView(_do), ephemeral=True,
        )

    async def _do_remove_full(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        ctv_data = db.load("ctv")
        channel_list = list(ctv_data.get(self.user_id, []))

        # 1. Xóa CTV
        def _save(data):
            data.pop(self.user_id, None)
        await db.aupdate("ctv", _save)

        # 2. Thu hồi tất cả role
        channels_data = db.load("channels")
        role_removed = role_failed = 0
        try:
            member = interaction.guild.get_member(int(self.user_id))
            if member:
                for cid in channel_list:
                    role_id = channels_data.get(cid, {}).get("role_id")
                    if not role_id:
                        continue
                    role = interaction.guild.get_role(role_id)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason=f"Xóa CTV bởi {interaction.user}")
                            role_removed += 1
                        except Exception:
                            role_failed += 1
        except Exception:
            log.exception("Thu hồi role lỗi")

        # 3. Refresh + đối chiếu lại bộ tính giờ (ngừng tính nếu không còn là CTV)
        try:
            await refresh_panel(interaction.client, "check_ctv")
        except Exception:
            log.exception("Refresh check_ctv panel lỗi")
        await resync_tracking(interaction.client, "cập nhật CTV")

        await interaction.followup.send(
            f"✅ Đã xóa hoàn toàn CTV <@{self.user_id}>.\n"
            f"✓ Bỏ khỏi {len(channel_list)} kênh\n"
            f"✓ Thu hồi {role_removed} role (fail: {role_failed})\n"
            "📚 Lịch sử live vẫn được giữ nguyên.\n"
            "🔄 Check CTV panel đã cập nhật.",
            ephemeral=True,
        )
        log.warning(f"Admin {interaction.user} xóa hoàn toàn CTV {self.user_id}")


class HistoryOnlyView(ui.View):
    """Người đã nghỉ (không còn là CTV) nhưng còn lịch sử."""

    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id

    @ui.button(label="🗑️ Xóa lịch sử live", style=discord.ButtonStyle.danger)
    async def delete_history(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(DeleteHistoryModal(self.user_id))


# =========================================================
#  MODAL: Xóa lịch sử live (phải gõ XOA để xác nhận)
# =========================================================
class DeleteHistoryModal(ui.Modal, title="Xóa lịch sử live"):
    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = str(user_id)
        self.confirm_input = ui.TextInput(
            label="Gõ XOA để xác nhận (không thể hoàn tác)",
            placeholder="XOA",
            max_length=10,
            required=True,
        )
        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction):
        typed = self.confirm_input.value.strip().upper().replace("Ó", "O")
        if typed != "XOA":
            await interaction.response.send_message(
                "🚫 Chưa xóa — bạn cần gõ đúng **XOA**.", ephemeral=True,
            )
            return

        removed = store.delete_user(self.user_id)
        await interaction.response.send_message(
            f"✅ Đã xóa **{removed}** dòng lịch sử live/audio của <@{self.user_id}>.",
            ephemeral=True,
        )
        log.warning(f"Admin {interaction.user} xóa lịch sử live của {self.user_id} ({removed} dòng)")
