"""Kiểm thử Phần 3: panel nhiều menu, embed an toàn, quyền admin, BXH gửi bù."""
import asyncio
import datetime
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import tests  # noqa: F401

import discord

from cogs import background_cog
from config import VN_TZ
from core.data_manager import db
from core.session_store import SessionStore
from helpers import embeds, permissions


class UserPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_more_than_25_channels_split_into_menus(self):
        from views.user_view import UserSelectView
        db.save("channels", {str(1000 + i): {"name": f"Kênh {i}", "role_id": 0} for i in range(60)})
        view = UserSelectView()
        ids = [c.custom_id for c in view.children]
        self.assertEqual(ids, ["user_channel_select", "user_channel_select_2", "user_channel_select_3"])
        self.assertEqual([len(c.options) for c in view.children], [25, 25, 10])

    async def test_check_ctv_panel_uses_member_picker(self):
        from views.check_ctv_view import CheckCTVPanelView
        view = CheckCTVPanelView()
        self.assertIsInstance(view.children[0], discord.ui.UserSelect)


class EmbedSafetyTests(unittest.TestCase):
    def test_invalid_image_ignored_and_lengths_clamped(self):
        db.save("embeds", {"k": {"title": "T" * 300, "description": "D" * 5000, "image": "abc", "thumbnail": "https://x.y/a.png"}})
        e = embeds.get_custom_embed("k")
        self.assertIsNone(e.image.url)
        self.assertEqual(e.thumbnail.url, "https://x.y/a.png")
        self.assertLessEqual(len(e.title), 256)
        self.assertLessEqual(len(e.description), 4096)
        e.description += "X" * 3000   # bot ghép thêm trạng thái
        self.assertLessEqual(len(embeds.clamp_embed(e).description), 4096)


class PermissionTests(unittest.TestCase):
    def test_is_admin(self):
        admin = NS(guild_permissions=NS(administrator=True), roles=[])
        normal = NS(guild_permissions=NS(administrator=False), roles=[NS(id=5)])
        self.assertTrue(permissions.is_admin(admin))
        self.assertFalse(permissions.is_admin(normal))
        with mock.patch.object(permissions, "ADMIN_ROLE_ID", 5):
            self.assertTrue(permissions.is_admin(normal))


class LeaderboardCatchUpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SessionStore(":memory:")
        self.channel = NS(send=mock.AsyncMock())
        bot = NS(get_channel=lambda cid: self.channel, wait_until_ready=lambda: asyncio.Event().wait())
        self.cog = background_cog.BackgroundCog(bot)
        self.p = mock.patch.object(background_cog, "store", self.store)
        self.p.start()

    async def asyncTearDown(self):
        self.p.stop()
        self.cog.cog_unload()
        self.store.close()

    async def _run_at(self, dt):
        with mock.patch.object(background_cog, "now_vn", return_value=dt):
            await self.cog._send_leaderboard_if_due("test")

    async def test_first_run_does_not_duplicate_then_catches_up(self):
        await self._run_at(datetime.datetime(2026, 11, 8, 10, 0, tzinfo=VN_TZ))
        self.channel.send.assert_not_awaited()                     # lần đầu: chỉ ghi nhớ
        self.assertEqual(self.store.get_meta("leaderboard_last_day"), "2026-11-07")

        await self._run_at(datetime.datetime(2026, 11, 9, 0, 3, tzinfo=VN_TZ))
        self.channel.send.assert_not_awaited()                     # chưa tới 00:05

        await self._run_at(datetime.datetime(2026, 11, 9, 9, 0, tzinfo=VN_TZ))  # bot offline lúc 00:05
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.store.get_meta("leaderboard_last_day"), "2026-11-08")

        await self._run_at(datetime.datetime(2026, 11, 9, 9, 1, tzinfo=VN_TZ))
        self.channel.send.assert_awaited_once()                    # không gửi trùng


if __name__ == "__main__":
    unittest.main()
