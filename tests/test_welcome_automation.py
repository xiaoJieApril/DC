import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv_stub

import storage
from welcome_automation import (
    MAX_DELAY_SECONDS,
    MIN_DELAY_SECONDS,
    build_follow_up_job,
    follow_up_delay_seconds,
    normalize_welcome_config,
    render_welcome_template,
    validate_welcome_config,
)


class WelcomeAutomationTests(unittest.TestCase):
    def test_template_renders_supported_variables_only(self):
        result = render_welcome_template(
            "Hello {member}, welcome to {server}. Read {rules_channel}. {unknown}",
            "42",
            "Gra-VT",
            "99",
        )
        self.assertEqual(result, "Hello <@42>, welcome to Gra-VT. Read <#99>. {unknown}")

    def test_config_normalization_and_delay_units(self):
        config = normalize_welcome_config({
            "enabled": True,
            "channel_id": 12,
            "delay_value": "2",
            "delay_unit": "days",
        })
        self.assertTrue(config["enabled"])
        self.assertEqual(config["channel_id"], "12")
        self.assertEqual(follow_up_delay_seconds(config), 2 * 86400)
        self.assertEqual(MIN_DELAY_SECONDS, 60)
        self.assertEqual(MAX_DELAY_SECONDS, 30 * 86400)

    def test_follow_up_job_snapshots_join_settings(self):
        job = build_follow_up_job("1", "2", "3", "Hi {member}", "4", "5", 100.25, 3600)
        self.assertEqual(job["job_id"], "1:2:100250")
        self.assertEqual(job["due_at"], 3700.25)
        self.assertEqual(job["rules_channel_id"], "4")
        self.assertEqual(job["fan_role_id"], "5")

    def test_validation_requires_content_rules_setup_and_valid_delay(self):
        config = normalize_welcome_config({"enabled": True, "channel_id": "3"})
        with self.assertRaisesRegex(ValueError, "Welcome message"):
            validate_welcome_config(config, {})

        config.update({
            "welcome_content": "Welcome {member}, read {rules_channel}",
            "follow_up_enabled": True,
            "follow_up_content": "Reminder {member}",
            "delay_value": 31,
            "delay_unit": "days",
        })
        with self.assertRaisesRegex(ValueError, "Rules channel"):
            validate_welcome_config(config, {})
        with self.assertRaisesRegex(ValueError, "30 days"):
            validate_welcome_config(config, {"channel_id": "4", "fan_role_id": "5"})

        config["delay_value"] = 1
        config["delay_unit"] = "minutes"
        validate_welcome_config(config, {"channel_id": "4", "fan_role_id": "5"})

    def test_persistent_queue_claim_retry_finish_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            lock_file = Path(directory) / "config.json.lock"
            with patch.object(storage, "CONFIG_FILE", config_file), patch.object(storage, "LOCK_FILE", lock_file):
                storage.init_db()
                job = build_follow_up_job("1", "2", "3", "Reminder", "4", "5", 100, 60)
                self.assertTrue(storage.enqueue_welcome_job(job))
                self.assertFalse(storage.enqueue_welcome_job(job))
                self.assertEqual(storage.claim_due_welcome_jobs(159), [])
                claimed = storage.claim_due_welcome_jobs(160)
                self.assertEqual([item["job_id"] for item in claimed], [job["job_id"]])
                self.assertEqual(storage.claim_due_welcome_jobs(161), [])

                self.assertTrue(storage.retry_welcome_job(job["job_id"], 500, "temporary"))
                reloaded = storage.load_config()
                self.assertEqual(reloaded["welcome_jobs"][0]["attempts"], 1)
                self.assertEqual(storage.claim_due_welcome_jobs(499), [])
                self.assertEqual(len(storage.claim_due_welcome_jobs(500)), 1)
                self.assertTrue(storage.finish_welcome_job(job["job_id"]))
                self.assertEqual(storage.load_config()["welcome_jobs"], [])

    def test_disabling_one_guild_only_cancels_its_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            lock_file = Path(directory) / "config.json.lock"
            with patch.object(storage, "CONFIG_FILE", config_file), patch.object(storage, "LOCK_FILE", lock_file):
                storage.init_db()
                storage.enqueue_welcome_job(build_follow_up_job("1", "2", "3", "A", "4", "5", 1, 60))
                storage.enqueue_welcome_job(build_follow_up_job("9", "8", "7", "B", "6", "5", 1, 60))
                self.assertEqual(storage.cancel_pending_welcome_jobs("1"), 1)
                jobs = storage.load_config()["welcome_jobs"]
                self.assertEqual([item["guild_id"] for item in jobs], ["9"])


if __name__ == "__main__":
    unittest.main()
