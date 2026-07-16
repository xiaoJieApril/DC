import json
import logging
import os
import re
import threading
import time
from collections import deque
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
    def __init__(self, base_url, token_getter, cache_file=None, session=None, clock=None, sleeper=None, request_permit=None):
        self.base_url = base_url.rstrip("/")
        self.token_getter = token_getter
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "DiscordBot (DC-Gra-vt-Dashboard, 1.0)"})
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.request_permit = request_permit
        self.cache_ttl = self._env_int("DISCORD_CACHE_TTL_SECONDS", 300)
        self.stale_max = self._env_int("DISCORD_CACHE_STALE_SECONDS", 86400)
        self.cloudflare_initial = self._env_int("DISCORD_CLOUDFLARE_COOLDOWN_SECONDS", 900)
        self.cloudflare_max = self._env_int("DISCORD_CLOUDFLARE_MAX_COOLDOWN_SECONDS", 3600)
        self.negative_ttl = self._env_int("DISCORD_NEGATIVE_CACHE_SECONDS", 60)
        self.auth_cooldown = self._env_int("DISCORD_AUTH_COOLDOWN_SECONDS", 300)
        self.upstream_queue_timeout = self._env_int("DISCORD_UPSTREAM_QUEUE_TIMEOUT_SECONDS", 3)
        self.cache_file = Path(cache_file) if cache_file else None
        self._lock = threading.RLock()
        self._inflight = {}
        self._memory_cache = {}
        self._persistent_cache = self._load_cache()
        self._circuit_until = 0.0
        self._cloudflare_failures = 0
        self._rate_limit_count = 0
        self._last_success = 0.0
        self._last_429 = 0.0
        self._route_bucket_ids = {}
        self._buckets = {}
        self._negative_cache = {}
        self._invalid_requests = deque()
        self._auth_until = 0.0
        self._upstream = threading.BoundedSemaphore(self._env_int("DISCORD_MAX_CONCURRENT_REQUESTS", 2))

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
                "active_buckets": sum(1 for item in self._buckets.values() if item.get("until", 0) > now),
                "auth_retry_after_seconds": max(0, int(self._auth_until - now + 0.999)),
                "invalid_requests_10m": self._invalid_request_count(now),
                "last_429_at": self._iso(self._last_429) if self._last_429 else None,
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

    def peek(self, cache_key, allow_stale=True, ttl=None):
        """Read an existing selector cache without making an upstream request."""
        with self._lock:
            cached = self._cache_entry(cache_key, allow_stale=allow_stale, ttl=ttl)
            return self._result(*cached) if cached else None

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
        attempts = 2 if method == "GET" and retry_get else 1
        for attempt in range(attempts):
            self._preflight(method, path, payload)
            if self.request_permit:
                allowed, retry_after = self.request_permit()
                if not allowed:
                    raise DiscordGuardError(429, "Discord requests are being paced to prevent overload.", retry_after, "discord_shared_budget")
            if not self._upstream.acquire(timeout=self.upstream_queue_timeout):
                raise DiscordGuardError(503, "Discord is busy. Please try again shortly.", 1, "discord_upstream_busy")
            try:
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
            finally:
                self._upstream.release()
            self._record_bucket(method, path, response)
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
                scope = self._header(response, "X-RateLimit-Scope").lower()
                is_global = self._header(response, "X-RateLimit-Global").lower() == "true"
                try:
                    body = response.json()
                    is_global = is_global or bool(isinstance(body, dict) and body.get("global"))
                except ValueError:
                    pass
                with self._lock:
                    self._rate_limit_count += 1
                    self._last_429 = self.clock()
                    if scope != "shared":
                        self._record_invalid(self.clock())
                    if cloudflare:
                        self._cloudflare_failures += 1
                        retry_after = min(self.cloudflare_max, self.cloudflare_initial * (2 ** (self._cloudflare_failures - 1)))
                    if cloudflare or is_global or scope == "global":
                        self._circuit_until = max(self._circuit_until, self.clock() + retry_after)
                    else:
                        bucket_key = self._bucket_key(method, path)
                        self._buckets.setdefault(bucket_key, {})["until"] = self.clock() + retry_after
                LOG.warning("Discord rate limit method=%s route=%s retry_after=%ss cloudflare=%s ray_id=%s", method, path.split("?")[0], retry_after, cloudflare, ray_id or "-")
                if method == "GET" and attempt == 0 and not cloudflare and retry_after > 0 and retry_after <= 5:
                    self.sleeper(retry_after)
                    with self._lock:
                        self._circuit_until = 0
                        self._buckets.pop(self._bucket_key(method, path), None)
                    continue
                raise DiscordGuardError(429, "Discord is temporarily rate limited. Try again later.", retry_after, "discord_rate_limited")
            message = "Discord rejected the request."
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    message = str(body["message"])
            except ValueError:
                pass
            if response.status_code in (401, 403, 404):
                now = self.clock()
                with self._lock:
                    self._record_invalid(now)
                    if response.status_code == 401:
                        self._auth_until = max(self._auth_until, now + self.auth_cooldown)
                    else:
                        self._negative_cache[self._negative_key(method, path, payload)] = {
                            "until": now + self.negative_ttl,
                            "status": response.status_code,
                            "message": message,
                        }
            LOG.warning("Discord error method=%s route=%s status=%s", method, path.split("?")[0], response.status_code)
            raise DiscordGuardError(response.status_code, message)

    def _preflight(self, method, path, payload):
        now = self.clock()
        with self._lock:
            if self._auth_until > now:
                retry = max(1, int(self._auth_until - now + 0.999))
                raise DiscordGuardError(503, "Discord authentication is temporarily paused.", retry, "discord_auth_paused")
            circuit = self.status()
            if circuit["state"] == "open":
                raise DiscordGuardError(503, "Discord is temporarily rate limited. Try again later.", circuit["retry_after_seconds"], "discord_circuit_open")
            bucket = self._buckets.get(self._bucket_key(method, path), {})
            if bucket.get("until", 0) > now:
                retry = max(1, int(bucket["until"] - now + 0.999))
                raise DiscordGuardError(429, "This Discord route is cooling down.", retry, "discord_bucket_cooldown")
            negative = self._negative_cache.get(self._negative_key(method, path, payload))
            if negative and negative["until"] > now:
                raise DiscordGuardError(negative["status"], negative["message"], code="discord_negative_cache")

    @staticmethod
    def _header(response, name):
        for key, value in (response.headers or {}).items():
            if str(key).lower() == name.lower():
                return str(value or "")
        return ""

    @staticmethod
    def _route_key(method, path):
        clean = path.split("?", 1)[0]
        return f"{method.upper()}:{clean}"

    def _bucket_key(self, method, path):
        route = self._route_key(method, path)
        return self._route_bucket_ids.get(route, route)

    @staticmethod
    def _negative_key(method, path, payload):
        payload_text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str) if payload is not None else ""
        return f"{method.upper()}:{path}:{payload_text}"

    def _record_bucket(self, method, path, response):
        bucket_id = self._header(response, "X-RateLimit-Bucket")
        remaining = self._header(response, "X-RateLimit-Remaining")
        reset_after = self._header(response, "X-RateLimit-Reset-After")
        if not bucket_id:
            return
        major_match = re.search(r"/(channels|guilds|webhooks)/(\d+)", path.split("?", 1)[0])
        if major_match:
            bucket_id = f"{bucket_id}:{major_match.group(1)}:{major_match.group(2)}"
        route = self._route_key(method, path)
        with self._lock:
            self._route_bucket_ids[route] = bucket_id
            state = self._buckets.setdefault(bucket_id, {})
            try:
                state["remaining"] = int(float(remaining))
            except (TypeError, ValueError):
                pass
            try:
                if state.get("remaining") == 0:
                    state["until"] = self.clock() + max(0.001, float(reset_after))
            except (TypeError, ValueError):
                pass

    def _record_invalid(self, now):
        self._invalid_requests.append(now)
        self._invalid_request_count(now)

    def _invalid_request_count(self, now):
        cutoff = now - 600
        while self._invalid_requests and self._invalid_requests[0] <= cutoff:
            self._invalid_requests.popleft()
        return len(self._invalid_requests)

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
