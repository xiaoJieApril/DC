import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


LOG = logging.getLogger("discord_guard")


class DiscordGuardError(Exception):
    def __init__(self, status_code, message, retry_after_seconds=0, code="discord_error"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        self.code = code


class DiscordGuard:
    def __init__(self, base_url, token_getter, cache_file=None, session=None, clock=None, sleeper=None):
        self.base_url = base_url.rstrip("/")
        self.token_getter = token_getter
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "DiscordBot (DC-Gra-vt-Dashboard, 1.0)"})
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.cache_ttl = self._env_int("DISCORD_CACHE_TTL_SECONDS", 300)
        self.stale_max = self._env_int("DISCORD_CACHE_STALE_SECONDS", 86400)
        self.cloudflare_initial = self._env_int("DISCORD_CLOUDFLARE_COOLDOWN_SECONDS", 900)
        self.cloudflare_max = self._env_int("DISCORD_CLOUDFLARE_MAX_COOLDOWN_SECONDS", 3600)
        self.cache_file = Path(cache_file) if cache_file else None
        self._lock = threading.RLock()
        self._inflight = {}
        self._memory_cache = {}
        self._persistent_cache = self._load_cache()
        self._circuit_until = 0.0
        self._cloudflare_failures = 0
        self._rate_limit_count = 0
        self._last_success = 0.0

    @staticmethod
    def _env_int(name, default):
        try:
            return max(1, int(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default

    def _load_cache(self):
        if not self.cache_file or not self.cache_file.exists():
            return {}
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        if not self.cache_file:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_file.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self._persistent_cache, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.cache_file)
        except OSError as exc:
            LOG.warning("Could not persist Discord cache: %s", exc)

    def status(self):
        now = self.clock()
        with self._lock:
            remaining = max(0, int(self._circuit_until - now + 0.999))
            return {
                "state": "open" if remaining else "closed",
                "retry_after_seconds": remaining,
                "last_success_at": self._iso(self._last_success) if self._last_success else None,
                "rate_limit_count": self._rate_limit_count,
            }

    @staticmethod
    def _iso(timestamp):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def _cache_entry(self, key, allow_stale=False, ttl=None):
        now = self.clock()
        entry = self._memory_cache.get(key) or self._persistent_cache.get(key)
        if not isinstance(entry, dict) or "data" not in entry:
            return None
        age = max(0, now - float(entry.get("cached_at", 0)))
        fresh_for = self.cache_ttl if ttl is None else ttl
        if age <= fresh_for or (allow_stale and age <= self.stale_max):
            return entry, age > fresh_for
        return None

    def _result(self, entry, stale=False):
        return {
            "data": entry["data"],
            "stale": bool(stale),
            "cached_at": self._iso(float(entry["cached_at"])),
            "retry_after_seconds": self.status()["retry_after_seconds"],
        }

    def get(self, path, cache_key=None, persist=False, ttl=None):
        key = cache_key or f"GET:{path}"
        try:
            with self._lock:
                fresh = self._cache_entry(key, ttl=ttl)
                if fresh:
                    return self._result(*fresh)
                circuit = self.status()
                if circuit["state"] == "open":
                    stale = self._cache_entry(key, allow_stale=persist, ttl=ttl)
                    if stale:
                        return self._result(stale[0], True)
                    raise DiscordGuardError(503, "Discord is temporarily rate limited. Try again later.", circuit["retry_after_seconds"], "discord_circuit_open")
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
                else:
                    owner = False
            if not owner:
                event.wait(timeout=20)
                with self._lock:
                    cached = self._cache_entry(key, allow_stale=persist, ttl=ttl)
                    if cached:
                        return self._result(cached[0], cached[1])
                raise DiscordGuardError(503, "Discord request did not complete.", code="discord_request_failed")
            try:
                data = self._request("GET", path, retry_get=True)
                entry = {"data": data, "cached_at": self.clock()}
                with self._lock:
                    self._memory_cache[key] = entry
                    if persist:
                        self._persistent_cache[key] = entry
                        self._save_cache()
                return self._result(entry)
            except DiscordGuardError:
                with self._lock:
                    stale = self._cache_entry(key, allow_stale=persist, ttl=ttl)
                    if stale:
                        return self._result(stale[0], True)
                raise
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                    event.set()
        finally:
            pass

    def request(self, method, path, payload=None):
        return self._request(method.upper(), path, payload=payload, retry_get=False)

    def _request(self, method, path, payload=None, retry_get=False):
        circuit = self.status()
        if circuit["state"] == "open":
            raise DiscordGuardError(503, "Discord is temporarily rate limited. Try again later.", circuit["retry_after_seconds"], "discord_circuit_open")
        attempts = 2 if method == "GET" and retry_get else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bot {self.token_getter()}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=15,
                )
            except requests.RequestException as exc:
                raise DiscordGuardError(503, "Discord is temporarily unreachable.", code="discord_unreachable") from exc
            if response.status_code < 400:
                with self._lock:
                    self._last_success = self.clock()
                    self._cloudflare_failures = 0
                if not response.text:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise DiscordGuardError(502, "Discord returned an invalid response.", code="discord_invalid_response") from exc
            if response.status_code == 429:
                retry_after, cloudflare, ray_id = self._rate_limit_details(response)
                with self._lock:
                    self._rate_limit_count += 1
                    if cloudflare:
                        self._cloudflare_failures += 1
                        retry_after = min(self.cloudflare_max, self.cloudflare_initial * (2 ** (self._cloudflare_failures - 1)))
                    self._circuit_until = max(self._circuit_until, self.clock() + retry_after)
                LOG.warning("Discord rate limit method=%s route=%s retry_after=%ss cloudflare=%s ray_id=%s", method, path.split("?")[0], retry_after, cloudflare, ray_id or "-")
                if method == "GET" and attempt == 0 and not cloudflare and retry_after > 0 and retry_after <= 5:
                    self.sleeper(retry_after)
                    with self._lock:
                        self._circuit_until = 0
                    continue
                raise DiscordGuardError(429, "Discord is temporarily rate limited. Try again later.", retry_after, "discord_rate_limited")
            message = "Discord rejected the request."
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    message = str(body["message"])
            except ValueError:
                pass
            LOG.warning("Discord error method=%s route=%s status=%s", method, path.split("?")[0], response.status_code)
            raise DiscordGuardError(response.status_code, message)

    def _rate_limit_details(self, response):
        text = response.text or ""
        cloudflare = bool(re.search(r"Error\s*1015|being rate limited|Cloudflare", text, re.I))
        ray = response.headers.get("CF-Ray") or ""
        if not ray:
            match = re.search(r"Ray ID:\s*</?[^>]*>*\s*([a-f0-9]+)|Ray ID:\s*([a-f0-9]+)", text, re.I)
            if match:
                ray = next((value for value in match.groups() if value), "")
        values = [response.headers.get("Retry-After"), response.headers.get("X-RateLimit-Reset-After")]
        try:
            body = response.json()
            if isinstance(body, dict):
                values.insert(0, body.get("retry_after"))
        except ValueError:
            pass
        for value in values:
            try:
                if value is not None:
                    return max(1, int(float(value) + 0.999)), cloudflare, ray
            except (TypeError, ValueError):
                continue
        return self.cloudflare_initial if cloudflare else 60, cloudflare, ray

    def invalidate(self, prefix=None):
        with self._lock:
            keys = list(self._memory_cache)
            for key in keys:
                if prefix is None or key.startswith(prefix):
                    self._memory_cache.pop(key, None)
            persistent_keys = list(self._persistent_cache)
            for key in persistent_keys:
                if prefix is None or key.startswith(prefix):
                    self._persistent_cache.pop(key, None)
            self._save_cache()
