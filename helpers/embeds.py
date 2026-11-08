"""
Factory tạo discord.Embed từ template trong data_embeds.json.

Cho phép Admin sửa Title/Description/Image/Footer qua Modal mà không
cần đụng code.
"""
import discord

from core.data_manager import db


def get_custom_embed(
    key: str,
    title: str = "Tiêu đề mặc định",
    desc: str = "Nội dung mặc định",
    color: discord.Color | None = None,
) -> discord.Embed:
    """
    Load embed template `key` từ DB. Nếu chưa có → dùng title/desc mặc định.

    Args:
        key: Ví dụ 'embed_main_select', 'embed_live_notif', ...
        title: Fallback nếu Admin chưa cấu hình title.
        desc: Fallback nếu Admin chưa cấu hình description.
        color: Màu embed (default: xanh dương).
    """
    data = db.load("embeds").get(key, {})

    embed = discord.Embed(
        title=data.get("title") or title,
        description=data.get("description") or desc,
        color=color or discord.Color.blue(),
    )

    if data.get("image"):
        embed.set_image(url=data["image"])
    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])

    embed.set_footer(text=data.get("footer") or "Hệ thống Bot Discord")
    return embed
