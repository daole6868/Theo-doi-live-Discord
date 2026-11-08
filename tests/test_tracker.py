"""Kiểm thử bộ tính giờ: nhiều CTV chung kênh, restart, qua đêm, audio."""
import datetime
import json
import unittest
from types import SimpleNamespace

import tests  # noqa: F401  (thiết lập môi trường)

from config import DATA_DIR, KIND_AUDIO, KIND_STREAM, VN_TZ
from core.data_manager import db
from core.session_store import SessionStore
from core.state import LiveState
from core.tracker import FLAG_OFFLINE, Tracker, desired_from_voice


def ts(day: int, hour: int, minute: int = 0, month: int = 11) -> float:
    return datetime.datetime(2026, month, day, hour, minute, tzinfo=VN_TZ).timestamp()


def totals(store: SessionStore, user_id: str, kind: str = KIND_STREAM) -> int:
    rows = store.query(datetime.date(2026, 1, 1), datetime.date(2026, 12, 31), [user_id], kind)
    return sum(r["duration"] for r in rows)


STREAM = lambda cid: (KIND_STREAM, cid)  # noqa: E731
AUDIO = lambda cid: (KIND_AUDIO, cid)  # noqa: E731


class TrackerTestBase(unittest.TestCase):
    def setUp(self):
        db.save("live_state", {})
        self.state = LiveState()
        self.store = SessionStore(":memory:")
        self.tracker = Tracker(self.state, self.store)

    def tearDown(self):
        self.store.close()


class MultiStreamerTests(TrackerTestBase):
    def test_two_ctv_same_channel_second_stops_first(self):
        """Lỗi cũ: B tắt trước làm chốt phiên của A, B bị ghi 0."""
        t = self.tracker
        t.apply("A", STREAM("X"), ts(8, 20, 0))
        t.apply("B", STREAM("X"), ts(8, 20, 10))
        t.apply("B", None, ts(8, 21, 0))
        t.apply("A", None, ts(8, 22, 0))
        self.assertEqual(totals(self.store, "A"), 2 * 3600)
        self.assertEqual(totals(self.store, "B"), 50 * 60)

    def test_three_ctv_in_owner_channel(self):
        """CTV1 + CTV2 vào kênh CTV3 đang live — cả 3 đều được tính đủ."""
        t = self.tracker
        t.apply("CTV3", STREAM("K3"), ts(8, 20, 0))
        t.apply("CTV1", STREAM("K3"), ts(8, 20, 10))
        t.apply("CTV2", STREAM("K3"), ts(8, 20, 20))
        t.apply("CTV1", None, ts(8, 21, 0))
        t.apply("CTV2", None, ts(8, 22, 0))
        t.apply("CTV3", None, ts(8, 23, 0))
        self.assertEqual(totals(self.store, "CTV3"), 3 * 3600)
        self.assertEqual(totals(self.store, "CTV1"), 50 * 60)
        self.assertEqual(totals(self.store, "CTV2"), 100 * 60)

    def test_streamer_gets_time_not_channel_owner(self):
        self.tracker.apply("CTV1", STREAM("kenh_ctv2"), ts(8, 20))
        self.tracker.apply("CTV1", None, ts(8, 22))
        rows = self.store.query(datetime.date(2026, 11, 8), datetime.date(2026, 11, 8))
        self.assertEqual([(r["user_id"], r["channel_id"], r["duration"]) for r in rows],
                         [("CTV1", "kenh_ctv2", 7200)])


class AudioAndTransitionTests(TrackerTestBase):
    def test_audio_and_stream_counted_separately(self):
        t = self.tracker
        t.apply("A", AUDIO("X"), ts(8, 19, 50))   # vào kênh, chưa live
        t.apply("A", STREAM("X"), ts(8, 20, 0))   # bật Go Live
        t.apply("A", AUDIO("X"), ts(8, 21, 0))    # tắt Go Live, vẫn ngồi
        t.apply("A", None, ts(8, 21, 5))          # rời kênh
        self.assertEqual(totals(self.store, "A", KIND_STREAM), 3600)
        self.assertEqual(totals(self.store, "A", KIND_AUDIO), 15 * 60)

    def test_same_state_is_noop(self):
        t = self.tracker
        t.apply("A", STREAM("X"), ts(8, 20))
        self.assertEqual(t.apply("A", STREAM("X"), ts(8, 20, 30)), [])  # tắt/bật mic
        self.assertEqual(self.state.get("A")["start_ts"], ts(8, 20))

    def test_move_channel_while_streaming(self):
        t = self.tracker
        t.apply("A", STREAM("X"), ts(8, 20))
        t.apply("A", STREAM("Y"), ts(8, 21))
        t.apply("A", None, ts(8, 21, 30))
        rows = self.store.query(datetime.date(2026, 11, 8), datetime.date(2026, 11, 8), ["A"])
        self.assertEqual([(r["channel_id"], r["duration"]) for r in rows], [("X", 3600), ("Y", 1800)])

    def test_short_session_ignored(self):
        self.tracker.apply("A", STREAM("X"), ts(8, 20))
        self.tracker.apply("A", None, ts(8, 20) + 3)
        self.assertEqual(totals(self.store, "A"), 0)


class MidnightTests(TrackerTestBase):
    def test_session_split_at_midnight(self):
        self.tracker.apply("A", STREAM("X"), ts(8, 23))
        self.tracker.apply("A", None, ts(9, 1))
        rows = self.store.query(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), ["A"])
        self.assertEqual([(r["day"], r["duration"]) for r in rows],
                         [("2026-11-08", 3600), ("2026-11-09", 3600)])

    def test_multi_day_session(self):
        self.tracker.apply("A", AUDIO("X"), ts(8, 22))
        self.tracker.apply("A", None, ts(10, 2))
        rows = self.store.query(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), ["A"])
        self.assertEqual([(r["day"], r["duration"]) for r in rows],
                         [("2026-11-08", 7200), ("2026-11-09", 86400), ("2026-11-10", 7200)])


class RestartTests(TrackerTestBase):
    def test_restart_with_heartbeat_does_not_count_downtime(self):
        self.tracker.apply("A", STREAM("X"), ts(8, 20))
        # Bot chết, nhịp tim cuối 21:00, bật lại 21:30 — A vẫn đang live
        changes = self.tracker.startup({"A": STREAM("X")}, ts(8, 21, 30), ts(8, 21), 86400)
        rows = self.store.query(datetime.date(2026, 11, 8), datetime.date(2026, 11, 8), ["A"])
        self.assertEqual([(r["duration"], r["flags"]) for r in rows], [(3600, FLAG_OFFLINE)])
        self.assertEqual(self.state.get("A")["start_ts"], ts(8, 21, 30))
        self.assertEqual([c.action for c in changes], ["stop", "start"])

    def test_restart_user_stopped_while_offline(self):
        """Lỗi cũ: phiên 'ma' tồn tại mãi. Giờ chốt tại nhịp tim cuối."""
        self.tracker.apply("A", STREAM("X"), ts(8, 20))
        self.tracker.startup({}, ts(9, 9), ts(8, 20, 30), 86400)
        self.assertEqual(totals(self.store, "A"), 30 * 60)
        self.assertIsNone(self.state.get("A"))

    def test_first_run_without_heartbeat_drops_stale_legacy(self):
        self.state.set("OLD", KIND_STREAM, "X", ts(5, 20))       # phiên ma 3 ngày trước
        self.state.set("NOW", KIND_STREAM, "Y", ts(8, 20, 30))   # đang live thật
        self.tracker.startup({"NOW": STREAM("Y")}, ts(8, 21), None, 24 * 3600)
        self.assertIsNone(self.state.get("OLD"))
        self.assertEqual(totals(self.store, "OLD"), 0)
        self.assertEqual(self.state.get("NOW")["start_ts"], ts(8, 20, 30))  # giữ nguyên

    def test_reconcile_after_disconnect_uses_disconnect_time(self):
        self.tracker.apply("A", STREAM("X"), ts(8, 20))
        self.tracker.apply("B", STREAM("X"), ts(8, 20))
        # Mất kết nối 20:30, vào lại 21:00: A đã rời, B vẫn live
        self.tracker.reconcile({"B": STREAM("X")}, ts(8, 21), end_ts=ts(8, 20, 30))
        self.assertEqual(totals(self.store, "A"), 30 * 60)
        self.assertEqual(self.state.get("B")["start_ts"], ts(8, 20))  # B không bị cắt

    def test_legacy_state_format_is_converted(self):
        db.save("live_state", {"999": {"start_ts": ts(8, 20), "user_id": "42"}})
        st = LiveState()
        st.load_from_disk()
        self.assertEqual(st.get("42"), {"kind": KIND_STREAM, "channel_id": "999", "start_ts": ts(8, 20)})
        self.assertIn("42", json.loads((DATA_DIR / "data_live_state.json").read_text(encoding="utf-8")))


class DesiredFromVoiceTests(unittest.TestCase):
    def test_values(self):
        afk = SimpleNamespace(id=1)
        guild = SimpleNamespace(afk_channel=afk)
        ch = SimpleNamespace(id=5, guild=guild)
        self.assertIsNone(desired_from_voice(None))
        self.assertIsNone(desired_from_voice(SimpleNamespace(channel=None)))
        self.assertEqual(desired_from_voice(SimpleNamespace(channel=ch, self_stream=True)), STREAM("5"))
        self.assertEqual(desired_from_voice(SimpleNamespace(channel=ch, self_stream=False)), AUDIO("5"))
        afk_voice = SimpleNamespace(channel=SimpleNamespace(id=1, guild=guild), self_stream=False)
        self.assertIsNone(desired_from_voice(afk_voice))


if __name__ == "__main__":
    unittest.main()
