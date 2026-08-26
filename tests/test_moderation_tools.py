import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import types

import bot
import storage

from moderation_tools import (
    evidence_snapshot_from_api,
    filter_status_view,
    moderation_strike_summary,
    normalize_moderation_rules,
    parse_discord_message_url,
    strike_action_for,
    status_counts,
    status_update,
    validate_moderation_rules,
)


class ModerationToolsTests(unittest.TestCase):
    def rule(self, number="1", action="warning", enabled=True, **extra):
        value = {
            "rule_id": f"rule-{number}",
            "number": number,
            "name": f"Rule {number}",
            "reason": "Reason",
            "severity": "normal",
            "action": action,
            "enabled": enabled,
        }
        value.update(extra)
        return value

    def test_message_link_parser_accepts_discord_hosts(self):
        expected = {"guild_id": "1", "channel_id": "2", "message_id": "3"}
        self.assertEqual(parse_discord_message_url("https://discord.com/channels/1/2/3"), expected)
        self.assertEqual(parse_discord_message_url("https://canary.discordapp.com/channels/1/2/3"), expected)
        with self.assertRaises(ValueError):
            parse_discord_message_url("https://example.com/channels/1/2/3")

    def test_evidence_snapshot_keeps_text_author_and_attachment_metadata(self):
        snapshot = evidence_snapshot_from_api({
            "id": "3",
            "content": "bad message",
            "timestamp": "2026-07-14T00:00:00+00:00",
            "author": {"id": "9", "username": "user", "global_name": "Display"},
            "attachments": [{"id": "4", "filename": "proof.png", "url": "https://cdn.test/proof.png", "content_type": "image/png", "size": 42}],
        }, "1", "2", captured_at=100)
        self.assertEqual(snapshot["author_id"], "9")
        self.assertEqual(snapshot["content"], "bad message")
        self.assertEqual(snapshot["attachments"][0]["filename"], "proof.png")
        self.assertEqual(snapshot["jump_url"], "https://discord.com/channels/1/2/3")

    def test_rule_validation_rejects_duplicates_limits_and_action_requirements(self):
        with self.assertRaisesRegex(ValueError, "duplicated"):
            validate_moderation_rules(normalize_moderation_rules([self.rule(), self.rule()]))
        with self.assertRaisesRegex(ValueError, "25"):
            validate_moderation_rules(normalize_moderation_rules([self.rule(str(i)) for i in range(26)]))
        with self.assertRaisesRegex(ValueError, "timeout"):
            validate_moderation_rules(normalize_moderation_rules([self.rule(action="timeout")]))
        with self.assertRaisesRegex(ValueError, "role"):
            validate_moderation_rules(normalize_moderation_rules([self.rule(action="remove_role")]))
        validate_moderation_rules(normalize_moderation_rules([self.rule(action="timeout", timeout_minutes=10)]))

    def test_active_archive_filter_counts_and_reopen_history(self):
        cases = [{"status": "open"}, {"status": "escalated"}, {"status": "resolved"}, {"status": "accepted"}, {"status": "rejected"}]
        self.assertEqual(len(filter_status_view(cases, "active", "case")), 2)
        self.assertEqual(status_counts(cases, "case"), {"active": 2, "archive": 3})
        ticket = {"status": "resolved", "status_history": []}
        update = status_update(ticket, "open", "admin", "reopen", now=200, kind="ticket")
        self.assertEqual(update["status"], "open")
        self.assertEqual(update["status_history"][0]["from"], "resolved")
        self.assertEqual(update["resolved_ts"], 0)

    def test_strike_summary_counts_recent_general_cases_only(self):
        now = 1_000_000
        cases = [
            {"case_id": "CASE-1", "target_user_id": "9", "severity": "normal", "status": "open", "ts": now - 10},
            {"case_id": "CASE-2", "target_user_id": "9", "severity": "serious", "status": "resolved", "ts": now - 20},
            {"case_id": "CASE-3", "target_user_id": "9", "severity": "red_line", "status": "open", "ts": now - 30},
            {"case_id": "CASE-4", "target_user_id": "9", "severity": "normal", "status": "accepted", "ts": now - 40},
            {"case_id": "CASE-5", "target_user_id": "9", "severity": "normal", "status": "open", "ts": now - (91 * 24 * 60 * 60)},
            {"case_id": "CASE-6", "target_user_id": "8", "severity": "normal", "status": "open", "ts": now - 50},
        ]
        summary = moderation_strike_summary(cases, "9", now=now)
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["next_count"], 3)
        self.assertIn("禁言一週", summary["suggested_action"])
        self.assertEqual([item["case_id"] for item in summary["recent_cases"]], ["CASE-1", "CASE-2"])

    def test_strike_actions_cover_first_through_fifth(self):
        expected = ["警告紀錄", "觀察期", "禁言一週", "踢出伺服器", "永久封鎖"]
        for index, text in enumerate(expected, start=1):
            self.assertIn(text, strike_action_for(index))

    def test_draft_claim_is_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            lock_file = Path(directory) / "config.json.lock"
            with patch.object(storage, "CONFIG_FILE", config_file), patch.object(storage, "LOCK_FILE", lock_file):
                storage.init_db()
                storage.save_moderation_draft("d1", {"status": "pending", "expires_at": 200})
                self.assertEqual(storage.get_moderation_draft("d1", 100)["status"], "pending")
                self.assertEqual(storage.claim_moderation_draft("d1", 100)["status"], "processing")
                self.assertIsNone(storage.claim_moderation_draft("d1", 100))

    def test_message_context_command_is_registered(self):
        commands = [(item.name, type(item).__name__) for item in bot.bot.pending_application_commands]
        self.assertIn(("Create Moderation Case", "MessageCommand"), commands)


class ModerationContextCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_command_rejects_non_staff(self):
        ctx = types.SimpleNamespace(
            author=types.SimpleNamespace(guild_permissions=types.SimpleNamespace(manage_messages=False, moderate_members=False)),
            respond=AsyncMock(),
        )
        await bot.create_moderation_case_from_message.callback(ctx, types.SimpleNamespace())
        ctx.respond.assert_awaited_once()
        self.assertIn("permission", ctx.respond.await_args.args[0].lower())

    async def test_context_command_requires_rules_before_creating_draft(self):
        ctx = types.SimpleNamespace(
            author=types.SimpleNamespace(id=5, guild_permissions=types.SimpleNamespace(manage_messages=True, moderate_members=False)),
            guild=types.SimpleNamespace(id=10),
            respond=AsyncMock(),
        )
        message = types.SimpleNamespace(guild=ctx.guild, author=types.SimpleNamespace(bot=False))
        with patch.object(bot, "load_config", return_value={}), patch.object(bot, "save_moderation_draft") as save:
            await bot.create_moderation_case_from_message.callback(ctx, message)
        save.assert_not_called()
        self.assertIn("no moderation rules", ctx.respond.await_args.args[0].lower())


if __name__ == "__main__":
    unittest.main()
