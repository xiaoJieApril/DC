import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path


def env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class SharedRateCoordinator:
    """Small SQLite-backed cooldown/metrics store shared by bot and dashboard."""

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cooldowns (key TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rate_metrics (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rate_windows (key TEXT PRIMARY KEY, window_start REAL NOT NULL, count INTEGER NOT NULL)"
            )

    def acquire(self, key, cooldown_seconds):
        now = self.clock()
        expires_at = now + max(0.1, float(cooldown_seconds))
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT expires_at FROM cooldowns WHERE key = ?", (str(key),)).fetchone()
            if row and float(row[0]) > now:
                return False, max(1, int(float(row[0]) - now + 0.999))
            connection.execute(
                "INSERT INTO cooldowns(key, expires_at) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires_at = excluded.expires_at",
                (str(key), expires_at),
            )
            connection.execute("DELETE FROM cooldowns WHERE expires_at <= ?", (now - 60,))
        return True, 0

    def increment(self, name, amount=1):
        now = self.clock()
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO rate_metrics(name, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value, updated_at = excluded.updated_at",
                (str(name), int(amount), now),
            )

    def check_window(self, key, limit, window_seconds=1):
        now = self.clock()
        key = str(key)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT window_start, count FROM rate_windows WHERE key = ?", (key,)
            ).fetchone()
            if not row or now - float(row[0]) >= window_seconds:
                connection.execute(
                    "INSERT INTO rate_windows(key, window_start, count) VALUES(?, ?, 1) "
                    "ON CONFLICT(key) DO UPDATE SET window_start = excluded.window_start, count = 1",
                    (key, now),
                )
                return True, 0
            if int(row[1]) >= int(limit):
                return False, max(1, int(float(row[0]) + window_seconds - now + 0.999))
            connection.execute("UPDATE rate_windows SET count = count + 1 WHERE key = ?", (key,))
            return True, 0

    def status(self):
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT name, value FROM rate_metrics").fetchall()
        return {name: value for name, value in rows}


class SlidingWindowLimiter:
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self._events = defaultdict(deque)
        self._lock = threading.RLock()
        self.rejected = 0

    def check(self, scope, identity, limit, window_seconds=60):
        now = self.clock()
        key = (str(scope), str(identity))
        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                self.rejected += 1
                return False, max(1, int(events[0] + window_seconds - now + 0.999))
            events.append(now)
            return True, 0


class IdempotencyStore:
    def __init__(self, ttl_seconds=300, clock=None):
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time
        self._entries = {}
        self._lock = threading.RLock()

    def begin(self, identity, key):
        now = self.clock()
        compound = (str(identity), str(key))
        with self._lock:
            self._entries = {item: value for item, value in self._entries.items() if value["expires_at"] > now}
            entry = self._entries.get(compound)
            if entry:
                return "replay" if entry.get("complete") else "inflight", entry
            entry = {"complete": False, "expires_at": now + self.ttl_seconds}
            self._entries[compound] = entry
            return "new", entry

    def complete(self, identity, key, status_code, body, headers=None):
        compound = (str(identity), str(key))
        with self._lock:
            entry = self._entries.get(compound)
            if entry is not None:
                entry.update({
                    "complete": True,
                    "status_code": int(status_code),
                    "body": bytes(body),
                    "headers": dict(headers or {}),
                })

    def discard(self, identity, key):
        with self._lock:
            self._entries.pop((str(identity), str(key)), None)
