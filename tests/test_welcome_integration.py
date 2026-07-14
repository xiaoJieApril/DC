import unittest
from unittest.mock import AsyncMock, patch

import bot
import dashboard_api
from fastapi import HTTPException


class FakeChannel:
    def __init__(self, channel_id=30):
        self.id = channel_id
        self.send = AsyncMock()


class FakeGuild:
    def __init__(self, channel, member=None, role=None):
        self.id = 10
        self.name = "Gra-VT"
        self._channel = channel
        self._member = member
        self._role = role

    def get_channel(self, channel_id):
        return self._channel if channel_id == self._channel.id else None

    def get_member(self, member_id):
        return self._member if self._member and member_id == self._member.id else None

    def get_role(self, role_id):
        return self._role if self._role and role_id == self._role.id else None


class FakeMember:
    def __init__(self, guild=None, is_bot=False, roles=None):
        self.id = 20
        self.guild = guild
        self.bot = is_bot
        self.roles = roles or []


class FakeRole:
    def __init__(self, role_id=50):
        self.id = role_id


class WelcomeBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_member_is_welcomed_and_follow_up_is_queued(self):
        channel = FakeChannel()
        guild = FakeGuild(channel)
        member = FakeMember(guild)
        config = {
            "welcome_automation": {
                "10": {
                    "enabled": True,
                    "channel_id": "30",
                    "welcome_content": "Welcome {member} to {server}; read {rules_channel}",
                    "follow_up_enabled": True,
                    "follow_up_content": "Reminder {member}",
                    "delay_value": 1,
                    "delay_unit": "hours",
                }
            },
            "onboarding": {"10": {"channel_id": "40", "fan_role_id": "50"}},
        }
        with patch.object(bot, "load_config", return_value=config), \
             patch.object(bot, "enqueue_welcome_job") as enqueue, \
             patch.object(bot, "log_welcome_result"), \
             patch.object(bot.time, "time", return_value=100.0):
            await bot.on_member_join(member)

        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.args[0], "Welcome <@20> to Gra-VT; read <#40>")
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0]["due_at"], 3700.0)

    async def test_bot_member_is_ignored(self):
        member = FakeMember(is_bot=True)
        with patch.object(bot, "load_config") as load:
            await bot.on_member_join(member)
        load.assert_not_called()

    async def test_completed_member_follow_up_is_skipped(self):
        channel = FakeChannel()
        role = FakeRole()
        member = FakeMember(roles=[role])
        guild = FakeGuild(channel, member=member, role=role)
        member.guild = guild
        job = {"job_id": "j1", "guild_id": "10", "user_id": "20", "fan_role_id": "50"}
        with patch.object(bot.bot, "get_guild", return_value=guild), \
             patch.object(bot, "finish_welcome_job") as finish, \
             patch.object(bot, "log_welcome_result") as log:
            await bot.process_welcome_follow_up(job)
        finish.assert_called_once_with("j1")
        self.assertEqual(log.call_args.args[0], "follow_up_skipped")
        channel.send.assert_not_awaited()


class WelcomeApiTests(unittest.TestCase):
    def test_valid_settings_are_saved(self):
        payload = dashboard_api.WelcomeAutomationPayload(
            enabled=True,
            channel_id="30",
            welcome_content="Welcome {member}; read {rules_channel}",
            follow_up_enabled=True,
            follow_up_content="Reminder {member}",
            delay_value=1,
            delay_unit="hours",
        )
        config = {"onboarding": {"10": {"channel_id": "40", "fan_role_id": "50"}}}
        with patch.object(dashboard_api, "load_config", return_value=config), \
             patch.object(dashboard_api, "cached_channel", return_value={"guild_id": "10"}), \
             patch.object(dashboard_api, "upsert_welcome_automation") as upsert, \
             patch.object(dashboard_api, "append_audit_log"):
            result = dashboard_api.save_welcome_automation("10", payload)
        self.assertTrue(result["enabled"])
        upsert.assert_called_once()

    def test_channel_from_another_server_is_rejected(self):
        payload = dashboard_api.WelcomeAutomationPayload(
            enabled=True,
            channel_id="30",
            welcome_content="Welcome {member}",
        )
        with patch.object(dashboard_api, "load_config", return_value={}), \
             patch.object(dashboard_api, "cached_channel", return_value={"guild_id": "99"}):
            with self.assertRaises(HTTPException) as raised:
                dashboard_api.save_welcome_automation("10", payload)
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
