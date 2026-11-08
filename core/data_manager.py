"""
DataManager — wrapper cho các file JSON.

Cải tiến so với bản cũ:
1. Mỗi file có 1 asyncio.Lock riêng → 2 coroutine cùng ghi 1 file không bị race.
2. Atomic write: ghi vào file .tmp rồi rename → không bị corrupt nếu crash giữa chừng.
3. Load fail thì return dict rỗng thay vì crash bot.
4. Có API async (aload/asave/aupdate) khuyến nghị dùng trong callbacks/tasks.
5. API sync (load/save) vẫn giữ cho startup init.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

from config import DATA_FILES

log = logging.getLogger(__name__)


class DataManager:
    def __init__(self):
        self.files: dict[str, Path] = dict(DATA_FILES)
        self._locks: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in self.files}
        self._initialize_files()

    def _initialize_files(self) -> None:
        """Tạo file rỗng cho các key chưa có file."""
        for key, path in self.files.items():
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
                log.info(f"Tạo file data rỗng: {path.name}")

    # ---------- SYNC API ----------
    def load(self, key: str) -> dict:
        """Đọc file. An toàn với file trống / lỗi format."""
        try:
            with open(self.files[key], "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    log.warning(f"File '{key}' không phải dict, trả rỗng.")
                    return {}
                return data
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as e:
            log.error(f"File '{key}' JSON lỗi: {e}. Trả về rỗng.")
            return {}

    def save(self, key: str, data: dict) -> None:
        """Ghi file dưới dạng atomic (tạm rồi rename)."""
        target = self.files[key]
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # os.replace là atomic trên hầu hết OS
        tmp.replace(target)

    # ---------- ASYNC API ----------
    async def aload(self, key: str) -> dict:
        async with self._locks[key]:
            return self.load(key)

    async def asave(self, key: str, data: dict) -> None:
        async with self._locks[key]:
            self.save(key, data)

    async def aupdate(self, key: str, fn: Callable[[dict], dict | None]) -> None:
        """
        Load → gọi fn(data) → save, tất cả trong 1 lock.

        `fn` có thể mutate data in-place (không return) hoặc trả dict mới.
        Đây là cách AN TOÀN NHẤT để cập nhật 1 phần của file.

        Ví dụ:
            await db.aupdate("ctv_subscribers", lambda subs: subs.setdefault(ctv_id, []).append(uid))
        """
        async with self._locks[key]:
            data = self.load(key)
            result = fn(data)
            self.save(key, result if result is not None else data)


# Singleton — import chỗ nào cũng dùng chung 1 instance
db = DataManager()
