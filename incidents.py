"""Incident log: turns heartbeat samples into "what happened, when, and why" records.

An incident is one stretch of time when the bot was not healthy:
  no_heartbeat  - nothing was written for more than STALE_AFTER seconds
  disconnected  - the process was alive but the Discord gateway was not ready
  high_latency  - gateway latency stayed at or above HIGH_LATENCY_MS

The scan is idempotent and can run from any process (bot or dashboard). The reason is a
best guess built from evidence the bot recorded around the incident (lifecycle events,
gateway log lines, event-loop lag, memory, feature usage). It is never presented as certain.
"""
import json
import statistics
import time

STALE_AFTER = 45            # three missed 15 s heartbeats
HIGH_LATENCY_MS = 250       # same threshold the dashboard uses for "延迟偏高"
HIGH_LATENCY_MIN_SAMPLES = 2  # one slow sample is a blip, two in a row is an incident
KIND_LABELS = {"no_heartbeat": "无心跳", "disconnected": "断线", "high_latency": "延迟偏高"}
RETENTION_SECONDS = 90 * 86400


# ---------------------------------------------------------------- scan

def scan(db, now=None):
    """Update the incidents table from new heartbeat samples. Safe to call repeatedly."""
    now = float(now if now is not None else time.time())
    db.execute("BEGIN IMMEDIATE")
    cursor = float(_meta(db, "incident_cursor") or 0)
    state = json.loads(_meta(db, "incident_state") or "{}")
    pending = [list(item) for item in state.get("pending_high", [])]

    prev = None
    if cursor:
        prev = db.execute("SELECT * FROM heartbeat_samples WHERE recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1", (cursor,)).fetchone()
    samples = db.execute("SELECT * FROM heartbeat_samples WHERE recorded_at > ? ORDER BY recorded_at ASC LIMIT 20000", (cursor,)).fetchall()
    open_row = db.execute("SELECT id, kind FROM incidents WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    cur = {"id": open_row["id"], "kind": open_row["kind"]} if open_row else None

    def open_incident(kind, started_at, peak=None):
        row = db.execute(
            "INSERT INTO incidents(kind, started_at, peak_latency_ms, updated_at) VALUES(?, ?, ?, ?)",
            (kind, started_at, peak, now),
        )
        return {"id": row.lastrowid, "kind": kind}

    def close_incident(incident, ended_at):
        db.execute("UPDATE incidents SET ended_at = ?, updated_at = ? WHERE id = ?", (ended_at, now, incident["id"]))
        _apply_reason(db, incident["id"], now)

    for sample in samples:
        t = sample["recorded_at"]
        if prev is not None and t - prev["recorded_at"] > STALE_AFTER:
            if cur and cur["kind"] == "no_heartbeat":
                close_incident(cur, t)
            else:
                if cur:
                    close_incident(cur, prev["recorded_at"])
                gap = open_incident("no_heartbeat", prev["recorded_at"])
                close_incident(gap, t)
            cur = None
            pending = []

        latency = sample["latency_ms"]
        if not sample["online"]:
            current_state = "disconnected"
        elif latency is not None and latency >= HIGH_LATENCY_MS:
            current_state = "high_latency"
        else:
            current_state = "ok"

        pending = (pending + [[t, latency]])[-6:] if current_state == "high_latency" else []
        if cur and cur["kind"] != current_state:
            close_incident(cur, t)
            cur = None
        if cur is None and current_state == "disconnected":
            cur = open_incident("disconnected", t)
        elif cur is None and current_state == "high_latency" and len(pending) >= HIGH_LATENCY_MIN_SAMPLES:
            cur = open_incident("high_latency", pending[0][0], max(value or 0 for _, value in pending))
        elif cur and current_state == "high_latency":
            db.execute("UPDATE incidents SET peak_latency_ms = MAX(COALESCE(peak_latency_ms, 0), ?) WHERE id = ?", (latency or 0, cur["id"]))
        prev = sample

    # Live check: the bot may be silent right now, and nobody has written a new sample to close the gap.
    if prev is not None and now - prev["recorded_at"] > STALE_AFTER:
        if cur and cur["kind"] != "no_heartbeat":
            close_incident(cur, prev["recorded_at"])
            cur = None
        if cur is None:
            cur = open_incident("no_heartbeat", prev["recorded_at"])
        pending = []

    if cur:
        _apply_reason(db, cur["id"], now)  # keeps the reason of an ongoing incident fresh
    if prev is not None:
        _set_meta(db, "incident_cursor", str(prev["recorded_at"]))
    _set_meta(db, "incident_state", json.dumps({"pending_high": pending}))
    db.execute("DELETE FROM incidents WHERE ended_at IS NOT NULL AND ended_at < ?", (now - RETENTION_SECONDS,))


def list_incidents(db, limit=20, now=None):
    now = float(now if now is not None else time.time())
    rows = db.execute("SELECT * FROM incidents ORDER BY started_at DESC, id DESC LIMIT ?", (int(limit),)).fetchall()
    items = []
    for row in rows:
        ended = row["ended_at"]
        items.append({
            "id": row["id"],
            "kind": row["kind"],
            "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
            "started_at": row["started_at"],
            "ended_at": ended,
            "ongoing": ended is None,
            "duration_seconds": max(0, round((ended if ended is not None else now) - row["started_at"])),
            "peak_latency_ms": row["peak_latency_ms"],
            "reason_code": row["reason_code"],
            "reason": row["reason"],
            "evidence": json.loads(row["evidence"] or "[]"),
            "planned": bool(row["planned"]),
        })
    return items


# ---------------------------------------------------------------- reasons

def _apply_reason(db, incident_id, now):
    row = db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    code, reason, evidence, planned = classify(db, row, now)
    db.execute(
        "UPDATE incidents SET reason_code = ?, reason = ?, evidence = ?, planned = ?, updated_at = ? WHERE id = ?",
        (code, reason, json.dumps(evidence, ensure_ascii=False), int(planned), now, incident_id),
    )


def classify(db, incident, now):
    """Return (reason_code, reason_text, evidence_lines, planned) for one incident row."""
    start = incident["started_at"]
    end = incident["ended_at"] if incident["ended_at"] is not None else now
    ongoing = incident["ended_at"] is None
    handler = {"no_heartbeat": _classify_gap, "disconnected": _classify_disconnect, "high_latency": _classify_latency}[incident["kind"]]
    return handler(db, incident, start, end, ongoing)


def _events(db, lo, hi, kinds=None):
    rows = db.execute("SELECT recorded_at, kind, detail FROM bot_events WHERE recorded_at BETWEEN ? AND ? ORDER BY recorded_at", (lo, hi)).fetchall()
    return [row for row in rows if kinds is None or row["kind"] in kinds]


def _clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _first_match(lines, keywords):
    hits = [line for line in lines if any(word.lower() in line.lower() for word in keywords)]
    return hits


def _clip(text, size=150):
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _classify_gap(db, incident, start, end, ongoing):
    before = db.execute("SELECT * FROM heartbeat_samples WHERE recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1", (start,)).fetchone()
    after = None if ongoing else db.execute("SELECT * FROM heartbeat_samples WHERE recorded_at >= ? ORDER BY recorded_at ASC LIMIT 1", (end,)).fetchone()
    events = _events(db, start - 90, end + 90)
    evidence = []
    if before is not None and before["memory_mb"] is not None:
        evidence.append(f"最后一次心跳 {_clock(start)}，当时内存 {before['memory_mb']} MB")

    stops = [e for e in events if e["kind"] == "manual_stop"]
    if stops:
        evidence.append(f"{_clock(stops[0]['recorded_at'])} Dashboard 记录了停止操作")
        return "manual_stop", "管理员从 Dashboard 手动停止了 bot", evidence, True
    shutdowns = [e for e in events if e["kind"] == "shutdown"]
    if shutdowns:
        evidence.append(f"{_clock(shutdowns[0]['recorded_at'])} bot 记录了正常关闭：{_clip(shutdowns[0]['detail'])}")
        return "service_restart", "Bot 被正常关闭或重启（服务重启、部署或手动停止）", evidence, True

    if ongoing:
        evidence.append("没有记录到正常关闭，也没有 Dashboard 停止操作")
        return "bot_down", "Bot 已停止发送心跳。可能是进程崩溃、被系统结束（如内存不足）、或主机已离线", evidence, False

    gap_seconds = end - start
    restarted = after is not None and before is not None and (after["recorded_at"] - (after["uptime_seconds"] or 0)) > before["recorded_at"] + 1
    if restarted:
        evidence.append(f"重新上线后运行时长只有 {round(after['uptime_seconds'])} 秒，说明是新进程")
        evidence.append("没有记录到正常关闭事件")
        errors = db.execute(
            "SELECT recorded_at, error_type, message FROM bot_errors WHERE level = 'ERROR' AND recorded_at BETWEEN ? AND ? ORDER BY recorded_at DESC LIMIT 2",
            (start - 120, start + 30),
        ).fetchall()
        for error in errors:
            evidence.append(f"{_clock(error['recorded_at'])} 最后的错误：{_clip((error['error_type'] + ': ' if error['error_type'] else '') + error['message'])}")
        return "process_crashed", "Bot 进程异常退出后被重新启动：没有正常关闭记录，可能是崩溃、内存不足被系统结束，或主机重启", evidence, False

    gap_ms = gap_seconds * 1000
    if after is not None and (after["clock_jump_ms"] or 0) >= gap_ms * 0.6:
        evidence.append(f"系统时钟走了 {round(gap_seconds)} 秒，但进程只运行了约 {round(gap_seconds - (after['clock_jump_ms'] or 0) / 1000)} 秒")
        return "host_suspended", "主机休眠或虚拟机被暂停，进程在这段时间没有运行", evidence, False
    if after is not None and (after["loop_lag_ms"] or 0) >= gap_ms * 0.5:
        evidence.append(f"同一个进程在 {round(gap_seconds)} 秒后恢复，期间事件循环被阻塞约 {round(after['loop_lag_ms'] / 1000)} 秒")
        evidence.extend(_feature_lines(db, start, end))
        evidence.extend(_windows_note(db, start))
        return "event_loop_blocked", "Bot 事件循环被长时间阻塞：处理器里可能有同步的耗时操作（网络请求、文件或数据库、AI 请求）", evidence, False
    evidence.append("进程没有重启，也没有检测到阻塞或休眠")
    return "silent_gap", "进程仍在运行但心跳中断：可能是数据库写入被锁住、磁盘已满，或主机短暂失去响应", evidence, False


def _classify_disconnect(db, incident, start, end, ongoing):
    events = _events(db, start - 90, end + 90)
    logs = [e["detail"] for e in events if e["kind"] == "log"]
    evidence = [f"{_clock(e['recorded_at'])} {_clip(e['detail'])}" for e in events if e["kind"] in ("disconnect", "resume", "reconnected")][:4]

    rules = [
        ("bad_token", ("4004", "Authentication failed", "Improper token"), "Bot Token 无效或已被重置，请在 Developer Portal 重新生成并更新 .env"),
        ("bad_intents", ("4014", "4013", "Disallowed intent", "Invalid intent"), "缺少 Discord 特权 Intent，请到 Developer Portal 开启 Server Members / Message Content"),
        ("rate_limited", ("429", "rate limit", "Cloudflare", "1015"), "被 Discord 或 Cloudflare 限流，连接被暂时拒绝"),
        ("network", ("ClientConnector", "Cannot connect", "getaddrinfo", "Temporary failure", "timed out", "Connection reset", "1006", "ConnectionClosed", "WebSocket closed"), "与 Discord 网关的网络连接中断（主机网络波动，或 Discord 端故障）"),
    ]
    for code, words, text in rules:
        hits = _first_match(logs, words)
        if hits:
            evidence.extend(_clip(hit) for hit in hits[:3])
            return code, text, evidence, False
    if not ongoing and end - start < 120:
        return "gateway_reconnect", "短暂断线后自动重连成功（多见于网络波动或 Discord 网关重启）", evidence, False
    evidence.append("期间没有记录到网关日志，无法判断具体原因")
    return "unknown_disconnect", "Bot 与 Discord 网关断开" + ("，仍未恢复" if ongoing else "") + "，原因不明", evidence, False


def _classify_latency(db, incident, start, end, ongoing):
    samples = db.execute("SELECT * FROM heartbeat_samples WHERE recorded_at BETWEEN ? AND ? ORDER BY recorded_at", (start - 30, end + 15)).fetchall()
    peak = incident["peak_latency_ms"] or 0
    evidence = [f"峰值 {round(peak)} ms，持续约 {max(1, round(end - start))} 秒"]
    lag = max((s["loop_lag_ms"] or 0 for s in samples), default=0)
    jump = max((s["clock_jump_ms"] or 0 for s in samples), default=0)
    memory = [s["memory_mb"] for s in samples if s["memory_mb"] is not None]
    baseline_rows = db.execute(
        "SELECT memory_mb FROM heartbeat_samples WHERE recorded_at BETWEEN ? AND ? AND memory_mb IS NOT NULL", (start - 3600, start - 60)
    ).fetchall()
    baseline = statistics.median(r["memory_mb"] for r in baseline_rows) if baseline_rows else None
    logs = [e["detail"] for e in _events(db, start - 90, end + 60, {"log", "disconnect", "resume"})]

    if jump >= 5000:
        evidence.append(f"系统时钟比进程多走了 {round(jump / 1000)} 秒")
        return "host_suspended", "主机休眠或虚拟机被暂停，造成延迟飙升", evidence, False
    if lag >= 2000:
        evidence.append(f"事件循环被阻塞约 {round(lag / 1000, 1)} 秒")
        evidence.extend(_feature_lines(db, start - 30, end))
        evidence.extend(_windows_note(db, start))
        return "event_loop_blocked", "Bot 事件循环被阻塞：处理器里可能有同步的耗时操作（网络请求、文件或数据库、AI 请求）", evidence, False
    if baseline and memory and max(memory) >= baseline * 1.5 and max(memory) - baseline >= 80:
        evidence.append(f"内存从约 {round(baseline)} MB 升到 {round(max(memory))} MB")
        return "memory_spike", "内存使用突然升高，主机可能在换页或频繁回收内存", evidence, False
    hits = _first_match(logs, ("429", "rate limit", "Cloudflare"))
    if hits:
        evidence.extend(_clip(hit) for hit in hits[:3])
        return "rate_limited", "同一时段遇到 Discord 限流，请求排队拖慢了响应", evidence, False
    hits = _first_match(logs, ("reconnect", "RESUMED", "WebSocket closed", "disconnect"))
    if hits:
        evidence.extend(_clip(hit) for hit in hits[:3])
        return "gateway_reconnect", "网关连接不稳定，期间发生了重连", evidence, False
    evidence.append("没有发现阻塞、内存升高、限流或重连的迹象")
    return "network_latency", "更可能是主机到 Discord 之间的网络延迟，或 Discord 端的波动（可查看 discordstatus.com）", evidence, False


def _feature_lines(db, lo, hi):
    rows = db.execute(
        "SELECT feature, COUNT(*) AS n FROM feature_usage WHERE recorded_at BETWEEN ? AND ? GROUP BY feature ORDER BY n DESC LIMIT 4", (lo, hi)
    ).fetchall()
    if not rows:
        return ["同一时段没有记录到功能操作"]
    return ["同一时段的功能操作：" + "、".join(f"{r['feature']} ×{r['n']}" for r in rows)]


def _windows_note(db, start):
    row = db.execute("SELECT detail FROM bot_events WHERE kind = 'startup' AND recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1", (start,)).fetchone()
    if row and "win32" in (row["detail"] or ""):
        return ["注意：Windows 的计时方式无法区分「阻塞」和「休眠」，这两种情况看起来一样"]
    return []


# ---------------------------------------------------------------- meta

def _meta(db, key):
    row = db.execute("SELECT value FROM observability_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(db, key, value):
    db.execute(
        "INSERT INTO observability_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value)
    )
