"""Kiểm thử luật Voice Alert: mọi kênh, xét cả kênh, giữ đồng hồ 5 phút."""
import unittest
from types import SimpleNamespace as NS

import tests  # noqa: F401

from cogs.voice_alert_cog import GRACE_SECONDS, apply_member, classify

CTV = {"1": ["K1"], "2": ["K2"], "3": []}


class World:
    def __init__(self):
        self.guild = NS(id=10, afk_channel=None)
        self.channels = {}

    def channel(self, cid):
        if cid not in self.channels:
            self.channels[cid] = NS(id=cid, guild=self.guild, members=[], type=None)
        return self.channels[cid]

    def member(self, uid, cid=None, stream=False):
        m = NS(id=uid, bot=False, voice=None, display_name=f"u{uid}")
        self.move(m, cid, stream)
        return m

    def move(self, m, cid, stream=False):
        for ch in self.channels.values():
            if m in ch.members:
                ch.members.remove(m)
        if cid is None:
            m.voice = None
        else:
            ch = self.channel(cid)
            ch.members.append(m)
            m.voice = NS(channel=ch, self_stream=stream)


class VoiceAlertRuleTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.data = {}

    def test_unmanaged_channel_is_tracked(self):
        m = self.w.member(3, cid=999)  # kênh chưa lưu trong hệ thống
        apply_member(self.data, m, CTV, 100.0)
        self.assertEqual(self.data["3"]["channel_id"], "999")

    def test_viewer_of_live_channel_not_tracked_then_tracked_when_live_ends(self):
        streamer = self.w.member(2, cid=5, stream=True)
        viewer = self.w.member(1, cid=5)
        apply_member(self.data, viewer, CTV, 100.0)
        self.assertNotIn("1", self.data)
        # CTV đang live rời kênh → xét lại người còn lại
        self.w.move(streamer, None)
        apply_member(self.data, viewer, CTV, 200.0)
        self.assertEqual(self.data["1"]["joined_ts"], 200.0)

    def test_rejoin_within_grace_keeps_timer(self):
        m = self.w.member(1, cid=5)
        apply_member(self.data, m, CTV, 100.0)
        self.w.move(m, None)
        apply_member(self.data, m, CTV, 200.0)
        self.assertEqual(self.data["1"]["paused_ts"], 200.0)
        self.w.move(m, 5)
        apply_member(self.data, m, CTV, 200.0 + GRACE_SECONDS - 1)
        self.assertEqual(self.data["1"]["joined_ts"], 100.0)
        self.assertNotIn("paused_ts", self.data["1"])

    def test_rejoin_after_grace_resets_timer(self):
        m = self.w.member(1, cid=5)
        apply_member(self.data, m, CTV, 100.0)
        self.w.move(m, None)
        apply_member(self.data, m, CTV, 200.0)
        self.w.move(m, 5)
        apply_member(self.data, m, CTV, 200.0 + GRACE_SECONDS + 1)
        self.assertEqual(self.data["1"]["joined_ts"], 200.0 + GRACE_SECONDS + 1)

    def test_move_channel_keeps_timer_and_stream_clears(self):
        m = self.w.member(1, cid=5)
        apply_member(self.data, m, CTV, 100.0)
        self.w.move(m, 6)
        apply_member(self.data, m, CTV, 150.0)
        self.assertEqual((self.data["1"]["channel_id"], self.data["1"]["joined_ts"]), ("6", 100.0))
        self.w.move(m, 6, stream=True)
        apply_member(self.data, m, CTV, 160.0)
        self.assertNotIn("1", self.data)

    def test_afk_and_non_ctv(self):
        afk = self.w.channel(77)
        self.w.guild.afk_channel = afk
        m = self.w.member(1, cid=77)
        self.assertEqual(classify(m, CTV)[0], "away")
        outsider = self.w.member(99, cid=5)
        self.assertIsNone(apply_member(self.data, outsider, CTV, 1.0))
        self.assertNotIn("99", self.data)

    def test_non_ctv_streamer_does_not_exempt(self):
        self.w.member(99, cid=5, stream=True)   # khách share màn hình
        ctv = self.w.member(1, cid=5)
        apply_member(self.data, ctv, CTV, 100.0)
        self.assertIn("1", self.data)


if __name__ == "__main__":
    unittest.main()
