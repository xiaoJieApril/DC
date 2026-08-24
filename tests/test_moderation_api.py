import unittest
from unittest.mock import patch

import dashboard_api
from fastapi import HTTPException


class ModerationApiTests(unittest.TestCase):
    def test_resolve_evidence_fetches_same_guild_message(self):
        message = {"id": "3", "content": "evidence", "author": {"id": "9", "username": "user"}, "attachments": []}
        with patch.object(dashboard_api, "discord_cached_get", return_value={"data": message, "stale": False, "cached_at": None}) as get:
            result = dashboard_api.resolve_moderation_evidence("1", dashboard_api.EvidenceResolvePayload(message_url="https://discord.com/channels/1/2/3"))
        self.assertEqual(result["evidence"]["author_id"], "9")
        self.assertIn("/channels/2/messages/3", get.call_args.args[0])

    def test_resolve_evidence_rejects_cross_guild_link_without_fetch(self):
        with patch.object(dashboard_api, "discord_cached_get") as get:
            with self.assertRaises(HTTPException) as raised:
                dashboard_api.resolve_moderation_evidence("1", dashboard_api.EvidenceResolvePayload(message_url="https://discord.com/channels/9/2/3"))
        self.assertEqual(raised.exception.status_code, 400)
        get.assert_not_called()

    def test_rule_api_normalizes_and_saves(self):
        payload = dashboard_api.ModerationRulesPayload(rules=[dashboard_api.ModerationRulePayload(
            rule_id="r1", number="1", name="Spam", reason="No spam", severity="serious", action="warning", enabled=True
        )])
        with patch.object(dashboard_api, "set_moderation_rules") as save, patch.object(dashboard_api, "append_audit_log"):
            result = dashboard_api.save_moderation_rules("10", payload)
        self.assertEqual(result["rules"][0]["name"], "Spam")
        save.assert_called_once()

    def test_moderation_and_ticket_views_return_counts(self):
        config = {
            "moderation_cases": {"1": [{"status": "open"}, {"status": "resolved"}]},
            "tickets": {"1": [{"status": "escalated"}, {"status": "rejected"}]},
        }
        with patch.object(dashboard_api, "load_config", return_value=config):
            cases = dashboard_api.get_moderation("1", 50, "active")
            tickets = dashboard_api.get_tickets("1", 50, "archive")
        self.assertEqual(len(cases["cases"]), 1)
        self.assertEqual(cases["counts"], {"active": 1, "archive": 1})
        self.assertEqual(tickets["tickets"][0]["status"], "rejected")

    def test_case_rejects_evidence_for_another_target(self):
        payload = dashboard_api.ModerationCasePayload(
            guild_id="1",
            target_user_id="9",
            reason="Reason",
            evidence_snapshot={"guild_id": "1", "author_id": "8", "jump_url": "https://discord.com/channels/1/2/3"},
        )
        with patch.object(dashboard_api, "load_config", return_value={}):
            with self.assertRaises(HTTPException) as raised:
                dashboard_api.create_moderation_case(payload)
        self.assertIn("author", str(raised.exception.detail).lower())

    def test_reopen_case_appends_status_history(self):
        existing = {"case_id": "CASE-0001", "status": "resolved", "status_history": []}
        with patch.object(dashboard_api, "load_config", return_value={"moderation_cases": {"1": [existing]}}), \
             patch.object(dashboard_api, "update_moderation_case", side_effect=lambda guild, case, update: {**existing, **update}) as update, \
             patch.object(dashboard_api, "append_audit_log"):
            result = dashboard_api.resolve_moderation_case("1", "CASE-0001", dashboard_api.ModerationResolvePayload(status="open", notes="Reopen"))
        self.assertEqual(result["status"], "open")
        self.assertEqual(update.call_args.args[2]["status_history"][0]["from"], "resolved")


if __name__ == "__main__":
    unittest.main()
