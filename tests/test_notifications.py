"""Kiểm thử thông báo khách, lịch nhắc theo CTV, hỏi giờ live tiếp theo."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import tests  # noqa: F401

from cogs import background_cog, live_tracker_cog
from config import KIND_STREAM
from core.data_manager import db
from core.session_store import SessionStore
from core.state import state
from core.tracker import Tracker
from helpers import migration
from helpers.time_utils import now_ts


class FakeBot:
    def __init__(self):
        self.guilds = []
        self.channels = {}
        self.members = {}

    def get_channel(self, cid):
        return self.channels.get(int(cid))

    def get_user(self, uid):
        return None

    async def wait_until_ready(self):
        await asyncio.Event().wait()

    def add_channel(self, cid):
        guild = NS(id=1, afk_channel=None, get_member=lambda i: self.members.get(int(i)))
        ch = NS(id=cid, guild=guild, name=f"K{cid}", members=[])
        self.channels[cid] = ch
        return ch


def member(uid, ch, stream):
    return NS(id=uid, bot=False, guild=ch.guild, display_name=f"u{uid}")


def voice(ch, stream):
    return NS(channel=ch, self_stream=stream)


class LiveNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.save("live_state", {})
        state.load_from_disk()
        db.save("ctv", {"1": ["100"], "2": ["200"]})
        db.save("ctv_subscribers", {"1": ["900"], "2": ["901"]})
        db.save("next_live", {
            "1": {"time": now_ts() + 3600, "time_str": "20:00", "channel_id": "100", "reminders_sent": 0},
            "2": {"time": now_ts() + 3600, "time_str": "21:00", "channel_id": "200", "reminders_sent": 0},
        })
        self.bot = FakeBot()
        self.k2 = self.bot.add_channel(200)
        self.store = SessionStore(":memory:")
        self.cog = live_tracker_cog.LiveTrackerCog(self.bot)
        self.cog.tracker = Tracker(state, self.store)
        self.cog._started = True
        self.cog._notify_subscribers = mock.AsyncMock()
        self.m1 = member(1, self.k2, True)
        self.m2 = member(2, self.k2, True)
        self.bot.members = {1: self.m1, 2: self.m2}

    async def asyncTearDown(self):
        self.store.close()

    async def test_ctv1_live_in_ctv2_channel_while_ctv2_live_notifies_ctv1_subs(self):
        off = NS(channel=None, self_stream=False)
        await self.cog.on_voice_state_update(self.m2, off, voice(self.k2, True))
        await self.cog.on_voice_state_update(self.m1, off, voice(self.k2, True))

        notified = [c.args[1].id for c in self.cog._notify_subscribers.await_args_list]
        self.assertEqual(notified, [2, 1])
        self.assertTrue(state.is_streaming("1") and state.is_streaming("2"))
        # Lịch nhắc của cả 2 bị hủy (mỗi người tự live), không hủy nhầm người khác
        self.assertEqual(db.load("next_live"), {})

    async def test_only_streamers_own_schedule_cancelled(self):
        off = NS(channel=None, self_stream=False)
        await self.cog.on_voice_state_update(self.m1, off, voice(self.k2, True))  # CTV1 live ở kênh CTV2
        self.assertEqual(list(db.load("next_live")), ["2"])

    async def _stream_off_on(self):
        off = NS(channel=None, self_stream=False)
        await self.cog.on_voice_state_update(self.m1, off, voice(self.k2, True))
        await self.cog.on_voice_state_update(self.m1, voice(self.k2, True), voice(self.k2, False))
        self.assertIn("1", self.cog._pending_prompts)          # hẹn hỏi giờ live sau 2 phút
        await self.cog.on_voice_state_update(self.m1, voice(self.k2, False), voice(self.k2, True))
        self.assertNotIn("1", self.cog._pending_prompts)       # bật lại → hủy hỏi

    async def test_default_notifies_every_time(self):
        """Mặc định (0 phút): CTV bật live lại → khách được báo lại ngay."""
        await self._stream_off_on()
        self.assertEqual(self.cog._notify_subscribers.await_count, 2)

    async def test_cooldown_when_configured(self):
        with mock.patch.object(live_tracker_cog, "NOTIFY_COOLDOWN_MINUTES", 30):
            await self._stream_off_on()
        self.assertEqual(self.cog._notify_subscribers.await_count, 1)  # không DM khách lần 2


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.save("live_state", {})
        state.load_from_disk()
        self.cog = background_cog.BackgroundCog(FakeBot())
        self.cog._send_reminder = mock.AsyncMock()
        self.cog._send_late_alert = mock.AsyncMock()

    async def asyncTearDown(self):
        self.cog.cog_unload()

    async def test_reminders(self):
        now = now_ts()
        db.save("next_live", {
            "late12": {"time": now - 12 * 60, "time_str": "x", "channel_id": "100", "reminders_sent": 0},
            "late40": {"time": now - 40 * 60, "time_str": "x", "channel_id": "100", "reminders_sent": 0},
            "live": {"time": now - 60, "time_str": "x", "channel_id": "100", "reminders_sent": 0},
            "future": {"time": now + 600, "time_str": "x", "channel_id": "100", "reminders_sent": 0},
        })
        state.set("live", KIND_STREAM, "555", now - 30)

        await self.cog.check_reminders()

        self.cog._send_reminder.assert_awaited_once_with("100", "late12", "x", 3)
        self.cog._send_late_alert.assert_awaited_once_with("100", "late40", "x")
        data = db.load("next_live")
        self.assertEqual(sorted(data), ["future", "late12"])
        self.assertEqual(data["late12"]["reminders_sent"], 3)

        await self.cog.check_reminders()  # chạy lại: không gửi thêm
        self.assertEqual(self.cog._send_reminder.await_count, 1)
        state.pop("live")


class NextLiveMigrationTests(unittest.TestCase):
    def test_channel_key_to_user_key(self):
        db.save("next_live", {
            "100": {"time": 2000.0, "time_str": "20:00", "user_id": 1, "reminders_sent": 2},
            "101": {"time": 1000.0, "time_str": "19:00", "user_id": 1, "reminders_sent": 0},
            "200": {"time": 3000.0, "time_str": "21:00", "user_id": "2", "reminders_sent": 0},
        })
        migration.migrate_next_live_per_user()
        self.assertEqual(db.load("next_live"), {
            "1": {"time": 1000.0, "time_str": "19:00", "channel_id": "101", "reminders_sent": 0},
            "2": {"time": 3000.0, "time_str": "21:00", "channel_id": "200", "reminders_sent": 0},
        })
        migration.migrate_next_live_per_user()  # chạy lại không đổi
        self.assertEqual(len(db.load("next_live")), 2)


class FollowCTVTests(unittest.IsolatedAsyncioTestCase):
    """Bật/Tắt thông báo theo CTV — nút ở panel server và nút trong DM phải giống nhau."""

    async def asyncSetUp(self):
        # CTV 1 quản kênh 100 + 101; CTV 2 quản kênh 101 (kênh chung) + 200
        db.save("ctv", {"1": ["100", "101"], "2": ["101", "200"]})
        db.save("channels", {c: {"name": f"Kênh {c}", "role_id": 0} for c in ("100", "101", "200")})
        db.save("ctv_subscribers", {})

    def _dm_inter(self, uid):
        return NS(user=NS(id=uid), client=NS(get_user=lambda i: None),
                  response=NS(defer=mock.AsyncMock()), followup=NS(send=mock.AsyncMock()))

    def _panel_inter(self, uid):
        return NS(user=NS(id=uid), guild=NS(get_member=lambda i: None),
                  response=NS(edit_message=mock.AsyncMock(), send_message=mock.AsyncMock()),
                  followup=NS(send=mock.AsyncMock()))

    async def _panel_toggle(self, uid, channel_id):
        from views.user_view import CustomerView
        view = CustomerView(channel_id, 1, False, uid)
        await view.toggle_notify(self._panel_inter(uid))
        return view

    async def test_panel_on_then_dm_off_is_same_as_panel_off(self):
        from helpers import follows
        from views.notify_view import NotifyOffCTVButton
        await self._panel_toggle(900, "100")                       # bật ở kênh 100 → theo dõi CTV 1
        self.assertEqual(follows.following(900, ["1", "2"]), ["1"])

        await NotifyOffCTVButton("1").callback(self._dm_inter(900))  # tắt trong DM của CTV 1
        self.assertEqual(follows.following(900, ["1", "2"]), [])
        # Panel kênh 100 và kênh 101 (cùng CTV 1) đều hiện lại "Bật Thông Báo"
        from views.user_view import CustomerView
        self.assertEqual(CustomerView("100", 1, False, 900).notify_btn.label, "Bật Thông Báo")

    async def test_panel_off_stops_dm_from_any_channel_of_ctv(self):
        from helpers import follows
        await self._panel_toggle(900, "100")       # bật
        await self._panel_toggle(900, "100")       # tắt ở panel
        self.assertEqual(follows.followers_of("1"), [])

    async def test_followers_get_dm_wherever_ctv_lives(self):
        from helpers import follows
        await follows.follow(900, ["1"])
        await follows.follow(901, ["1"])
        sent = {}

        def get_user(uid):
            return NS(send=mock.AsyncMock(side_effect=lambda **kw: sent.setdefault(uid, kw)))

        bot = FakeBot()
        bot.get_user = get_user
        cog = live_tracker_cog.LiveTrackerCog(bot)
        live_channel = NS(id=555, name="Kênh khác", guild=NS(id=1))   # CTV 1 live ở kênh lạ
        await cog._notify_subscribers(live_channel, NS(id=1, display_name="CTV1"))

        self.assertEqual(sorted(sent), [900, 901])
        ids = [c.custom_id for c in sent[900]["view"].children if getattr(c, "custom_id", None)]
        self.assertEqual(ids, ["notify_off_ctv:1"])
        self.assertIn("CTV CTV1", sent[900]["embed"].description)

    async def test_shared_channel_panel_follows_all_owners_dm_off_only_one(self):
        from helpers import follows
        from views.notify_view import NotifyOffCTVButton
        await self._panel_toggle(900, "101")                       # kênh chung của CTV 1 và 2
        self.assertEqual(sorted(follows.following(900, ["1", "2"])), ["1", "2"])
        await NotifyOffCTVButton("2").callback(self._dm_inter(900))  # tắt trong DM của CTV 2
        self.assertEqual(follows.following(900, ["1", "2"]), ["1"])

    async def test_channel_without_ctv(self):
        from views.user_view import CustomerView
        db.save("channels", {"300": {"name": "Trống", "role_id": 0}})
        view = CustomerView("300", 1, False, 900)
        self.assertTrue(view.notify_btn.disabled)

    async def test_legacy_dm_button(self):
        from helpers import follows
        from views.notify_view import NotifyOffButton
        await follows.follow(900, ["1"])
        await NotifyOffButton("100").callback(self._dm_inter(900))
        self.assertEqual(follows.followers_of("1"), [])
        inter = self._dm_inter(901)
        await NotifyOffButton("555").callback(inter)
        self.assertIn("kênh chọn live", inter.followup.send.await_args.args[0])


class SubscribersMigrationTests(unittest.TestCase):
    def test_channel_subs_become_ctv_follows(self):
        from config import LEGACY_SUBS_JSON
        import json as _json
        db.save("ctv", {"1": ["100", "101"], "2": ["101"]})
        db.save("ctv_subscribers", {})
        LEGACY_SUBS_JSON.write_text(_json.dumps(
            {"100": ["900"], "101": [900, "901"], "999": ["902"]}), encoding="utf-8")
        store = SessionStore(":memory:")
        with mock.patch.object(migration, "store", store):
            migration.migrate_subscribers_to_ctv()
            migration.migrate_subscribers_to_ctv()
        self.assertEqual(db.load("ctv_subscribers"), {"1": ["900", "901"], "2": ["900", "901"]})
        self.assertFalse(LEGACY_SUBS_JSON.exists())
        store.close()


if __name__ == "__main__":
    unittest.main()
