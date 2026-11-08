"""Kiểm thử báo cáo: cộng phiên đang chạy, tách Live/Audio, BXH, migrate JSON."""
import datetime
import json
import unittest
from unittest import mock

import tests  # noqa: F401

from config import KIND_AUDIO, KIND_STREAM, LEGACY_SESSIONS_JSON
from core.data_manager import db
from core.session_store import SessionStore
from core.state import LiveState
from helpers import migration, stats
from helpers.time_utils import now_ts, now_vn


class StatsTests(unittest.TestCase):
    def setUp(self):
        db.save("live_state", {})
        db.save("ctv", {"A": ["K1"], "B": ["K2"]})
        db.save("channels", {"K1": {"name": "Kênh A", "role_id": 0}, "K2": {"name": "Kênh B", "role_id": 0}})
        self.store = SessionStore(":memory:")
        self.state = LiveState()
        self.patches = [
            mock.patch.object(stats, "store", self.store),
            mock.patch.object(stats, "state", self.state),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.store.close()

    def test_running_session_included(self):
        now = now_ts()
        start = max(now - 3600, datetime.datetime.combine(
            now_vn().date(), datetime.time(0, 0), tzinfo=now_vn().tzinfo).timestamp())
        self.state.set("A", KIND_STREAM, "K1", start)
        today = now_vn().date()
        segs = stats.collect_segments(today, today, ["A"], KIND_STREAM, now=now)
        self.assertEqual(len(segs), 1)
        self.assertTrue(segs[0]["running"])
        self.assertEqual(segs[0]["duration"], int(now - start))

    def test_ctv_report_live_and_audio(self):
        today = now_vn().date()
        base = datetime.datetime.combine(today, datetime.time(0, 0), tzinfo=now_vn().tzinfo).timestamp()
        self.store.add_session("A", "K1", KIND_STREAM, base + 60, base + 60 + 3600)
        self.store.add_session("A", "K2", KIND_STREAM, base + 7200, base + 7200 + 1800)
        self.store.add_session("A", "K1", KIND_AUDIO, base + 10000, base + 10000 + 600)
        embed = stats.create_ctv_report("A", 1)
        self.assertIn("1:30:00", embed.description)   # LIVE tổng
        self.assertIn("1:00:00", embed.description)   # kênh mình
        self.assertIn("0:30:00", embed.description)   # kênh khác
        self.assertIn("0:10:00", embed.description)   # audio
        self.assertIn("Kênh B 🌐", embed.description)

        summary = stats.create_ctv_report("A", 7)
        self.assertIn("1:30:00", summary.description)

    def test_unknown_user(self):
        embed = stats.create_ctv_report("ZZZ", 1)
        self.assertIn("Không tìm thấy", embed.title)

    def test_leaderboard_counts_only_stream(self):
        day = datetime.date(2026, 11, 8)
        base = datetime.datetime.combine(day, datetime.time(20, 0), tzinfo=now_vn().tzinfo).timestamp()
        self.store.add_session("A", "K1", KIND_STREAM, base, base + 3600)
        self.store.add_session("B", "K1", KIND_STREAM, base, base + 7200)   # B live ở kênh của A
        self.store.add_session("A", "K1", KIND_AUDIO, base, base + 99999)
        ranking = stats.build_leaderboard(day)
        self.assertEqual([(u, r["total"], r["other"]) for u, r in ranking],
                         [("B", 7200, 7200), ("A", 3600, 0)])
        self.assertIn("BẢNG XẾP HẠNG", stats.build_daily_leaderboard_embed(day).title)

    def test_channel_report_sorted_by_real_date(self):
        """Lỗi cũ: sắp xếp chuỗi dd/mm/yyyy sai khi qua tháng."""
        with mock.patch.object(stats, "now_vn", return_value=datetime.datetime(
                2026, 11, 2, 12, 0, tzinfo=now_vn().tzinfo)):
            for d in (datetime.date(2026, 10, 31), datetime.date(2026, 11, 1)):
                base = datetime.datetime.combine(d, datetime.time(20), tzinfo=now_vn().tzinfo).timestamp()
                self.store.add_session("A", "K1", KIND_STREAM, base, base + 600)
            embed = stats.create_channel_report("K1", 7, "7 ngày")
        text = embed.fields[0].value
        self.assertLess(text.index("01/11/2026"), text.index("31/10/2026"))


class MigrationTests(unittest.TestCase):
    def test_json_sessions_imported_once(self):
        store = SessionStore(":memory:")
        LEGACY_SESSIONS_JSON.write_text(json.dumps({
            "K1": [
                {"start_time": 1793970000.0, "end_time": 1793973600.0, "duration": 3600, "user_id": "A"},
                {"start_time": 1793970000.0, "end_time": 1793973600.0, "duration": 3600},  # không user_id
            ]
        }), encoding="utf-8")
        with mock.patch.object(migration, "store", store):
            migration.migrate_sessions_to_sqlite()
            migration.migrate_sessions_to_sqlite()  # chạy lại không nhân đôi
        self.assertEqual(store.count(), 1)
        self.assertFalse(LEGACY_SESSIONS_JSON.exists())
        self.assertTrue(LEGACY_SESSIONS_JSON.with_name(LEGACY_SESSIONS_JSON.name + ".migrated").exists())
        store.close()


if __name__ == "__main__":
    unittest.main()
