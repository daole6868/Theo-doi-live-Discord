"""
Flow Thêm Kênh và Thêm CTV.

THÊM KÊNH:
1. Admin bấm "Thêm Kênh" -> ephemeral 2 nút [Tạo Mới] [Sẵn Có]
2. Modal nhập tên / ID
3. Ephemeral 2 nút [🔒 Riêng tư] [🌐 Công khai]
4. Bot tạo/thêm theo chế độ -> auto-refresh user panel

THÊM CTV (ephemeral tại kênh admin):
1. Admin bấm "Thêm CTV" -> dropdown chọn kênh
2. Chọn kênh -> nút "Nhập ID CTV"
3. Modal nhập user_id -> add CTV (chỉ gán role nếu kênh Riêng tư)
"""
import logging

import discord
from discord import ui

from config import TARGET_CATEGORY_ID
from core.data_manager import db
from helpers.panels import refresh_panel

log = logging.getLogger(__name__)


MODE_DESC = (
    "🔒 **Riêng tư** - Bot tạo Role riêng, chỉ user được cấp Role mới vào được\n"
    "🌐 **Công khai** - Không cần Role, mọi thành viên vào được"
)


# =========================================================
#  MODAL: Nhập tên kênh mới
# =========================================================
class AddChannelNewModal(ui.Modal, title="Tạo Kênh Mới - Bước 1/2"):
    channel_name = ui.TextInput(
        label="Tên Kênh",
        placeholder="Nhập tên kênh muốn tạo...",
        max_length=100,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.channel_name.value.strip()
        if not name:
            await interaction.response.send_message("⚠️ Tên kênh không được để trống.", ephemeral=True)
            return

        if interaction.guild.get_channel(TARGET_CATEGORY_ID) is None:
            await interaction.response.send_message(
                f"⚠️ LỖI: Bot không tìm thấy Danh mục ID `{TARGET_CATEGORY_ID}`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"➕ Tạo kênh mới: {name}",
            description=f"**Bước 2/2:** Chọn chế độ của kênh:\n\n{MODE_DESC}",
            color=discord.Color.blurple(),
        )
        view = ChannelModeSelectView(value=name, is_new=True)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            log.exception("Edit ephemeral lỗi")
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# =========================================================
#  MODAL: Nhập ID kênh sẵn có
# =========================================================
class AddExistingChannelModal(ui.Modal, title="Thêm Kênh Sẵn Có - Bước 1/2"):
    channel_id_input = ui.TextInput(
        label="ID Kênh Voice sẵn có",
        placeholder="Bật Developer Mode -> Chuột phải kênh -> Copy ID",
        max_length=25,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        chan_id_str = self.channel_id_input.value.strip()
        if not chan_id_str.isdigit():
            await interaction.response.send_message("⚠️ ID phải là số.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(int(chan_id_str))
        if not channel:
            await interaction.response.send_message(
                f"⚠️ Không tìm thấy kênh ID `{chan_id_str}` trong server.",
                ephemeral=True,
            )
            return

        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                f"⚠️ Kênh `{channel.name}` không phải Voice Channel.",
                ephemeral=True,
            )
            return

        if chan_id_str in db.load("channels"):
            await interaction.response.send_message(
                f"⚠️ Kênh **{channel.name}** đã có trong hệ thống rồi.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📎 Thêm kênh sẵn có: {channel.name}",
            description=(
                f"ID: `{chan_id_str}`\n\n"
                f"**Bước 2/2:** Chọn chế độ của kênh:\n\n{MODE_DESC}\n\n"
                "*Bot sẽ thiết lập lại quyền của kênh theo lựa chọn*"
            ),
            color=discord.Color.blurple(),
        )
        view = ChannelModeSelectView(value=chan_id_str, is_new=False)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            log.exception("Edit ephemeral lỗi")
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# =========================================================
#  VIEW: Chọn chế độ Riêng tư / Công khai
# =========================================================
class ChannelModeSelectView(ui.View):
    """Bước cuối cho cả Tạo Mới và Sẵn Có."""

    def __init__(self, value: str, is_new: bool):
        super().__init__(timeout=300)
        self.value = value      # tên kênh (is_new) hoặc channel_id (sẵn có)
        self.is_new = is_new

    @ui.button(label="🔒 Riêng tư (có Role)", style=discord.ButtonStyle.blurple)
    async def private_mode(self, interaction: discord.Interaction, button: ui.Button):
        await self._process(interaction, is_public=False)

    @ui.button(label="🌐 Công khai (mọi người vào)", style=discord.ButtonStyle.green)
    async def public_mode(self, interaction: discord.Interaction, button: ui.Button):
        await self._process(interaction, is_public=True)

    async def _process(self, interaction: discord.Interaction, is_public: bool):
        # Khóa nút tránh bấm 2 lần
        for c in self.children:
            c.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

        if self.is_new:
            await self._create_new(interaction, is_public)
        else:
            await self._add_existing(interaction, is_public)

    async def _create_new(self, interaction: discord.Interaction, is_public: bool):
        guild = interaction.guild
        name = self.value

        category = guild.get_channel(TARGET_CATEGORY_ID)
        if category is None:
            await interaction.followup.send(
                f"⚠️ Không tìm thấy Danh mục ID `{TARGET_CATEGORY_ID}`.",
                ephemeral=True,
            )
            return

        role = None
        if is_public:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
            }
        else:
            try:
                role = await guild.create_role(name=f"Role-{name}")
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Bot không đủ quyền tạo Role.", ephemeral=True)
                return
            except Exception as e:
                log.exception("Tạo role lỗi")
                await interaction.followup.send(f"⚠️ LỖI: {e}", ephemeral=True)
                return
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(view_channel=True, connect=True),
            }

        try:
            channel = await guild.create_voice_channel(name, overwrites=overwrites, category=category)
        except Exception as e:
            log.exception("Tạo voice channel lỗi")
            if role:
                try:
                    await role.delete(reason="Rollback do tạo kênh lỗi")
                except Exception:
                    pass
            await interaction.followup.send(f"⚠️ LỖI: {e}", ephemeral=True)
            return

        def _save(data):
            data[str(channel.id)] = {
                "name": name,
                "role_id": role.id if role else 0,  # 0 = công khai
            }

        await db.aupdate("channels", _save)
        await refresh_panel(interaction.client, "user")

        mode_label = "🌐 Công khai" if is_public else "🔒 Riêng tư"
        await interaction.followup.send(
            f"✅ Đã tạo kênh **{name}** trong danh mục **{category.name}**.\n"
            f"Chế độ: **{mode_label}**\n"
            "🔄 User panel đã tự cập nhật.",
            ephemeral=True,
        )
        log.info(f"Admin {interaction.user} tạo kênh mới: {name} (id={channel.id}, public={is_public})")

    async def _add_existing(self, interaction: discord.Interaction, is_public: bool):
        guild = interaction.guild
        chan_id_str = self.value
        channel = guild.get_channel(int(chan_id_str))

        if not channel:
            await interaction.followup.send(
                f"⚠️ Không tìm thấy kênh ID `{chan_id_str}` (có thể đã bị xóa).",
                ephemeral=True,
            )
            return

        role = None
        perm_warning = ""
        if is_public:
            try:
                await channel.set_permissions(guild.default_role, view_channel=True, connect=True)
            except discord.Forbidden:
                perm_warning = "\n⚠️ Bot không đủ quyền chỉnh permission kênh, hãy tự set lại."
            except Exception:
                log.exception("Set permission public lỗi")
        else:
            try:
                role = await guild.create_role(name=f"Role-{channel.name}")
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Bot không đủ quyền tạo Role.", ephemeral=True)
                return
            except Exception as e:
                log.exception("Tạo role lỗi")
                await interaction.followup.send(f"⚠️ Lỗi tạo role: {e}", ephemeral=True)
                return
            try:
                await channel.set_permissions(guild.default_role, view_channel=False)
                await channel.set_permissions(role, view_channel=True, connect=True)
            except discord.Forbidden:
                perm_warning = "\n⚠️ Bot không đủ quyền chỉnh permission kênh, hãy tự set lại."
            except Exception:
                log.exception("Set permission private lỗi")

        def _save(data):
            data[chan_id_str] = {
                "name": channel.name,
                "role_id": role.id if role else 0,
            }

        await db.aupdate("channels", _save)
        await refresh_panel(interaction.client, "user")

        mode_label = "🌐 Công khai" if is_public else "🔒 Riêng tư"
        await interaction.followup.send(
            f"✅ Đã thêm kênh sẵn có **{channel.name}**.\n"
            f"Chế độ: **{mode_label}**{perm_warning}\n"
            "🔄 User panel đã tự cập nhật.",
            ephemeral=True,
        )
        log.info(f"Admin {interaction.user} thêm kênh sẵn có: {channel.name} ({chan_id_str}), public={is_public}")


# =========================================================
#  VIEW: Chọn cách thêm kênh
# =========================================================
class AddChannelChoiceView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label="🆕 Tạo Kênh Mới", style=discord.ButtonStyle.green)
    async def new_channel_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddChannelNewModal())

    @ui.button(label="📎 Thêm Kênh Sẵn Có", style=discord.ButtonStyle.blurple)
    async def existing_channel_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddExistingChannelModal())


# =========================================================
#  FLOW THÊM CTV
# =========================================================
class AddCTVSimpleModal(ui.Modal):
    def __init__(self, channel_id: str, channel_name: str):
        super().__init__(title=f"Thêm CTV: {channel_name[:35]}")
        self.channel_id = channel_id

        self.user_input = ui.TextInput(
            label=f"ID CTV cho kênh {channel_name[:30]}",
            placeholder="Copy User ID (bật Developer Mode)...",
            max_length=25,
            required=True,
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = self.user_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        if not user_id.isdigit():
            await interaction.followup.send("⚠️ ID phải là số.", ephemeral=True)
            return

        channels_data = db.load("channels")
        if self.channel_id not in channels_data:
            await interaction.followup.send("⚠️ Kênh không tồn tại. Có thể đã bị xóa.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(int(user_id))
        if not member:
            await interaction.followup.send("⚠️ Không tìm thấy user này trong server.", ephemeral=True)
            return

        ctv_data = db.load("ctv")
        if user_id in ctv_data and self.channel_id in ctv_data[user_id]:
            await interaction.followup.send("⚠️ User này đã là CTV kênh này rồi.", ephemeral=True)
            return

        # Chỉ gán role nếu kênh Riêng tư (role_id > 0)
        role_id = channels_data[self.channel_id].get("role_id", 0)
        role_added = False
        if role_id:
            role = guild.get_role(role_id)
            if not role:
                await interaction.followup.send(
                    f"⚠️ Không tìm thấy Role ID {role_id}. Role kênh có thể đã bị xóa.",
                    ephemeral=True,
                )
                return
            try:
                await member.add_roles(role)
                role_added = True
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Bot không đủ quyền set Role.", ephemeral=True)
                return

        def _save(data):
            data.setdefault(user_id, [])
            if self.channel_id not in data[user_id]:
                data[user_id].append(self.channel_id)

        await db.aupdate("ctv", _save)
        await refresh_panel(interaction.client, "check_ctv")

        c_name = channels_data[self.channel_id].get("name", "?")
        role_text = "\n✓ Đã gán Role kênh" if role_added else "\n*(Kênh công khai, không cần Role)*"
        await interaction.followup.send(
            f"✅ Đã thêm CTV <@{user_id}> tại kênh **{c_name}**.{role_text}\n"
            "🔄 Check CTV panel đã cập nhật.",
            ephemeral=True,
        )
        log.info(f"Admin {interaction.user} thêm CTV {user_id} vào kênh {self.channel_id} (role_added={role_added})")


class AddCTVForChannelView(ui.View):
    """Bước 2: nút mở modal nhập user_id."""

    def __init__(self, channel_id: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id

        btn = ui.Button(label="➕ Nhập ID CTV", style=discord.ButtonStyle.green)
        btn.callback = self._open_modal
        self.add_item(btn)

    async def _open_modal(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ Chỉ Admin mới được thêm CTV.", ephemeral=True)
            return
        channel_name = db.load("channels").get(self.channel_id, {}).get("name", "?")
        await interaction.response.send_modal(AddCTVSimpleModal(self.channel_id, channel_name))


class AddCTVChannelSelect(ui.Select):
    """Bước 1: dropdown chọn kênh."""

    def __init__(self):
        channels = db.load("channels")
        options: list[discord.SelectOption] = []
        if not channels:
            options.append(discord.SelectOption(label="Chưa có kênh nào", value="none"))
        else:
            for cid, data in channels.items():
                is_public = not data.get("role_id", 0)
                options.append(discord.SelectOption(
                    label=data.get("name", f"Kênh {cid}")[:100],
                    value=cid,
                    description="🌐 Công khai" if is_public else "🔒 Riêng tư",
                ))
        super().__init__(
            placeholder="Chọn kênh để thêm CTV...",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        cid_str = self.values[0]
        if cid_str == "none":
            await interaction.response.send_message("⚠️ Chưa có kênh nào. Hãy Thêm Kênh trước!", ephemeral=True)
            return

        channel_name = db.load("channels").get(cid_str, {}).get("name", "?")
        embed = discord.Embed(
            title=f"➕ Thêm CTV cho kênh {channel_name}",
            description=(
                f"**Bước 2/2:** Nhập ID CTV cho kênh **{channel_name}**\n\n"
                "Bấm nút bên dưới để mở form nhập User ID."
            ),
            color=discord.Color.green(),
        )
        view = AddCTVForChannelView(cid_str)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            log.exception("Edit ephemeral lỗi, fallback send_message")
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AddCTVChannelSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AddCTVChannelSelect())
