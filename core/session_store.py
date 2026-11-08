"""
Lưu lịch sử phiên (LIVE + AUDIO) vào SQLite — giữ vĩnh viễn.

Mỗi dòng là 1 đoạn thời gian của 1 CTV trong 1 kênh, thuộc đúng 1 ngày (giờ VN):
phiên vắt qua 00:00 được cắt thành nhiều dòng ngay lúc lưu, nên không phụ thuộc
vào việc bot có chạy đúng lúc nửa đêm hay không.

Bảng `meta` lưu vài giá trị nhỏ của hệ thống (nhịp tim, ngày đã gửi BXH, cờ migrate...).
"""
import datetime
import logging
import sqlite3
import threading
from pathlib import Path

from config import MIN_SESSION_SECONDS, SESSIONS_DB_FILE
from helpers.time_utils import split_by_day

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    channel_id  TEXT    NOT NULL,
    kind        TEXT    NOT NULL,              -- 'stream' | 'audio'
    start_ts    REAL    NOT NULL,
    end_ts      REAL    NOT NULL,
    duration    INTEGER NOT NULL,              -- giây
    day         TEXT    NOT NULL,              -- YYYY-MM-DD (giờ VN)
    flags       TEXT    NOT NULL DEFAULT ''    -- 'offline' = giờ kết thúc ước tính do bot offline
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_day ON sessions(user_id, day);
CREATE INDEX IF NOT EXISTS idx_sessions_day      ON sessions(day);
CREATE INDEX IF NOT EXISTS idx_sessions_channel  ON sessions(channel_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SessionStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)

    # =====================================================
    #  GHI
    # =====================================================
    def add_session(
        self,
        user_id,
        channel_id,
        kind: str,
        start_ts: float,
        end_ts: float,
        flags: str = "",
        min_seconds: float = MIN_SESSION_SECONDS,
    ) -> int:
        """
        Lưu 1 phiên, tự cắt theo ngày. Trả về số giây đã lưu (0 nếu bỏ qua).

        Phiên ngắn hơn `min_seconds` bị bỏ qua.
        """
        total = end_ts - start_ts
        if total <= 0 or total < min_seconds:
            return 0

        rows = []
        for s, e, day in split_by_day(start_ts, end_ts):
            dur = int(e - s)
            if dur < 1:
                continue
            rows.append((str(user_id), str(channel_id), kind, s, e, dur, day.isoformat(), flags or ""))

        if not rows:
            return 0

        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT INTO sessions (user_id, channel_id, kind, start_ts, end_ts, duration, day, flags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return sum(r[5] for r in rows)

    def delete_user(self, user_id) -> int:
        """Xóa toàn bộ lịch sử của 1 người. Trả về số dòng đã xóa."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (str(user_id),))
            return cur.rowcount

    # =====================================================
    #  ĐỌC
    # =====================================================
    def query(
        self,
        start_day: datetime.date,
        end_day: datetime.date,
        user_ids: set[str] | list[str] | None = None,
        kind: str | None = None,
    ) -> list[dict]:
        """Các dòng có `day` trong [start_day, end_day] (tính cả 2 đầu)."""
        sql = "SELECT * FROM sessions WHERE day >= ? AND day <= ?"
        params: list = [start_day.isoformat(), end_day.isoformat()]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if user_ids is not None:
            ids = [str(u) for u in user_ids]
            if not ids:
                return []
            sql += f" AND user_id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
        sql += " ORDER BY start_ts"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def last_session(self, user_ids, kind: str | None = None) -> dict | None:
        ids = [str(u) for u in user_ids]
        if not ids:
            return None
        sql = f"SELECT * FROM sessions WHERE user_id IN ({','.join('?' * len(ids))})"
        params: list = list(ids)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY end_ts DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def has_history(self, user_id) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE user_id = ? LIMIT 1", (str(user_id),)
            ).fetchone()
        return row is not None

    def user_ids(self) -> list[str]:
        """Mọi người từng có lịch sử."""
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT DISTINCT user_id FROM sessions").fetchall()]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    # =====================================================
    #  META
    # =====================================================
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Singleton dùng chung
store = SessionStore(SESSIONS_DB_FILE)
