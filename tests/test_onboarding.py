import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import dashboard_api
from welcome_automation import build_follow_up_job, onboarding_completion_role_ids


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def entry(self):
        return {
            "enabled": True,
            "fan_role_id": "99",
            "rules_title": "{label} Rules",
            "rules_color": "Blurple",
            "agree_label": "Agree",
            "languages": {
                "zh": {
                    "enabled": True,
                    "label": "中文",
                    "rules": "中文規則",
                    "language_role_id": "10",
                },
                "en": {
                    "enabled": True,
                    "label": "English",
                    "rules": "English rules",
                    "language_role_id": "20",
                },
            },
        }

    def test_language_selection_requires_exactly_one_value(self):
        self.assertEqual(bot.selected_onboarding_language(["zh"]), "zh")
        self.assertEqual(bot.selected_onboarding_language([]), "")
        self.assertEqual(bot.selected_onboarding_language(["zh", "en"]), "")

    def test_published_language_selector_is_single_choice(self):
        payload = dashboard_api.onboarding_panel_payload("1", self.entry())
        selector = payload["components"][0]["components"][0]
        self.assertEqual(selector["min_values"], 1)
        self.assertEqual(selector["max_values"], 1)
        self.assertEqual([item["value"] for item in selector["options"]], ["zh", "en"])

    def test_language_role_is_used_before_common_role(self):
        self.assertEqual(bot.onboarding_role_id(self.entry(), "zh"), "10")
        entry = self.entry()
        entry["languages"]["zh"]["language_role_id"] = ""
        self.assertEqual(bot.onboarding_role_id(entry, "zh"), "99")

    async def test_existing_language_role_member_sees_rules_without_button(self):
        role = SimpleNamespace(id=10, name="Chinese Fans")
        member = SimpleNamespace(id=5, roles=[role])
        guild = SimpleNamespace(id=1)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=5),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch.object(bot, "onboarding_member_and_role", AsyncMock(return_value=(member, role, ""))):
            await bot.send_onboarding_rules(interaction, self.entry(), "zh")
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertIsNone(kwargs["view"])
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["embed"].description, "中文規則")

    async def test_member_without_language_role_gets_one_agree_button(self):
        role = SimpleNamespace(id=10, name="Chinese Fans")
        member = SimpleNamespace(id=5, roles=[])
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(id=5),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch.object(bot, "onboarding_member_and_role", AsyncMock(return_value=(member, role, ""))):
            await bot.send_onboarding_rules(interaction, self.entry(), "zh")
        view = interaction.followup.send.await_args.kwargs["view"]
        self.assertIsInstance(view, bot.OnboardingAgreeView)
        self.assertEqual(len(view.children), 1)

    async def test_agree_adds_language_and_optional_common_role(self):
        language_role = SimpleNamespace(id=10, name="Chinese Fans")
        common_role = SimpleNamespace(id=99, name="Member")
        member = SimpleNamespace(id=5, roles=[], add_roles=AsyncMock())
        guild = SimpleNamespace(get_role=lambda role_id: common_role if role_id == 99 else None)
        interaction = SimpleNamespace(user=SimpleNamespace(id=5), guild=guild)
        with (
            patch.object(bot, "onboarding_member_and_role", AsyncMock(return_value=(member, language_role, ""))),
            patch.object(bot, "bot_can_manage_role", return_value=(True, "")),
        ):
            result = await bot.apply_onboarding_agreement(interaction, self.entry(), "zh")
        member.add_roles.assert_awaited_once_with(
            language_role,
            common_role,
            reason="Accepted zh onboarding rules",
        )
        self.assertIn("Chinese Fans", result)

    def test_welcome_job_snapshots_all_completion_roles(self):
        role_ids = onboarding_completion_role_ids(self.entry())
        self.assertEqual(role_ids, ["99", "10", "20"])
        job = build_follow_up_job("1", "2", "3", "Hi", "4", "99", 100, 60, role_ids)
        self.assertEqual(job["fan_role_ids"], ["99", "10", "20"])


if __name__ == "__main__":
    unittest.main()
