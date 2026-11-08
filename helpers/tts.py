"""
Text-to-Speech helper — dùng edge-tts (Microsoft Edge TTS, miễn phí, không cần API key).

Voice tiếng Việt có sẵn:
- vi-VN-HoaiMyNeural   (nữ, tự nhiên)
- vi-VN-NamMinhNeural  (nam)
"""
import logging
import os
import tempfile

try:
    import edge_tts
    _EDGE_TTS_OK = True
except ImportError:
    _EDGE_TTS_OK = False

log = logging.getLogger(__name__)

# Voice mặc định
DEFAULT_VOICE_FEMALE = "vi-VN-HoaiMyNeural"
DEFAULT_VOICE_MALE = "vi-VN-NamMinhNeural"
DEFAULT_VOICE = DEFAULT_VOICE_FEMALE


async def generate_tts_file(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """
    Generate TTS audio → save to temp mp3 file → return path.

    IMPORTANT: Nhớ os.remove(path) sau khi dùng để dọn file tạm.

    Args:
        text: Nội dung cần đọc (tối đa vài trăm ký tự)
        voice: mã voice edge-tts (default: vi-VN-HoaiMyNeural)

    Return: Đường dẫn file mp3 tạm, hoặc None nếu lỗi.
    """
    if not _EDGE_TTS_OK:
        log.error("edge-tts chưa cài. Chạy: pip install edge-tts")
        return None

    if not text or not text.strip():
        log.warning("Text rỗng, skip TTS")
        return None

    # Create temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    path = tmp.name

    try:
        communicate = edge_tts.Communicate(text.strip(), voice)
        await communicate.save(path)
        return path
    except Exception:
        log.exception("Generate TTS lỗi")
        # Cleanup file rỗng nếu có
        try:
            os.remove(path)
        except Exception:
            pass
        return None


def cleanup_tts_file(path: str) -> None:
    """Xóa file tạm sau khi phát xong."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        log.exception(f"Xóa file TTS {path} lỗi")
