"""
Voice manager — connect voice channel, play audio, disconnect.

Cần FFmpeg cài system-level (không phải pip).
Windows: https://ffmpeg.org/download.html → add binary vào PATH
Linux:   sudo apt install ffmpeg
"""
import asyncio
import logging

import discord

log = logging.getLogger(__name__)


# Hàng đợi phát loa: mỗi server chỉ phát 1 audio tại 1 thời điểm.
# Voice Alert và "Gửi Voice" dùng chung → không cắt ngang nhau.
_guild_locks: dict[int, asyncio.Lock] = {}


def _guild_lock(guild_id: int) -> asyncio.Lock:
    lock = _guild_locks.get(guild_id)
    if lock is None:
        lock = _guild_locks[guild_id] = asyncio.Lock()
    return lock


async def play_audio_in_channel(
    channel: discord.VoiceChannel,
    audio_file_path: str,
    max_wait_seconds: float = 30.0,
    volume: float = 1.0,
) -> bool:
    """
    Kết nối voice channel, phát audio, tự động ngắt kết nối.

    Các lượt phát trong cùng server được xếp hàng lần lượt.

    Args:
        channel: kênh voice cần phát
        audio_file_path: đường dẫn file mp3
        max_wait_seconds: tối đa chờ bao lâu (tránh treo)
        volume: âm lượng 0.0-2.0 (1.0 = 100% mặc định)

    Return: True nếu phát thành công.
    """
    async with _guild_lock(channel.guild.id):
        return await _play_unlocked(channel, audio_file_path, max_wait_seconds, volume)


async def _play_unlocked(
    channel: discord.VoiceChannel,
    audio_file_path: str,
    max_wait_seconds: float,
    volume: float,
) -> bool:
    voice_client: discord.VoiceClient | None = None

    try:
        # Nếu bot đã đang connect ở guild này rồi → dùng lại (tránh conflict)
        existing = channel.guild.voice_client
        if existing and existing.is_connected():
            if existing.channel.id != channel.id:
                # Đang ở kênh khác → move sang kênh mới
                await existing.move_to(channel)
            voice_client = existing
        else:
            # Nếu có voice_client orphan (không is_connected nhưng chưa cleanup) → force disconnect
            if existing:
                try:
                    await existing.disconnect(force=True)
                    await asyncio.sleep(1)  # Đợi Discord xử lý disconnect
                except Exception:
                    pass
            # Connect mới (timeout 15s thay vì 10s)
            voice_client = await channel.connect(timeout=15, self_deaf=True)

        # Play audio với volume control
        raw_source = discord.FFmpegPCMAudio(audio_file_path)
        source = discord.PCMVolumeTransformer(raw_source, volume=max(0.0, min(2.0, volume)))
        if voice_client.is_playing():
            voice_client.stop()
        voice_client.play(source)

        # Chờ phát xong
        elapsed = 0.0
        while voice_client.is_playing() and elapsed < max_wait_seconds:
            await asyncio.sleep(0.5)
            elapsed += 0.5

        if voice_client.is_playing():
            voice_client.stop()
            log.warning(f"Audio phát quá {max_wait_seconds}s, stop cưỡng bức")

        return True

    except asyncio.TimeoutError:
        log.warning(f"Connect voice channel {channel.name} timeout")
        return False
    except discord.ClientException as e:
        log.warning(f"Voice client error: {e}")
        return False
    except FileNotFoundError:
        log.error("FFmpeg không tìm thấy! Cài FFmpeg system-level trước.")
        return False
    except Exception:
        log.exception("Play audio lỗi")
        return False
    finally:
        # Luôn disconnect sau khi phát + đợi 1s để Discord xử lý sạch
        # (tránh conflict khi lần play tiếp theo cần reconnect)
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect(force=False)
                await asyncio.sleep(1)
            except Exception:
                log.exception("Disconnect voice client lỗi")


async def kick_from_voice(member: discord.Member) -> bool:
    """
    Đá 1 member khỏi voice channel (move_to None).

    Return: True nếu thành công, False nếu không đủ quyền hoặc user không trong voice.
    """
    if not member.voice or not member.voice.channel:
        return False

    try:
        await member.move_to(None, reason="Ngắt kết nối bởi Voice Alert (quá hạn không live)")
        return True
    except discord.Forbidden:
        log.error(f"Không đủ quyền disconnect {member} (thiếu quyền Move Members)")
        return False
    except Exception:
        log.exception(f"Disconnect {member} lỗi")
        return False
