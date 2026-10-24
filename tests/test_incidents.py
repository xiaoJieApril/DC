import tempfile
import unittest
from pathlib import Path

from observability import ObservabilityStore

T0 = 1_700_000_000.0


class IncidentLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ObservabilityStore(Path(self.tmp.name) / "obs.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def beat(self, t, online=True, latency=100.0, started=T0, memory=50.0, lag=None, jump=None):
        self.store.record_heartbeat(online, latency, 1, t - started, memory, recorded_at=t, loop_lag_ms=lag, clock_jump_ms=jump)

    def healthy(self, start, end, started=T0, step=15):
        t = start
        while t <= end:
            self.beat(t, started=started)
            t += step

    def incidents(self, now):
        self.store.scan_incidents(now)
        return list(reversed(self.store.list_incidents(50, now)))

    def test_restart_without_shutdown_is_a_crash(self):
        self.healthy(T0, T0 + 60)
        self.healthy(T0 + 300, T0 + 330, started=T0 + 290)
        found = self.incidents(T0 + 340)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "no_heartbeat")
        self.assertEqual(found[0]["reason_code"], "process_crashed")
        self.assertFalse(found[0]["planned"])
        self.assertEqual(found[0]["duration_seconds"], 240)

    def test_dashboard_stop_is_planned(self):
        self.healthy(T0, T0 + 60)
        self.store.record_event("manual_stop", "Dashboard 停止 bot", recorded_at=T0 + 65)
        self.healthy(T0 + 300, T0 + 330, started=T0 + 290)
        found = self.incidents(T0 + 340)
        self.assertEqual(found[0]["reason_code"], "manual_stop")
        self.assertTrue(found[0]["planned"])

    def test_clean_shutdown_event_is_planned_restart(self):
        self.healthy(T0, T0 + 60)
        self.store.record_event("shutdown", "进程正常退出", recorded_at=T0 + 70)
        self.healthy(T0 + 200, T0 + 230, started=T0 + 190)
        found = self.incidents(T0 + 240)
        self.assertEqual(found[0]["reason_code"], "service_restart")
        self.assertTrue(found[0]["planned"])

    def test_same_process_stalled_event_loop(self):
        self.healthy(T0, T0 + 60)
        self.store.record_feature("messages", "/sendmessage", True, recorded_at=T0 + 70)
        self.beat(T0 + 160, lag=85_000, jump=0)
        found = self.incidents(T0 + 170)
        self.assertEqual(found[0]["reason_code"], "event_loop_blocked")
        self.assertTrue(any("messages" in line for line in found[0]["evidence"]))

    def test_same_process_host_suspended(self):
        self.healthy(T0, T0 + 60)
        self.beat(T0 + 160, lag=0, jump=95_000)
        found = self.incidents(T0 + 170)
        self.assertEqual(found[0]["reason_code"], "host_suspended")

    def test_disconnect_reason_from_gateway_log(self):
        self.healthy(T0, T0 + 30)
        self.store.record_event("log", "discord.client: Shard ID None WebSocket closed with 4014: Disallowed intent(s)", recorded_at=T0 + 40)
        self.beat(T0 + 45, online=False, latency=None)
        self.beat(T0 + 60, online=False, latency=None)
        self.beat(T0 + 75)
        found = self.incidents(T0 + 80)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "disconnected")
        self.assertEqual(found[0]["reason_code"], "bad_intents")
        self.assertFalse(found[0]["ongoing"])

    def test_short_disconnect_without_logs_is_reconnect(self):
        self.healthy(T0, T0 + 30)
        self.beat(T0 + 45, online=False, latency=None)
        self.beat(T0 + 60)
        found = self.incidents(T0 + 70)
        self.assertEqual(found[0]["reason_code"], "gateway_reconnect")

    def test_single_slow_sample_is_not_an_incident(self):
        self.healthy(T0, T0 + 30)
        self.beat(T0 + 45, latency=400.0)
        self.healthy(T0 + 60, T0 + 90)
        self.assertEqual(self.incidents(T0 + 100), [])

    def test_sustained_high_latency_with_blocked_loop(self):
        self.healthy(T0, T0 + 30)
        self.beat(T0 + 45, latency=900.0, lag=6000)
        self.beat(T0 + 60, latency=700.0, lag=5000)
        self.healthy(T0 + 75, T0 + 105)
        found = self.incidents(T0 + 110)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "high_latency")
        self.assertEqual(found[0]["started_at"], T0 + 45)
        self.assertEqual(found[0]["peak_latency_ms"], 900.0)
        self.assertEqual(found[0]["reason_code"], "event_loop_blocked")

    def test_high_latency_without_clues_points_at_network(self):
        self.healthy(T0, T0 + 30)
        self.beat(T0 + 45, latency=500.0)
        self.beat(T0 + 60, latency=480.0)
        self.healthy(T0 + 75, T0 + 90)
        found = self.incidents(T0 + 100)
        self.assertEqual(found[0]["reason_code"], "network_latency")

    def test_ongoing_gap_opens_live_then_closes_without_duplicates(self):
        self.healthy(T0, T0 + 60)
        live = self.incidents(T0 + 200)
        self.assertEqual(len(live), 1)
        self.assertTrue(live[0]["ongoing"])
        self.assertEqual(live[0]["reason_code"], "bot_down")
        self.assertEqual(len(self.incidents(T0 + 205)), 1)  # scanning again must not duplicate
        self.beat(T0 + 210, started=T0 + 205)
        closed = self.incidents(T0 + 215)
        self.assertEqual(len(closed), 1)
        self.assertFalse(closed[0]["ongoing"])
        self.assertEqual(closed[0]["reason_code"], "process_crashed")

    def test_summary_includes_incidents(self):
        self.healthy(T0, T0 + 60)
        self.healthy(T0 + 300, T0 + 330, started=T0 + 290)
        summary = self.store.summary(now=T0 + 340)
        self.assertEqual(len(summary["incidents"]), 1)
        self.assertEqual(summary["incidents"][0]["reason_code"], "process_crashed")


if __name__ == "__main__":
    unittest.main()
