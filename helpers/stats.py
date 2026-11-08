"""
Tính toán thống kê từ lịch sử phiên (SQLite) + các đoạn đang chạy (state).

- LIVE  = thời gian bật Go Live (chia sẻ màn hình) — kind "stream".
- AUDIO = thời gian ở trong voice nhưng không chia sẻ màn hình — kind "audio".

Mọi báo cáo đều cộng cả phiên ĐANG CHẠY (cắt đến thời điểm xem), nên admin bấm
Check lúc CTV đang live vẫn thấy đủ giờ.

- create_channel_report (khách/CTV bấm Check ở 1 kênh): chỉ LIVE của các CTV chủ kênh,
  ở mọi kênh (🏠 = tại kênh này, 🌐 = kênh khác).
- create_ctv_report (Check CTV): LIVE + AUDIO của 1 người.
- build_leaderboard: BXH theo LIVE.
"""
import datetime
import logging
from typing import Callable

import discord

from config import CHECK_RANGE_OPTIONS, KIND_AUDIO, KIND_STREAM
from core.data_manager import db
from core.session_store import store
from core.state import state
from core.tracker import FLAG_OFFLINE
from helpers.time_utils import fmt_dur, from_ts, now_ts, now_vn, split_by_day

log = logging.getLogger(__name__)


# =========================================================
#  TÊN KÊNH
# =========================================================
_channel_name_resolver: Callable[[str], str | None] | None = None


def set_channel_name_resolver(fn: Callable[[str], str | None]) -> None:
    """main.py gắn hàm lấy tên kênh từ Discord (cho kênh chưa lưu trong hệ thống)."""
    global _channel_name_resolver
    _channel_name_resolver = fn


def channel_name(channel_id, channels_data: dict | None = None) -> str:
    cid = str(channel_id)
    if channels_data is None:
        channels_data = db.load("channels")
    name = channels_data.get(cid, {}).get("name")
    if name:
        return name
    if _channel_name_resolver is not None:
        try:
            name = _channel_name_resolver(cid)
        except Exception:
            name = None
        if name:
            return name
    return f"Kênh {cid}"


# =========================================================
#  LOW-LEVEL
# =========================================================
def get_channel_owners(channel_id, ctv_data: dict | None = None) -> list[str]:
    """user_id các CTV chính đang quản kênh này."""
    if ctv_data is None:
        ctv_data = db.load("ctv")
    cid = str(channel_id)
    return [
        str(uid) for uid, chans in ctv_data.items()
        if isinstance(chans, list) and cid in chans
    ]


def collect_segments(
    start_day: datetime.date,
    end_day: datetime.date,
    user_ids=None,
    kind: str | None = None,
    now: float | None = None,
) -> list[dict]:
    """
    Tất cả đoạn (đã lưu + đang chạy) thuộc các ngày [start_day, end_day].

    Mỗi phần tử: {user_id, channel_id, kind, start_ts, end_ts, duration, day, flags, running}
    """
    ids = None if user_ids is None else {str(u) for u in user_ids}
    segs: list[dict] = []
    for row in store.query(start_day, end_day, ids, kind):
        row["running"] = False
        segs.append(row)

    now = now if now is not None else now_ts()
    for uid, info in state.snapshot().items():
        if ids is not None and uid not in ids:
            continue
        if kind and info["kind"] != kind:
            continue
        for s, e, day in split_by_day(info["start_ts"], now):
            if not (start_day <= day <= end_day):
                continue
            segs.append({
                "user_id": uid,
                "channel_id": info["channel_id"],
                "kind": info["kind"],
                "start_ts": s,
                "end_ts": e,
                "duration": int(e - s),
                "day": day.isoformat(),
                "flags": "",
                "running": e >= now,
            })

    segs.sort(key=lambda x: x["start_ts"])
    return segs


def get_last_stream_session(user_ids) -> dict | None:
    """Phiên LIVE gần nhất (đã kết thúc) của 1 nhóm CTV."""
    return store.last_session(user_ids, KIND_STREAM)


def _range_days(days: int) -> tuple[datetime.date, datetime.date]:
    """`days` ngày theo lịch, tính cả hôm nay."""
    today = now_vn().date()
    return today - datetime.timedelta(days=max(1, days) - 1), today


def _day_label(d: datetime.date, today: datetime.date) -> str:
    if d == today:
        return "Hôm nay"
    if d == today - datetime.timedelta(days=1):
        return "Hôm qua"
    return d.strftime("%d/%m")


def _seg_time_range(seg: dict) -> str:
    start = from_ts(seg["start_ts"]).strftime("%H:%M")
    if seg.get("running"):
        return f"{start} - nay"
    end_dt = from_ts(seg["end_ts"])
    # Đoạn kết thúc đúng 24:00 hiển thị 24:00 cho dễ hiểu
    end = "24:00" if (end_dt.hour, end_dt.minute, end_dt.second) == (0, 0, 0) and seg["end_ts"] > seg["start_ts"] else end_dt.strftime("%H:%M")
    return f"{start} - {end}"


def _join_limited(lines: list[str], limit: int) -> str:
    out: list[str] = []
    size = 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit:
            out.append(f"... (còn {len(lines) - i} dòng)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


# =========================================================
#  REPORT — KÊNH (khách / CTV bấm Check)
# =========================================================
def create_channel_report(channel_id, days: int, title_name: str) -> discord.Embed:
    """
    Giờ LIVE của các CTV chủ kênh này, ở mọi kênh:
    🏠 = tại kênh này, 🌐 = kênh khác.
    """
    channel_id = str(channel_id)
    channels_data = db.load("channels")
    ctv_data = db.load("ctv")
    owners = get_channel_owners(channel_id, ctv_data)
    multi_owner = len(owners) > 1

    start_day, end_day = _range_days(days)
    segs = collect_segments(start_day, end_day, owners, KIND_STREAM) if owners else []

    total = sum(s["duration"] for s in segs)
    own_total = sum(s["duration"] for s in segs if s["channel_id"] == channel_id)
    other_total = total - own_total
    finished = [s for s in segs if not s["running"]]
    running = [s for s in segs if s["running"]]

    owners_display = ", ".join(f"<@{uid}>" for uid in owners) if owners else "*(chưa có CTV)*"
    embed = discord.Embed(
        title=f"📊 Thống Kê {title_name}",
        description=(
            f"**Kênh:** {channel_name(channel_id, channels_data)}\n"
            f"**CTV chính:** {owners_display}\n\n"
            f"**Tổng thời gian LIVE:** `{fmt_dur(total)}`\n"
            f"  🏠 Tại kênh này:  `{fmt_dur(own_total)}`\n"
            f"  🌐 Tại kênh khác: `{fmt_dur(other_total)}`\n"
            f"**Số phiên:** `{len(finished)}`"
            + (f" (+{len(running)} đang live)" if running else "")
        ),
        color=discord.Color.blue() if days > 1 else discord.Color.green(),
    )

    if not segs:
        embed.add_field(name="Chi tiết", value="💤 Chưa có hoạt động nào.", inline=False)
        embed.set_footer(text="🏠 = kênh này | 🌐 = kênh khác")
        return embed

    if days == 1:
        lines = []
        for s in sorted(segs, key=lambda x: x["start_ts"], reverse=True):
            marker = "🏠" if s["channel_id"] == channel_id else "🌐"
            live_mark = " 🔴" if s["running"] else ""
            warn = " ⚠️" if s.get("flags") == FLAG_OFFLINE else ""
            if multi_owner:
                where = "" if s["channel_id"] == channel_id else f" tại {channel_name(s['channel_id'], channels_data)}"
                suffix = f"<@{s['user_id']}>{where}"
            else:
                suffix = f"📺 {channel_name(s['channel_id'], channels_data)}"
            lines.append(
                f"{marker} `{_seg_time_range(s)}` | ⏳ **{fmt_dur(s['duration'])}**{live_mark}{warn} | {suffix}"
            )
        embed.add_field(
            name="Chi tiết các phiên hôm nay:",
            value=_join_limited(lines, 1000),
            inline=False,
        )
    else:
        daily: dict[str, int] = {}
        for s in segs:
            daily[s["day"]] = daily.get(s["day"], 0) + s["duration"]
        today = now_vn().date()
        lines = [
            f"📅 `{datetime.date.fromisoformat(d).strftime('%d/%m/%Y')}` "
            f"({_day_label(datetime.date.fromisoformat(d), today)}) : ⏳ **{fmt_dur(sec)}**"
            for d, sec in sorted(daily.items(), reverse=True)  # ISO date → sắp xếp đúng thứ tự
        ]
        embed.add_field(name="Tổng kết từng ngày:", value=_join_limited(lines, 1000), inline=False)

    embed.set_footer(text="🏠 = kênh này | 🌐 = kênh khác | 🔴 = đang live | ⚠️ = giờ kết thúc ước tính (bot mất kết nối)")
    return embed


# =========================================================
#  REPORT — CTV (Check CTV)
# =========================================================
def create_ctv_report(user_id, days: int, target_day: datetime.date | None = None) -> discord.Embed:
    """
    Báo cáo LIVE + AUDIO của 1 người.

    days: 1 (Hôm nay) / 7 / 15 — khung xem
    target_day: nếu set → chi tiết các phiên của đúng ngày đó
    """
    user_id = str(user_id)
    ctv_data = db.load("ctv")

    if user_id not in ctv_data and not store.has_history(user_id) and state.get(user_id) is None:
        return discord.Embed(
            title="❌ Không tìm thấy CTV",
            description=f"<@{user_id}> không phải CTV và chưa có lịch sử live.",
            color=discord.Color.red(),
        )

    if target_day is not None:
        return _build_ctv_day_embed(user_id, target_day, ctv_data)
    if days == 1:
        return _build_ctv_day_embed(user_id, now_vn().date(), ctv_data)
    return _build_ctv_summary_embed(user_id, days, ctv_data)


def _build_ctv_day_embed(user_id: str, target_day: datetime.date, ctv_data: dict) -> discord.Embed:
    channels_data = db.load("channels")
    own_channels = set(ctv_data.get(user_id, []) or [])
    segs = collect_segments(target_day, target_day, [user_id])

    streams = [s for s in segs if s["kind"] == KIND_STREAM]
    audios = [s for s in segs if s["kind"] == KIND_AUDIO]
    live_total = sum(s["duration"] for s in streams)
    live_own = sum(s["duration"] for s in streams if s["channel_id"] in own_channels)
    audio_total = sum(s["duration"] for s in audios)

    now = now_vn()
    today = now.date()
    day_label = {
        today: "Hôm nay",
        today - datetime.timedelta(days=1): "Hôm qua",
    }.get(target_day, target_day.strftime("%d/%m/%Y"))

    is_active = user_id in ctv_data
    status_note = "" if is_active else "\n*(Đã nghỉ — không còn trong danh sách CTV)*"
    live_count = len([s for s in streams if not s["running"]])
    running_note = " +🔴 đang live" if any(s["running"] for s in streams) else ""

    description = (
        f"**CTV:** <@{user_id}>{status_note}\n"
        f"**Ngày:** `{target_day.strftime('%d/%m/%Y')}`\n\n"
        f"📺 **LIVE (chia sẻ màn hình):** `{fmt_dur(live_total)}` ({live_count} phiên{running_note})\n"
        f"  • Kênh mình quản: `{fmt_dur(live_own)}`\n"
        f"  • Kênh khác:      `{fmt_dur(live_total - live_own)}`\n"
        f"🎧 **Chỉ Audio (không live):** `{fmt_dur(audio_total)}`"
    )

    embed = discord.Embed(
        title=f"📊 Chi tiết — {day_label}",
        description=description,
        color=discord.Color.gold(),
    )

    if not segs:
        embed.description += (
            f"\n\n📭 Không có hoạt động nào trong ngày {target_day.strftime('%d/%m/%Y')}."
        )
        embed.set_footer(text=f"Check lúc: {now.strftime('%H:%M %d/%m')}")
        return embed

    lines: list[str] = []
    for s in sorted(segs, key=lambda x: x["start_ts"], reverse=True):
        icon = "📺" if s["kind"] == KIND_STREAM else "🎧"
        other = "" if s["channel_id"] in own_channels else " 🌐"
        live_mark = " 🔴" if s["running"] else ""
        warn = " ⚠️" if s.get("flags") == FLAG_OFFLINE else ""
        lines.append(
            f"{icon} `{_seg_time_range(s)}` | ⏳ **{fmt_dur(s['duration'])}**{live_mark}{warn} "
            f"| {channel_name(s['channel_id'], channels_data)}{other}"
        )

    budget = 4000 - len(embed.description) - 40
    embed.description += "\n\n**Chi tiết (mới nhất trên đầu):**\n" + _join_limited(lines, budget)
    embed.set_footer(
        text=(
            f"Check lúc: {now.strftime('%H:%M %d/%m')} | 📺 live · 🎧 audio · 🌐 kênh khác · "
            "🔴 đang chạy · ⚠️ giờ kết thúc ước tính"
        )
    )
    return embed


def _build_ctv_summary_embed(user_id: str, days: int, ctv_data: dict) -> discord.Embed:
    start_day, end_day = _range_days(days)
    own_channels = set(ctv_data.get(user_id, []) or [])
    segs = collect_segments(start_day, end_day, [user_id])

    daily: dict[str, dict] = {}
    for s in segs:
        d = daily.setdefault(s["day"], {"live": 0, "own": 0, "audio": 0, "count": 0})
        if s["kind"] == KIND_STREAM:
            d["live"] += s["duration"]
            if s["channel_id"] in own_channels:
                d["own"] += s["duration"]
            if not s["running"]:
                d["count"] += 1
        else:
            d["audio"] += s["duration"]

    live_all = sum(d["live"] for d in daily.values())
    own_all = sum(d["own"] for d in daily.values())
    audio_all = sum(d["audio"] for d in daily.values())
    count_all = sum(d["count"] for d in daily.values())

    period_label = next(
        (label for label, d in CHECK_RANGE_OPTIONS if d == days),
        f"{days} ngày qua",
    )
    status_note = "" if user_id in ctv_data else "\n*(Đã nghỉ — không còn trong danh sách CTV)*"

    embed = discord.Embed(
        title=f"📊 Tổng quan — {period_label}",
        description=(
            f"**CTV:** <@{user_id}>{status_note}\n\n"
            f"📺 **LIVE:** `{fmt_dur(live_all)}` ({count_all} phiên)\n"
            f"  • Kênh mình quản: `{fmt_dur(own_all)}`\n"
            f"  • Kênh khác:      `{fmt_dur(live_all - own_all)}`\n"
            f"🎧 **Chỉ Audio:** `{fmt_dur(audio_all)}`"
        ),
        color=discord.Color.gold(),
    )

    if not daily:
        embed.description += f"\n\n📭 Không có hoạt động nào trong {period_label.lower()}."
        embed.set_footer(text="Bấm nút ngày bên dưới để xem chi tiết từng ngày")
        return embed

    today = now_vn().date()
    lines = []
    for day_iso in sorted(daily.keys(), reverse=True):
        info = daily[day_iso]
        lbl = _day_label(datetime.date.fromisoformat(day_iso), today)
        lines.append(
            f"📅 **{lbl}** — 📺 `{fmt_dur(info['live'])}` ({info['count']} phiên) · "
            f"🎧 `{fmt_dur(info['audio'])}`"
        )

    budget = 4000 - len(embed.description) - 40
    embed.description += "\n\n**Chi tiết theo ngày:**\n" + _join_limited(lines, budget)
    embed.set_footer(text="Bấm nút ngày bên dưới để xem chi tiết phiên của ngày đó")
    return embed


# =========================================================
#  LEADERBOARD — theo LIVE
# =========================================================
def build_leaderboard(target_date: datetime.date) -> list[tuple[str, dict]]:
    """
    BXH LIVE ngày `target_date`.

    Return: [(user_id, {total, own, other, sessions_own, sessions_other}), ...] giảm dần.
    """
    ctv_data = db.load("ctv")
    result: dict[str, dict] = {}
    for s in collect_segments(target_date, target_date, None, KIND_STREAM):
        uid = str(s["user_id"])
        r = result.setdefault(uid, {
            "total": 0, "own": 0, "other": 0, "sessions_own": 0, "sessions_other": 0,
        })
        dur = s["duration"]
        r["total"] += dur
        if s["channel_id"] in (ctv_data.get(uid) or []):
            r["own"] += dur
            r["sessions_own"] += 1
        else:
            r["other"] += dur
            r["sessions_other"] += 1

    return sorted(result.items(), key=lambda x: x[1]["total"], reverse=True)


def build_daily_leaderboard_embed(target_date: datetime.date) -> discord.Embed:
    """Embed BXH ngày — Top 3 có breakdown, top 4-10 gọn."""
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

    embed.add_field(name="👑 Vinh danh Top 1", value=f"<@{ranking[0][0]}>", inline=False)
    embed.set_footer(text="Chỉ tính thời gian chia sẻ màn hình (Go Live)")
    return embed
