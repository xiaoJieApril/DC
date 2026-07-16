import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    requests_stub.RequestException = Exception
    import sys
    sys.modules["requests"] = requests_stub

from discord_guard import DiscordGuard, DiscordGuardError


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if text is None and body is not None else (text or "")
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, responses, delay=0):
        self.responses = list(responses)
        self.calls = []
        self.delay = delay
        self.headers = {}
        self._lock = threading.Lock()

    def request(self, method, url, **kwargs):
        with self._lock:
            self.calls.append((method, url))
            response = self.responses.pop(0)
        if self.delay:
            time.sleep(self.delay)
        return response


class DiscordGuardTests(unittest.TestCase):
    def make_guard(self, session, cache_file=None, now=None, sleeper=None):
        clock = (lambda: now[0]) if now is not None else None
        return DiscordGuard(
            "https://discord.test/api/v10",
            lambda: "secret-token",
            cache_file=cache_file,
            session=session,
            clock=clock,
            sleeper=sleeper,
        )

    def test_fresh_get_cache_uses_one_upstream_request(self):
        session = FakeSession([FakeResponse(body=[{"id": "1"}])])
        guard = self.make_guard(session)
        first = guard.get("/users/@me/guilds", "guilds")
        second = guard.get("/users/@me/guilds", "guilds")
        self.assertEqual(first["data"], second["data"])
        self.assertEqual(len(session.calls), 1)

    def test_peek_reads_persistent_selector_cache_without_upstream_request(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "discord_cache.json"
            cache_file.write_text(
                json.dumps({"channels:1": {"data": [{"id": "10"}], "cached_at": 1000}}),
                encoding="utf-8",
            )
            session = FakeSession([FakeResponse(body={"should": "not run"})])
            guard = self.make_guard(session, cache_file, now=[1200])
            result = guard.peek("channels:1")
        self.assertEqual(result["data"], [{"id": "10"}])
        self.assertEqual(session.calls, [])

    def test_parallel_gets_are_coalesced(self):
        session = FakeSession([FakeResponse(body=[{"id": "1"}])], delay=0.05)
        guard = self.make_guard(session)
        results = []
        threads = [threading.Thread(target=lambda: results.append(guard.get("/guilds/1/roles", "roles:1"))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 4)
        self.assertEqual(len(session.calls), 1)

    def test_json_429_opens_circuit_and_get_retries_once(self):
        now = [1000.0]
        session = FakeSession([
            FakeResponse(429, {"message": "rate limited", "retry_after": 2}),
            FakeResponse(200, [{"id": "1"}]),
        ])
        guard = self.make_guard(session, now=now, sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds))
        result = guard.get("/users/@me/guilds", "guilds")
        self.assertEqual(result["data"][0]["id"], "1")
        self.assertEqual(len(session.calls), 2)

    def test_cloudflare_1015_opens_circuit_without_leaking_html(self):
        html = "<title>Error 1015</title><h2>You are being rate limited</h2><span>Ray ID: abc123</span>"
        session = FakeSession([FakeResponse(429, text=html)])
        guard = self.make_guard(session)
        with self.assertRaises(DiscordGuardError) as raised:
            guard.get("/users/@me/guilds", "guilds")
        self.assertEqual(raised.exception.retry_after_seconds, 900)
        self.assertNotIn("<title>", raised.exception.message)
        with self.assertRaises(DiscordGuardError):
            guard.get("/guilds/1/roles", "roles:1")
        self.assertEqual(len(session.calls), 1)

    def test_stale_persistent_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "discord_cache.json"
            now = [1000.0]
            first = self.make_guard(FakeSession([FakeResponse(body=[{"id": "1"}])]), cache_file, now)
            first.get("/users/@me/guilds", "guilds", persist=True)
            now[0] += 600
            blocked = FakeSession([FakeResponse(429, text="Error 1015 You are being rate limited")])
            second = self.make_guard(blocked, cache_file, now)
            result = second.get("/users/@me/guilds", "guilds", persist=True)
            self.assertTrue(result["stale"])
            self.assertEqual(result["data"][0]["id"], "1")

    def test_cache_older_than_stale_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "discord_cache.json"
            cache_file.write_text(json.dumps({"guilds": {"data": [{"id": "old"}], "cached_at": 1}}), encoding="utf-8")
            now = [90000.0]
            session = FakeSession([FakeResponse(429, text="Error 1015 You are being rate limited")])
            guard = self.make_guard(session, cache_file, now)
            with self.assertRaises(DiscordGuardError):
                guard.get("/users/@me/guilds", "guilds", persist=True)

    def test_write_429_is_never_retried(self):
        session = FakeSession([
            FakeResponse(429, {"message": "rate limited", "retry_after": 1}),
            FakeResponse(200, {"id": "should-not-run"}),
        ])
        guard = self.make_guard(session)
        with self.assertRaises(DiscordGuardError):
            guard.request("POST", "/channels/1/messages", {"content": "hello"})
        self.assertEqual(len(session.calls), 1)

    def test_exhausted_response_bucket_blocks_next_write_before_upstream(self):
        now = [1000.0]
        session = FakeSession([FakeResponse(200, {"id": "1"}, headers={
            "X-RateLimit-Bucket": "message-bucket",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset-After": "10",
        })])
        guard = self.make_guard(session, now=now)
        guard.request("POST", "/channels/1/messages", {"content": "first"})
        with self.assertRaises(DiscordGuardError) as raised:
            guard.request("POST", "/channels/1/messages", {"content": "second"})
        self.assertEqual(raised.exception.code, "discord_bucket_cooldown")
        self.assertEqual(len(session.calls), 1)

    def test_unauthorized_response_pauses_follow_up_requests(self):
        session = FakeSession([FakeResponse(401, {"message": "invalid token"})])
        guard = self.make_guard(session)
        with self.assertRaises(DiscordGuardError):
            guard.request("GET", "/users/@me")
        with self.assertRaises(DiscordGuardError) as raised:
            guard.request("GET", "/users/@me/guilds")
        self.assertEqual(raised.exception.code, "discord_auth_paused")
        self.assertEqual(len(session.calls), 1)

    def test_forbidden_response_is_negative_cached(self):
        session = FakeSession([FakeResponse(403, {"message": "missing access"})])
        guard = self.make_guard(session)
        with self.assertRaises(DiscordGuardError):
            guard.request("POST", "/channels/1/messages", {"content": "same"})
        with self.assertRaises(DiscordGuardError) as raised:
            guard.request("POST", "/channels/1/messages", {"content": "same"})
        self.assertEqual(raised.exception.code, "discord_negative_cache")
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
