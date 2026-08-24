import unittest
from unittest.mock import patch

import dashboard_api


class DashboardResilienceTests(unittest.TestCase):
    def test_send_message_uses_guild_channel_cache_without_direct_preflight(self):
        payload = dashboard_api.MessagePayload(
            guild_id="10",
            channel_id="30",
            content="Hello",
            use_embed=False,
        )
        with patch.object(dashboard_api, "cached_guild_channel", return_value={"id": "30", "type": 0}) as cached, \
             patch.object(dashboard_api, "cached_channel") as direct_get, \
             patch.object(dashboard_api, "discord_request", return_value={"id": "40"}) as request, \
             patch.object(dashboard_api, "upsert_message"), \
             patch.object(dashboard_api, "append_audit_log"):
            result = dashboard_api.send_message(payload)
        self.assertEqual(result["guild_id"], "10")
        cached.assert_called_once_with("10", "30")
        direct_get.assert_not_called()
        request.assert_called_once()

    def test_legacy_send_payload_keeps_direct_channel_fallback(self):
        payload = dashboard_api.MessagePayload(channel_id="30", content="Hello", use_embed=False)
        with patch.object(dashboard_api, "cached_guild_channel") as selector_get, \
             patch.object(dashboard_api, "cached_channel", return_value={"id": "30", "guild_id": "10"}) as direct_get, \
             patch.object(dashboard_api, "discord_request", return_value={"id": "40"}), \
             patch.object(dashboard_api, "upsert_message"), \
             patch.object(dashboard_api, "append_audit_log"):
            result = dashboard_api.send_message(payload)
        self.assertEqual(result["guild_id"], "10")
        selector_get.assert_not_called()
        direct_get.assert_called_once_with("30")


if __name__ == "__main__":
    unittest.main()
