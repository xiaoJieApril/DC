import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import bot


class FakeCoordinator:
    def acquire(self, key, cooldown):
        return True, 0

    def increment(self, name, amount=1):
        return None

    def check_window(self, key, limit, window_seconds=1):
        return True, 0


class Emoji:
    id = None
    animated = False

    def __str__(self):
        return "✅"


class BotRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_dropdown_batches_adds_and_removes(self):
        add_role = types.SimpleNamespace(id=1, name="Add")
        remove_role = types.SimpleNamespace(id=2, name="Remove")
        member = types.SimpleNamespace(
            roles=[remove_role],
            add_roles=AsyncMock(),
            remove_roles=AsyncMock(),
        )
        guild = types.SimpleNamespace(
            id=10,
            get_member=lambda user_id: member,
            get_role=lambda role_id: {1: add_role, 2: remove_role}.get(role_id),
        )
        interaction = types.SimpleNamespace(guild=guild, user=types.SimpleNamespace(id=20))
        entry = {"mappings": {"a": "1", "b": "2"}}
        with patch.object(bot, "bot_can_manage_role", return_value=(True, "")):
            await bot.apply_role_selection(interaction, entry, ["1"])
        member.add_roles.assert_awaited_once_with(add_role, reason="Dropdown role selection")
        member.remove_roles.assert_awaited_once_with(remove_role, reason="Dropdown role selection")

    async def test_reaction_burst_applies_only_final_state(self):
        role = types.SimpleNamespace(id=7, name="Role")
        member = types.SimpleNamespace(
            roles=[role], bot=False, display_name="Member",
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        guild = types.SimpleNamespace(id=10, get_role=lambda role_id: role)
        fake_bot = types.SimpleNamespace(user=types.SimpleNamespace(id=999), get_guild=lambda guild_id: guild)
        payload = types.SimpleNamespace(guild_id=10, user_id=20, message_id=30, emoji=Emoji(), member=member)
        config = {"reaction_roles": {"10": {"30": {"mappings": {"✅": "7"}}}}}
        bot.REACTION_PENDING.clear()
        bot.REACTION_TASKS.clear()
        with patch.object(bot, "bot", fake_bot), \
             patch.object(bot, "load_config", return_value=config), \
             patch.object(bot, "RATE_COORDINATOR", FakeCoordinator()), \
             patch.object(bot, "bot_can_manage_role", return_value=(True, "")):
            await bot.queue_reaction_change(payload, True)
            await bot.queue_reaction_change(payload, False)
            task = next(iter(bot.REACTION_TASKS.values()))
            await asyncio.wait_for(task, timeout=2)
        member.add_roles.assert_not_awaited()
        member.remove_roles.assert_awaited_once_with(role, reason="Reaction role removed")


if __name__ == "__main__":
    unittest.main()
