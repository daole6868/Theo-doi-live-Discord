"""
Tính toán thống kê session.

DESIGN (post batch refactor):
- create_channel_report (User/CTV bấm Check): aggregate TẤT CẢ session của CTV chủ kênh
  đó (bất kể ở kênh nào). Đánh dấu 🏠 (tại kênh này) / 🌐 (kênh khác).
- create_ctv_report (Admin bấm Check CTV): aggregate theo user_id, giữ nguyên.
- build_leaderboard: BXH theo CTV, cộng thời gian mọi kênh (kể cả kênh khác).
"""
import datetime
import logging

import discord

from config import CHECK_RANGE_OPTIONS
from core.data_manager import db
from helpers.time_utils import from_ts, fmt_dur, now_vn, start_of_day_vn

log = logging.getLogger(__name__)


# =========================================================
#  LOW-LEVEL HELPERS
# =========================================================
def _get_session_times(s: dict) -> tuple[float, float] | None:
    ts_start = s.get("start_time", s.get("start"))
    ts_end = s.get("end_time", s.get("end"))
    if ts_start and ts_end:
        return float(ts_start), float(ts_end)
    return None


def _get_channel_owners(channel_id: str, ctv_data: dict) -> list[str]:
    """Trả về list user_id các CTV chính đang quản kênh này."""
    cid = str(channel_id)
    return [
        str(uid) for uid, chans in ctv_data.items()
        if isinstance(chans, list) and cid in chans
    ]


def get_last_session(channel_id) -> dict | None:
    """Session mới nhất của 1 kênh (bất kể user nào live)."""
    sessions = db.load("sessions")
    lst = sessions.get(str(channel_id), [])
    return lst[-1] if lst else None


def calculate_today_seconds(channel_id) -> int:
    """
    Tổng giây live hôm nay ở 1 kênh (aggregate theo CTV chủ, mọi kênh họ live).
    """
    sessions = db.load("sessions")
    ctv_data = db.load("ctv")
    owners = set(_get_channel_owners(channel_id, ctv_data))
    today = now_vn().date()

    total = 0
    for cid, s_list in sessions.items():
        if not isinstance(s_list, list):
            continue
        for s in s_list:
            if str(s.get("user_id", "")) not in owners:
                continue
            times = _get_session_times(s)
            if not times:
                continue
            if from_ts(times[0]).date() == today:
                total += int(s.get("duration", 0))
    return total


# =========================================================
#  REPORT — CHANNEL (User/CTV bấm Check)
# =========================================================
def create_channel_report(channel_id, days: int, title_name: str) -> discord.Embed:
    """
    Report cho User/CTV bấm 'Check Phiên Live'.

    Aggregate TẤT CẢ session của CTV chủ kênh này, bất kể họ live ở kênh nào:
    - 🏠 = session tại kênh này
    - 🌐 = session tại kênh khác của cùng CTV chủ
    """
    channel_id = str(channel_id)
    sessions = db.load("sessions")
    channels_data = db.load("channels")
    ctv_data = db.load("ctv")

    owners = _get_channel_owners(channel_id, ctv_data)
    multi_owner = len(owners) > 1

    now = now_vn()
    cutoff = start_of_day_vn(now) if days == 1 else now - datetime.timedelta(days=days)

    found = []
    total = 0
    own_total = 0   # session TẠI kênh này
    other_total = 0  # session tại kênh khác của cùng CTV chủ

    for cid, s_list in sessions.items():
        if not isinstance(s_list, list):
            continue
        c_name = channels_data.get(cid, {}).get("name", f"Kênh {cid}")
        is_this_channel = cid == channel_id

        for s in s_list:
            uid = str(s.get("user_id", ""))
            if uid not in owners:
                continue
            times = _get_session_times(s)
            if not times:
                continue
            start_dt = from_ts(times[0])
            if start_dt >= cutoff:
                dur = int(s.get("duration", 0))
                found.append({
                    "start": start_dt,
                    "end": from_ts(times[1]),
                    "duration": dur,
                    "user_id": uid,
                    "channel": c_name,
                    "is_this_channel": is_this_channel,
                })
                total += dur
                if is_this_channel:
                    own_total += dur
                else:
                    other_total += dur

    found.sort(key=lambda x: x["start"], reverse=True)
    channel_name = channels_data.get(channel_id, {}).get("name", "Kênh")

    owners_display = ", ".join(f"<@{uid}>" for uid in owners) if owners else "*(chưa có CTV)*"

    embed = discord.Embed(
        title=f"📊 Thống Kê {title_name}",
        description=(
            f"**Kênh:** {channel_name}\n"
            f"**CTV chính:** {owners_display}\n\n"
            f"**Tổng thời gian:** `{fmt_dur(total)}`\n"
            f"  🏠 Tại kênh này:  `{fmt_dur(own_total)}`\n"
            f"  🌐 Tại kênh khác: `{fmt_dur(other_total)}`\n"
            f"**Số phiên:** `{len(found)}`"
        ),
        color=discord.Color.blue() if days > 1 else discord.Color.green(),
    )

    if not found:
        embed.add_field(name="Chi tiết", value="💤 Chưa có hoạt động nào.", inline=False)
        embed.set_footer(text="🏠 = kênh này | 🌐 = kênh khác")
        return embed

    if days == 1:
        # Chi tiết từng phiên hôm nay
        lines = []
        for s in found:
            marker = "🏠" if s["is_this_channel"] else "🌐"
            time_str = f"`{s['start'].strftime('%H:%M')} - {s['end'].strftime('%H:%M')}`"
            dur_str = f"⏳ **{fmt_dur(s['duration'])}**"

            if multi_owner:
                if s["is_this_channel"]:
                    suffix = f"<@{s['user_id']}>"
                else:
                    suffix = f"<@{s['user_id']}> tại {s['channel']}"
            else:
                suffix = f"📺 {s['channel']}"

            lines.append(f"{marker} {time_str} | {dur_str} | {suffix}")

        text = "\n".join(lines)
        if len(text) > 1000:
            text = text[:950] + "\n... (Hiển thị rút gọn)"
        embed.add_field(name="Chi tiết các phiên hôm nay:", value=text, inline=False)
    else:
        # Tổng kết theo ngày
        daily: dict[str, int] = {}
        for s in found:
            ds = s["start"].strftime("%d/%m/%Y")
            daily[ds] = daily.get(ds, 0) + s["duration"]
        # Sort descending (mới nhất trên đầu)
        sorted_days = sorted(daily.items(), key=lambda x: x[0], reverse=True)
        lines = [f"📅 `{d}` : ⏳ **{fmt_dur(sec)}**" for d, sec in sorted_days]
        text = "\n".join(lines)
        if len(text) > 1000:
            text = text[:950] + "\n... (Hiển thị rút gọn)"
        embed.add_field(name="Tổng kết từng ngày:", value=text, inline=False)

    embed.set_footer(text="🏠 = kênh này | 🌐 = kênh khác")
    return embed


# =========================================================
#  REPORT — CTV (Admin bấm Check CTV)
# =========================================================
def create_ctv_report(user_id, days: int, target_day: datetime.date | None = None) -> discord.Embed:
    """
    Report cho Admin bấm 'Check CTV' → chọn CTV.

    Args:
        user_id: ID CTV
        days: 1 (Hôm nay) / 7 / 15 — khung xem
        target_day: nếu set → hiển thị CHI TIẾT phiên của ngày đó
    """
    user_id = str(user_id)
    ctv_data = db.load("ctv")
    sessions = db.load("sessions")
    channels_data = db.load("channels")

    if user_id not in ctv_data:
        return discord.Embed(
            title="❌ Không tìm thấy CTV",
            description=f"ID `{user_id}` không có trong danh sách CTV.",
            color=discord.Color.red(),
        )

    if target_day is not None:
        return _build_ctv_day_embed(user_id, target_day, sessions, ctv_data, channels_data)
    elif days == 1:
        return _build_ctv_day_embed(user_id, now_vn().date(), sessions, ctv_data, channels_data)
    else:
        return _build_ctv_summary_embed(user_id, days, sessions, ctv_data, channels_data)


def _build_ctv_day_embed(
    user_id: str,
    target_day: datetime.date,
    sessions: dict,
    ctv_data: dict,
    channels_data: dict,
) -> discord.Embed:
    """Chi tiết sessions của 1 ngày cụ thể."""
    found = []
    for cid, s_list in sessions.items():
        c_name = channels_data.get(cid, {}).get("name", f"Kênh {cid}")
        is_own = cid in ctv_data.get(user_id, [])
        if not isinstance(s_list, list):
            continue
        for s in s_list:
            if str(s.get("user_id", "")) != user_id:
                continue
            times = _get_session_times(s)
            if not times:
                continue
            start_dt = from_ts(times[0])
            if start_dt.date() == target_day:
                found.append({
                    "channel": c_name,
                    "is_own": is_own,
                    "start": start_dt,
                    "end": from_ts(times[1]),
                    "duration": int(s.get("duration", 0)),
                })

    found.sort(key=lambda x: x["start"], reverse=True)
    total = sum(s["duration"] for s in found)
    own_total = sum(s["duration"] for s in found if s["is_own"])
    other_total = total - own_total

    now = now_vn()
    today = now.date()
    if target_day == today:
        day_label = "Hôm nay"
    elif target_day == today - datetime.timedelta(days=1):
        day_label = "Hôm qua"
    else:
        day_label = target_day.strftime("%d/%m/%Y")

    embed = discord.Embed(
        title=f"📊 Chi tiết Live — {day_label}",
        description=(
            f"**CTV:** <@{user_id}>\n"
            f"**Ngày:** `{target_day.strftime('%d/%m/%Y')}`\n"
            f"**Tổng thời gian:** `{fmt_dur(total)}`\n"
            f"  • Kênh mình quản: `{fmt_dur(own_total)}`\n"
            f"  • Kênh khác:      `{fmt_dur(other_total)}`\n"
            f"**Số phiên:** `{len(found)}`"
        ),
        color=discord.Color.gold(),
    )

    if not found:
        embed.add_field(
            name="Chi tiết",
            value=f"📭 Không có phiên live nào trong ngày {target_day.strftime('%d/%m/%Y')}.",
            inline=False,
        )
        embed.set_footer(text=f"Check lúc: {now.strftime('%H:%M %d/%m')}")
        return embed

    lines: list[str] = []
    for s in found:
        marker = "" if s["is_own"] else " 🌐"
        lines.append(
            f"`{s['start'].strftime('%H:%M')} - {s['end'].strftime('%H:%M')}` "
            f"| ⏳ **{fmt_dur(s['duration'])}** | 📺 {s['channel']}{marker}"
        )

    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3400] + "\n... (Còn nữa)"

    embed.description += f"\n\n**Chi tiết các phiên:**\n{text}"
    embed.set_footer(text=f"Check lúc: {now.strftime('%H:%M %d/%m')}  |  🌐 = kênh khác")
    return embed


def _build_ctv_summary_embed(
    user_id: str,
    days: int,
    sessions: dict,
    ctv_data: dict,
    channels_data: dict,
) -> discord.Embed:
    """Summary N ngày qua — tóm tắt theo ngày."""
    now = now_vn()
    today = now.date()
    cutoff_date = today - datetime.timedelta(days=days - 1)

    daily: dict[datetime.date, dict] = {}
    for cid, s_list in sessions.items():
        is_own = cid in ctv_data.get(user_id, [])
        if not isinstance(s_list, list):
            continue
        for s in s_list:
            if str(s.get("user_id", "")) != user_id:
                continue
            times = _get_session_times(s)
            if not times:
                continue
            start_dt = from_ts(times[0])
            d = start_dt.date()
            if d < cutoff_date:
                continue
            dur = int(s.get("duration", 0))
            if d not in daily:
                daily[d] = {"total": 0, "own": 0, "other": 0, "count": 0}
            daily[d]["total"] += dur
            daily[d]["count"] += 1
            if is_own:
                daily[d]["own"] += dur
            else:
                daily[d]["other"] += dur

    total_all = sum(d["total"] for d in daily.values())
    own_all = sum(d["own"] for d in daily.values())
    other_all = total_all - own_all
    total_sessions = sum(d["count"] for d in daily.values())

    period_label = next(
        (label for label, d in CHECK_RANGE_OPTIONS if d == days),
        f"{days} ngày qua",
    )

    embed = discord.Embed(
        title=f"📊 Tổng quan — {period_label}",
        description=(
            f"**CTV:** <@{user_id}>\n"
            f"**Tổng thời gian:** `{fmt_dur(total_all)}`\n"
            f"  • Kênh mình quản: `{fmt_dur(own_all)}`\n"
            f"  • Kênh khác:      `{fmt_dur(other_all)}`\n"
            f"**Số phiên:** `{total_sessions}`"
        ),
        color=discord.Color.gold(),
    )

    if not daily:
        embed.add_field(
            name="Chi tiết theo ngày",
            value=f"📭 Không có phiên live nào trong {period_label.lower()}.",
            inline=False,
        )
        embed.set_footer(text="Bấm nút ngày bên dưới để xem chi tiết từng ngày")
        return embed

    sorted_days = sorted(daily.keys(), reverse=True)
    lines: list[str] = []
    for d in sorted_days:
        info = daily[d]
        if d == today:
            day_lbl = "Hôm nay"
        elif d == today - datetime.timedelta(days=1):
            day_lbl = "Hôm qua"
        else:
            day_lbl = d.strftime("%d/%m")
        lines.append(
            f"📅 **{day_lbl}** — ⏳ `{fmt_dur(info['total'])}` ({info['count']} phiên)"
        )

    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3400] + "\n... (Còn nữa)"

    embed.description += f"\n\n**Chi tiết theo ngày:**\n{text}"
    embed.set_footer(text="Bấm nút ngày bên dưới để xem chi tiết phiên của ngày đó")
    return embed


# =========================================================
#  LEADERBOARD — theo CTV, aggregate mọi kênh
# =========================================================
def build_leaderboard(target_date: datetime.date) -> list[tuple[str, dict]]:
    """
    BXH ngày `target_date` theo CTV.

    Return: list [(user_id, {total, own, other, sessions_own, sessions_other}), ...]
            sorted giảm dần theo total.
    """
    sessions = db.load("sessions")
    ctv_data = db.load("ctv")

    result: dict[str, dict] = {}

    for cid, s_list in sessions.items():
        if not isinstance(s_list, list):
            continue
        for s in s_list:
            uid = str(s.get("user_id", ""))
            if not uid or uid not in ctv_data:
                continue  # Chỉ tính CTV đăng ký
            times = _get_session_times(s)
            if not times:
                continue
            if from_ts(times[0]).date() != target_date:
                continue

            dur = int(s.get("duration", 0))
            is_own = cid in ctv_data.get(uid, [])

            if uid not in result:
                result[uid] = {
                    "total": 0,
                    "own": 0,
                    "other": 0,
                    "sessions_own": 0,
                    "sessions_other": 0,
                }
            result[uid]["total"] += dur
            if is_own:
                result[uid]["own"] += dur
                result[uid]["sessions_own"] += 1
            else:
                result[uid]["other"] += dur
                result[uid]["sessions_other"] += 1

    return sorted(result.items(), key=lambda x: x[1]["total"], reverse=True)


def build_daily_leaderboard_embed(target_date: datetime.date) -> discord.Embed:
    """Build embed BXH ngày — Top 3 có breakdown, top 4-10 gọn."""
    ranking = build_leaderboard(target_date)

    embed = discord.Embed(
        title=f"🏆 BẢNG XẾP HẠNG CTV — {target_date.strftime('%d/%m/%Y')}",
        color=discord.Color.gold(),
    )

    if not ranking:
        embed.description = "💤 Không có CTV nào Live ngày này."
        return embed

    medals = ["🥇", "🥈", "🥉"]
    top_sections: list[str] = []
    rest_lines: list[str] = []

    for idx, (uid, stats) in enumerate(ranking[:10]):
        if idx < 3:
            section = f"{medals[idx]} <@{uid}> — `{fmt_dur(stats['total'])}`"
            if stats["own"] > 0:
                section += (
                    f"\n   • Kênh mình: `{fmt_dur(stats['own'])}` "
                    f"({stats['sessions_own']} phiên)"
                )
            if stats["other"] > 0:
                section += (
                    f"\n   • Kênh khác: `{fmt_dur(stats['other'])}` "
                    f"({stats['sessions_other']} phiên)"
                )
            top_sections.append(section)
        else:
            rest_lines.append(f"**{idx + 1}.** <@{uid}> — `{fmt_dur(stats['total'])}`")

    parts = ["\n\n".join(top_sections)]
    if rest_lines:
        parts.append("\n".join(rest_lines))

    embed.description = "\n\n".join(parts)

    # Vinh danh Top 1
    top_uid = ranking[0][0]
    embed.add_field(
        name="👑 Vinh danh Top 1",
        value=f"<@{top_uid}>",
        inline=False,
    )

    return embed
