import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import JSONResponse

import dashboard_api
from request_limits import IdempotencyStore, SharedRateCoordinator, SlidingWindowLimiter


def make_request(method="GET", path="/api/health", headers=None):
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": raw_headers,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    })


class RequestLimitTests(unittest.TestCase):
    def test_shared_cooldown_is_visible_to_second_process_instance(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.sqlite3"
            first = SharedRateCoordinator(path, clock=lambda: now[0])
            second = SharedRateCoordinator(path, clock=lambda: now[0])
            self.assertEqual(first.acquire("bot:1", 3), (True, 0))
            self.assertEqual(second.acquire("bot:1", 3), (False, 3))
            now[0] += 3
            self.assertEqual(second.acquire("bot:1", 3), (True, 0))

    def test_sliding_window_isolated_by_identity(self):
        now = [1000.0]
        limiter = SlidingWindowLimiter(clock=lambda: now[0])
        self.assertEqual(limiter.check("read", "a", 1), (True, 0))
        self.assertEqual(limiter.check("read", "a", 1), (False, 60))
        self.assertEqual(limiter.check("read", "b", 1), (True, 0))

    def test_shared_window_caps_combined_process_requests(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.sqlite3"
            bot_process = SharedRateCoordinator(path, clock=lambda: now[0])
            dashboard_process = SharedRateCoordinator(path, clock=lambda: now[0])
            self.assertEqual(bot_process.check_window("discord", 2, 1), (True, 0))
            self.assertEqual(dashboard_process.check_window("discord", 2, 1), (True, 0))
            self.assertEqual(bot_process.check_window("discord", 2, 1), (False, 1))

    def test_idempotency_store_replays_completed_result(self):
        store = IdempotencyStore(ttl_seconds=30)
        state, _ = store.begin("admin", "request-1")
        self.assertEqual(state, "new")
        store.complete("admin", "request-1", 200, b'{"ok":true}', {"content-type": "application/json"})
        state, entry = store.begin("admin", "request-1")
        self.assertEqual(state, "replay")
        self.assertEqual(entry["body"], b'{"ok":true}')

class DashboardMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_middleware_returns_structured_429(self):
        limiter = SlidingWindowLimiter()
        async def call_next(request):
            return JSONResponse({"ok": True})
        with patch.object(dashboard_api, "DASHBOARD_LIMITER", limiter), \
             patch.object(dashboard_api, "env_int", return_value=1):
            first = await dashboard_api.dashboard_request_protection(make_request(), call_next)
            response = await dashboard_api.dashboard_request_protection(make_request(), call_next)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(response.status_code, 429)
        self.assertIn(b'"code":"dashboard_rate_limited"', response.body)
        self.assertIn("Retry-After", response.headers)

    async def test_dashboard_write_idempotency_replays_response(self):
        calls = []
        async def call_next(request):
            calls.append(request.url.path)
            return JSONResponse({"ok": True})
        headers = {"X-Idempotency-Key": "logout-once"}
        with patch.object(dashboard_api, "DASHBOARD_LIMITER", SlidingWindowLimiter()), \
             patch.object(dashboard_api, "IDEMPOTENCY_STORE", IdempotencyStore()):
            first = await dashboard_api.dashboard_request_protection(make_request("POST", "/api/logout", headers), call_next)
            second = await dashboard_api.dashboard_request_protection(make_request("POST", "/api/logout", headers), call_next)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.headers.get("X-Idempotent-Replay"), "true")
        self.assertEqual(calls, ["/api/logout"])


if __name__ == "__main__":
    unittest.main()
