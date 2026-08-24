import re
import time
import uuid


CASE_ACTIVE_STATUSES = {"open", "escalated"}
CASE_ARCHIVE_STATUSES = {"resolved", "rejected", "accepted"}
TICKET_ACTIVE_STATUSES = {"open", "escalated"}
TICKET_ARCHIVE_STATUSES = {"resolved", "rejected"}
VALID_SEVERITIES = {"normal", "serious", "red_line"}
VALID_ACTIONS = {"warning", "probation", "timeout", "remove_role", "note"}
MESSAGE_LINK_RE = re.compile(
    r"^https?://(?:canary\.|ptb\.)?(?:discord(?:app)?\.com)/channels/(\d+)/(\d+)/(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)


def normalize_moderation_rule(value):
    value = value if isinstance(value, dict) else {}
    try:
        timeout_minutes = max(0, int(value.get("timeout_minutes") or 0))
    except (TypeError, ValueError):
        timeout_minutes = 0
    return {
        "rule_id": str(value.get("rule_id") or uuid.uuid4().hex),
        "number": str(value.get("number") or "").strip()[:40],
        "name": str(value.get("name") or "").strip()[:120],
        "reason": str(value.get("reason") or "").strip()[:1000],
        "severity": str(value.get("severity") or "normal") if str(value.get("severity") or "normal") in VALID_SEVERITIES else "normal",
        "action": str(value.get("action") or "warning") if str(value.get("action") or "warning") in VALID_ACTIONS else "warning",
        "timeout_minutes": timeout_minutes,
        "remove_role_id": str(value.get("remove_role_id") or ""),
        "enabled": bool(value.get("enabled", True)),
    }


def normalize_moderation_rules(values):
    return [normalize_moderation_rule(item) for item in (values or []) if isinstance(item, dict)]


def validate_moderation_rules(rules):
    enabled = [item for item in rules if item.get("enabled")]
    if len(enabled) > 25:
        raise ValueError("A maximum of 25 rules can be enabled for Discord")
    numbers = set()
    ids = set()
    for item in rules:
        if not item["number"] or not item["name"] or not item["reason"]:
            raise ValueError("Every rule needs a number, name, and reason")
        key = item["number"].casefold()
        if key in numbers:
            raise ValueError(f"Rule number '{item['number']}' is duplicated")
        if item["rule_id"] in ids:
            raise ValueError("Rule IDs must be unique")
        numbers.add(key)
        ids.add(item["rule_id"])
        if item["action"] == "timeout" and item["timeout_minutes"] <= 0:
            raise ValueError(f"Rule {item['number']} needs timeout minutes")
        if item["action"] == "remove_role" and not item["remove_role_id"].isdigit():
            raise ValueError(f"Rule {item['number']} needs a role to remove")


def parse_discord_message_url(value):
    match = MESSAGE_LINK_RE.match(str(value or "").strip())
    if not match:
        raise ValueError("Paste a valid Discord message link")
    return {"guild_id": match.group(1), "channel_id": match.group(2), "message_id": match.group(3)}


def discord_message_url(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def evidence_snapshot_from_api(message, guild_id, channel_id, captured_at=None):
    author = message.get("author") or {}
    attachments = []
    for item in message.get("attachments") or []:
        attachments.append({
            "id": str(item.get("id") or ""),
            "filename": str(item.get("filename") or ""),
            "url": str(item.get("url") or ""),
            "content_type": str(item.get("content_type") or ""),
            "size": int(item.get("size") or 0),
        })
    message_id = str(message.get("id") or "")
    return {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "message_id": message_id,
        "jump_url": discord_message_url(guild_id, channel_id, message_id),
        "author_id": str(author.get("id") or ""),
        "author_display": str(author.get("global_name") or author.get("username") or author.get("id") or ""),
        "content": str(message.get("content") or "")[:4000],
        "created_at": str(message.get("timestamp") or ""),
        "edited_at": str(message.get("edited_timestamp") or ""),
        "attachments": attachments[:10],
        "captured_at": int(captured_at if captured_at is not None else time.time()),
    }


def evidence_snapshot_from_message(message, captured_at=None):
    data = {
        "id": str(message.id),
        "content": str(message.content or ""),
        "timestamp": message.created_at.isoformat() if message.created_at else "",
        "edited_timestamp": message.edited_at.isoformat() if message.edited_at else "",
        "author": {
            "id": str(message.author.id),
            "username": str(message.author),
            "global_name": getattr(message.author, "display_name", ""),
        },
        "attachments": [
            {
                "id": str(item.id),
                "filename": item.filename,
                "url": item.url,
                "content_type": item.content_type or "",
                "size": item.size,
            }
            for item in message.attachments
        ],
    }
    return evidence_snapshot_from_api(data, message.guild.id, message.channel.id, captured_at)


def status_group(status, kind="case"):
    value = str(status or "open")
    archive = CASE_ARCHIVE_STATUSES if kind == "case" else TICKET_ARCHIVE_STATUSES
    return "archive" if value in archive else "active"


def filter_status_view(rows, view, kind="case"):
    if view == "all":
        return list(rows)
    target = "archive" if view == "archive" else "active"
    return [item for item in rows if status_group(item.get("status"), kind) == target]


def status_counts(rows, kind="case"):
    counts = {"active": 0, "archive": 0}
    for item in rows:
        counts[status_group(item.get("status"), kind)] += 1
    return counts


def status_update(item, new_status, actor, notes="", now=None, kind="case"):
    timestamp = int(now if now is not None else time.time())
    old_status = str(item.get("status") or "open")
    history = list(item.get("status_history") or [])
    history.append({"from": old_status, "to": new_status, "ts": timestamp, "actor": actor, "notes": str(notes or "")})
    update = {"status": new_status, "status_history": history, "updated_ts": timestamp, "updated_by": actor}
    if status_group(new_status, kind) == "archive":
        update.update({"resolution_notes": str(notes or ""), "resolved_ts": timestamp, "resolved_by": actor})
    else:
        update.update({"resolution_notes": "", "resolved_ts": 0, "resolved_by": ""})
    return update
