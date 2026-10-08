"""Shared SQLite-backed bot telemetry for the bot and FastAPI processes."""

import logging
import queue
import re
import sqlite3
import threading
import time
import traceback as traceback_module
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "data" / "observability.sqlite3"
FEATURES = {
    "system": {"name": "Bot 运行控制", "trigger": "Dashboard"},
    "messages": {"name": "发送消息", "trigger": "/sendmessage · Dashboard"},
    "roles": {"name": "Reaction Roles", "trigger": "/reactionrole · /giverole · /removerole"},
    "onboarding": {"name": "New Member Rules", "trigger": "Discord 选择菜单 · Dashboard"},
    "welcome": {"name": "Welcome Automation", "trigger": "新成员加入 · Dashboard"},
    "moderation": {"name": "Moderation", "trigger": "/warn · /timeout · /case · Dashboard"},
    "tickets": {"name": "Tickets", "trigger": "Discord Ticket 按钮 · Dashboard"},
}
ERROR_STATUSES = {"open", "in_progress", "resolved"}


def _redact(value):
    text = str(value or "")
    text = re.sub(r"(?i)(DISCORD_TOKEN|BOT_TOKEN|DASHBOARD_TOKEN|ADMIN_PASSWORD|SESSION_SECRET)(\s*[=:]\s*)[^\s'\"]+", r"\1\2[redacted]", text)
    text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|bot)\s+)[^\s'\"]+", r"\1[redacted]", text)
    return text[:40000]


def error_hint(error_type, message):
    kind = str(error_type or "").lower()
    text = str(message or "").lower()
    if "forbidden" in kind or "missing permissions" in text or "403" in text:
        return "Bot 缺少所需权限，或目标频道/身份组的权限覆盖阻止了操作。检查 Discord 角色权限与频道权限。"
    if "notfound" in kind or "404" in text:
        return "目标频道、消息、成员或身份组可能已删除。确认 Discord ID 与配置仍有效。"
    if "timeout" in kind or "timed out" in text or "temporarily unavailable" in text:
        return "网络或 Discord API 暂时没有响应。先检查连接状态；恢复后重试操作。"
    if "ratelimit" in kind or "429" in text:
        return "Discord API 请求触发速率限制。减少重复操作并等待冷却时间结束。"
    if "validation" in kind or "valueerror" in kind:
        return "输入或保存的配置不符合要求。检查必填内容、Discord ID 和功能设置。"
    if "typeerror" in kind and ("none" in text or "mapping" in text):
        return "程序尝试展开一个空结果。检查调用函数是否在所有正常执行路径都返回了字典或其他预期值；展开 traceback 查看出错函数。"
    return "查看 traceback 中最早出现的项目代码位置，确认相关配置与 Discord API 响应。"


class ObservabilityStore:
    def __init__(self, path=DATABASE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS heartbeat_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                online INTEGER NOT NULL,
                latency_ms REAL,
                guild_count INTEGER NOT NULL DEFAULT 0,
                uptime_seconds REAL NOT NULL DEFAULT 0,
                memory_mb REAL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS heartbeat_recorded_at ON heartbeat_samples(recorded_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS feature_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                feature TEXT NOT NULL,
                trigger_name TEXT NOT NULL DEFAULT '',
                succeeded INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS feature_usage_time ON feature_usage(feature, recorded_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS bot_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                level TEXT NOT NULL,
                feature TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                traceback TEXT NOT NULL DEFAULT '',
                hint TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                updated_at REAL NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS bot_errors_time ON bot_errors(recorded_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS bot_errors_filter ON bot_errors(level, status, id DESC)")

    def record_heartbeat(self, online, latency_ms=None, guild_count=0, uptime_seconds=0, memory_mb=None, recorded_at=None):
        now = float(recorded_at if recorded_at is not None else time.time())
        with self._connect() as db:
            db.execute(
                "INSERT INTO heartbeat_samples(recorded_at, online, latency_ms, guild_count, uptime_seconds, memory_mb) VALUES(?, ?, ?, ?, ?, ?)",
                (now, int(bool(online)), latency_ms, int(guild_count or 0), float(uptime_seconds or 0), memory_mb),
            )
            db.execute("DELETE FROM heartbeat_samples WHERE recorded_at < ?", (now - 7 * 86400,))

    def record_feature(self, feature, trigger_name="", succeeded=True, recorded_at=None):
        feature = str(feature or "other")[:80]
        now = float(recorded_at if recorded_at is not None else time.time())
        with self._connect() as db:
            db.execute(
                "INSERT INTO feature_usage(recorded_at, feature, trigger_name, succeeded) VALUES(?, ?, ?, ?)",
                (now, feature, str(trigger_name or "")[:160], int(bool(succeeded))),
            )
            db.execute("DELETE FROM feature_usage WHERE recorded_at < ?", (now - 90 * 86400,))

    def record_error(self, level, feature="", command="", error=None, message=None, trace=None, recorded_at=None):
        now = float(recorded_at if recorded_at is not None else time.time())
        error_type = type(error).__name__ if error is not None else ""
        error_message = message if message is not None else (str(error) if error is not None else "Unknown error")
        if trace is None and error is not None:
            trace = "".join(traceback_module.format_exception(type(error), error, error.__traceback__))
        clean_level = str(level or "ERROR").upper()
        if clean_level == "WARNING":
            clean_level = "WARN"
        safe_message = _redact(error_message)
        safe_trace = _redact(trace or "")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO bot_errors(recorded_at, level, feature, command, error_type, message, traceback, hint, status, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (now, clean_level, str(feature or "")[:80], str(command or "")[:160], error_type[:120], safe_message, safe_trace, error_hint(error_type, safe_message), now),
            )
            db.execute("DELETE FROM bot_errors WHERE recorded_at < ?", (now - 90 * 86400,))
            return int(cursor.lastrowid)

    def update_error_status(self, error_id, status):
        if status not in ERROR_STATUSES:
            return None
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE bot_errors SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, int(error_id)),
            )
            if cursor.rowcount != 1:
                return None
            row = db.execute("SELECT * FROM bot_errors WHERE id = ?", (int(error_id),)).fetchone()
            return dict(row) if row else None

    def list_errors(self, level="", status="", limit=50, cursor=None):
        clauses, values = [], []
        if level:
            clauses.append("level = ?")
            values.append(level)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if cursor:
            clauses.append("id < ?")
            values.append(int(cursor))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM bot_errors{where} ORDER BY id DESC LIMIT ?", (*values, int(limit) + 1)).fetchall()
            unresolved = db.execute("SELECT COUNT(*) FROM bot_errors WHERE status = 'open'").fetchone()[0]
        has_more = len(rows) > int(limit)
        rows = rows[:int(limit)]
        return {
            "items": [dict(row) for row in rows],
            "unresolved_count": int(unresolved),
            "next_cursor": rows[-1]["id"] if has_more and rows else None,
        }

    def summary(self, now=None):
        current = float(now if now is not None else time.time())
        with self._connect() as db:
            latest = db.execute("SELECT * FROM heartbeat_samples ORDER BY recorded_at DESC LIMIT 1").fetchone()
            samples = db.execute(
                "SELECT recorded_at, online, latency_ms FROM heartbeat_samples WHERE recorded_at >= ? ORDER BY recorded_at ASC",
                (current - 3600,),
            ).fetchall()
            usage = db.execute(
                "SELECT feature, COUNT(*) AS total, SUM(succeeded) AS succeeded, MAX(recorded_at) AS last_used FROM feature_usage WHERE recorded_at >= ? GROUP BY feature",
                (current - 86400,),
            ).fetchall()
            latest_triggers = {}
            for row in db.execute(
                "SELECT feature, trigger_name FROM feature_usage WHERE recorded_at >= ? ORDER BY recorded_at DESC",
                (current - 86400,),
            ).fetchall():
                latest_triggers.setdefault(row["feature"], row["trigger_name"])
            unresolved = db.execute("SELECT COUNT(*) FROM bot_errors WHERE status = 'open'").fetchone()[0]

        latest_data = dict(latest) if latest else None
        fresh = bool(latest_data and latest_data["recorded_at"] >= current - 45)
        online = bool(fresh and latest_data["online"])
        if not online:
            current_status = "disconnected"
        elif latest_data["latency_ms"] is not None and latest_data["latency_ms"] >= 250:
            current_status = "high_latency"
        else:
            current_status = "normal"

        by_minute = {}
        for sample in samples:
            key = int(sample["recorded_at"] // 60)
            bucket = by_minute.setdefault(key, {"offline": False, "max_latency": 0.0, "has_latency": False})
            if not sample["online"]:
                bucket["offline"] = True
            if sample["latency_ms"] is not None:
                bucket["has_latency"] = True
                bucket["max_latency"] = max(bucket["max_latency"], float(sample["latency_ms"]))
        timeline = []
        start_minute = int(current // 60) - 59
        for offset in range(60):
            minute = start_minute + offset
            bucket = by_minute.get(minute)
            if not bucket:
                status = "unknown"
            elif bucket["offline"]:
                status = "disconnected"
            elif bucket["has_latency"] and bucket["max_latency"] >= 250:
                status = "high_latency"
            else:
                status = "normal"
            timeline.append({"minute": minute * 60, "status": status, "latency_ms": bucket["max_latency"] if bucket and bucket["has_latency"] else None})

        aggregates = {row["feature"]: row for row in usage}
        features = []
        for key, meta in FEATURES.items():
            row = aggregates.get(key)
            total = int(row["total"]) if row else 0
            ok = int(row["succeeded"] or 0) if row else 0
            features.append({
                "key": key,
                "name": meta["name"],
                "trigger": latest_triggers.get(key) or meta["trigger"],
                "total": total,
                "success_rate": round(ok / total * 100, 1) if total else None,
                "last_used": row["last_used"] if row else None,
            })
        return {
            "status": current_status,
            "online": online,
            "gateway_latency_ms": latest_data["latency_ms"] if online else None,
            "uptime_seconds": latest_data["uptime_seconds"] if latest_data and fresh else None,
            "guild_count": latest_data["guild_count"] if latest_data and fresh else 0,
            "memory_mb": latest_data["memory_mb"] if online else None,
            "sampled_at": latest_data["recorded_at"] if latest_data else None,
            "timeline": timeline,
            "features": [feature for feature in features if feature["key"] != "system"],
            "unresolved_errors": int(unresolved),
        }


observability = ObservabilityStore()


def feature_for_trigger(trigger):
    name = str(trigger or "").lower()
    if "sendmessage" in name or "/api/messages" in name:
        return "messages"
    if "reactionrole" in name or "role_" in name or "giverole" in name or "removerole" in name or "/api/reaction-roles" in name:
        return "roles"
    if "onboarding" in name:
        return "onboarding"
    if "welcome" in name:
        return "welcome"
    if any(value in name for value in ("warn", "probation", "timeout", "history", "case", "moderation")) or "/api/moderation" in name:
        return "moderation"
    if "ticket" in name or "/api/tickets" in name:
        return "tickets"
    if "/api/saved" in name:
        return "messages"
    return "other"


class ObservabilityLogHandler(logging.Handler):
    """Move warning/error records to SQLite from a background thread."""

    def __init__(self, store):
        super().__init__(level=logging.WARNING)
        self.store = store
        self.records = queue.Queue(maxsize=2000)
        self.worker = threading.Thread(target=self._write_records, name="observability-writer", daemon=True)
        self.worker.start()

    def emit(self, record):
        try:
            self.records.put_nowait(record)
        except queue.Full:
            # Logging must never stall the bot event loop when telemetry is busy.
            pass

    def _write_records(self):
        while True:
            record = self.records.get()
            try:
                error = record.exc_info[1] if record.exc_info else None
                trace = "".join(traceback_module.format_exception(*record.exc_info)) if record.exc_info else ""
                trigger = getattr(record, "command", "") or record.name
                self.store.record_error(
                    "ERROR" if record.levelno >= logging.ERROR else "WARN",
                    getattr(record, "feature", "") or feature_for_trigger(trigger),
                    trigger,
                    error,
                    message=record.getMessage(),
                    trace=trace,
                )
            except Exception:
                # Telemetry persistence failures should not recursively log.
                pass
            finally:
                self.records.task_done()
