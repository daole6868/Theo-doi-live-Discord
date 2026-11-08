"""
Factory tạo discord.Embed từ template trong data_embeds.json.

Cho phép Admin sửa Title/Description/Image/Footer qua Modal mà không
cần đụng code.

An toàn: link ảnh không hợp lệ bị bỏ qua và độ dài được cắt theo giới hạn
của Discord, để panel / DM báo live không bao giờ bị Discord từ chối.
"""
import discord

from core.data_manager import db

# Giới hạn của Discord
TITLE_MAX = 256
DESCRIPTION_MAX = 4096
FOOTER_MAX = 2048
TOTAL_MAX = 6000

# Độ dài tối đa admin được nhập cho phần nội dung (chừa chỗ cho bot ghép trạng thái)
TEMPLATE_DESCRIPTION_MAX = 2000


def is_valid_image_url(url: str | None) -> bool:
    if not url:
        return False
    url = url.strip()
    return url.startswith(("http://", "https://")) and " " not in url and len(url) <= 2000


def _cut(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def clamp_embed(embed: discord.Embed) -> discord.Embed:
    """Cắt title/description/footer cho vừa giới hạn Discord."""
    embed.title = _cut(embed.title, TITLE_MAX)
    embed.description = _cut(embed.description, DESCRIPTION_MAX)
    if embed.footer and embed.footer.text:
        embed.set_footer(text=_cut(embed.footer.text, FOOTER_MAX), icon_url=embed.footer.icon_url)
    if len(embed) > TOTAL_MAX and embed.description:
        over = len(embed) - TOTAL_MAX
        embed.description = _cut(embed.description, max(1, len(embed.description) - over - 1))
    return embed


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

    if is_valid_image_url(data.get("image")):
        embed.set_image(url=data["image"].strip())
    if is_valid_image_url(data.get("thumbnail")):
        embed.set_thumbnail(url=data["thumbnail"].strip())

    embed.set_footer(text=data.get("footer") or "Hệ thống Bot Discord")
    return clamp_embed(embed)
