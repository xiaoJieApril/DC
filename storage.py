import json
import os
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOCK_FILE = BASE_DIR / "config.json.lock"

DEFAULT_CONFIG = {
    "reaction_roles": {},
    "messages": {},
    "onboarding": {},
    "welcome_automation": {},
    "welcome_jobs": [],
    "moderation_cases": {},
    "moderation_settings": {},
    "tickets": {},
    "ticket_settings": {},
    "audit_logs": [],
}


load_dotenv()


def env(name, default=""):
    return os.getenv(name, default).strip()


def storage_name():
    return env("STORAGE_BACKEND", "json").lower() or "json"


def normalize_config(data):
    if not isinstance(data, dict):
        return deepcopy(DEFAULT_CONFIG)
    # Keep every JSON-backed feature section available after config upgrades.
    return {
        "reaction_roles": data.get("reaction_roles", {}) if isinstance(data.get("reaction_roles", {}), dict) else {},
        "messages": data.get("messages", {}) if isinstance(data.get("messages", {}), dict) else {},
        "onboarding": data.get("onboarding", {}) if isinstance(data.get("onboarding", {}), dict) else {},
        "welcome_automation": data.get("welcome_automation", {}) if isinstance(data.get("welcome_automation", {}), dict) else {},
        "welcome_jobs": data.get("welcome_jobs", []) if isinstance(data.get("welcome_jobs", []), list) else [],
        "moderation_cases": data.get("moderation_cases", {}) if isinstance(data.get("moderation_cases", {}), dict) else {},
        "moderation_settings": data.get("moderation_settings", {}) if isinstance(data.get("moderation_settings", {}), dict) else {},
        "tickets": data.get("tickets", {}) if isinstance(data.get("tickets", {}), dict) else {},
        "ticket_settings": data.get("ticket_settings", {}) if isinstance(data.get("ticket_settings", {}), dict) else {},
        "audit_logs": data.get("audit_logs", []) if isinstance(data.get("audit_logs", []), list) else [],
    }


@contextmanager
def config_lock(timeout=10):
    start = time.time()
    fd = None
    while fd is None:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except FileExistsError:
            if time.time() - start > timeout:
                try:
                    LOCK_FILE.unlink()
                except OSError:
                    pass
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def init_db():
    if not CONFIG_FILE.exists():
        save_config(deepcopy(DEFAULT_CONFIG))


def _load_config_unlocked():
    if not CONFIG_FILE.exists():
        return deepcopy(DEFAULT_CONFIG)
    try:
        content = CONFIG_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return deepcopy(DEFAULT_CONFIG)
        return normalize_config(json.loads(content))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_CONFIG)


def load_config():
    return _load_config_unlocked()


def _save_config_unlocked(data):
    config = normalize_config(data)
    temp_file = CONFIG_FILE.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_file.replace(CONFIG_FILE)


def save_config(data):
    with config_lock():
        _save_config_unlocked(data)


def upsert_message(guild_id, message_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("messages", {}).setdefault(str(guild_id), {})[str(message_id)] = dict(payload)
        _save_config_unlocked(config)


def upsert_reaction_role(guild_id, message_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("reaction_roles", {}).setdefault(str(guild_id), {})[str(message_id)] = dict(payload)
        _save_config_unlocked(config)


def upsert_onboarding(guild_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("onboarding", {})[str(guild_id)] = dict(payload)
        _save_config_unlocked(config)


def upsert_welcome_automation(guild_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("welcome_automation", {})[str(guild_id)] = dict(payload)
        _save_config_unlocked(config)


def enqueue_welcome_job(payload):
    job = dict(payload)
    with config_lock():
        config = _load_config_unlocked()
        jobs = config.setdefault("welcome_jobs", [])
        if any(str(item.get("job_id")) == str(job.get("job_id")) for item in jobs):
            return False
        jobs.append(job)
        _save_config_unlocked(config)
    return True


def claim_due_welcome_jobs(now, limit=20, lease_seconds=120):
    claimed = []
    with config_lock():
        config = _load_config_unlocked()
        for job in config.setdefault("welcome_jobs", []):
            if len(claimed) >= limit:
                break
            status = str(job.get("status") or "pending")
            due_at = float(job.get("due_at") or 0)
            lease_until = float(job.get("lease_until") or 0)
            if due_at > now or (status == "processing" and lease_until > now):
                continue
            if status not in ("pending", "processing"):
                continue
            job["status"] = "processing"
            job["lease_until"] = now + lease_seconds
            claimed.append(deepcopy(job))
        if claimed:
            _save_config_unlocked(config)
    return claimed


def finish_welcome_job(job_id):
    with config_lock():
        config = _load_config_unlocked()
        jobs = config.setdefault("welcome_jobs", [])
        remaining = [item for item in jobs if str(item.get("job_id")) != str(job_id)]
        if len(remaining) == len(jobs):
            return False
        config["welcome_jobs"] = remaining
        _save_config_unlocked(config)
    return True


def retry_welcome_job(job_id, due_at, error=""):
    with config_lock():
        config = _load_config_unlocked()
        for job in config.setdefault("welcome_jobs", []):
            if str(job.get("job_id")) != str(job_id):
                continue
            job["status"] = "pending"
            job["due_at"] = float(due_at)
            job["lease_until"] = 0
            job["attempts"] = int(job.get("attempts") or 0) + 1
            job["last_error"] = str(error or "")[:500]
            _save_config_unlocked(config)
            return True
    return False


def cancel_pending_welcome_jobs(guild_id):
    guild_id = str(guild_id)
    with config_lock():
        config = _load_config_unlocked()
        jobs = config.setdefault("welcome_jobs", [])
        remaining = [item for item in jobs if str(item.get("guild_id")) != guild_id]
        removed = len(jobs) - len(remaining)
        if removed:
            config["welcome_jobs"] = remaining
            _save_config_unlocked(config)
        return removed


def set_moderation_settings(guild_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("moderation_settings", {})[str(guild_id)] = dict(payload)
        _save_config_unlocked(config)


def append_moderation_case(guild_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        guild_cases = config.setdefault("moderation_cases", {}).setdefault(str(guild_id), [])
        guild_cases.insert(0, dict(payload))
        del guild_cases[250:]
        _save_config_unlocked(config)
    return payload


def update_moderation_case(guild_id, case_id, updates):
    with config_lock():
        config = _load_config_unlocked()
        guild_cases = config.setdefault("moderation_cases", {}).setdefault(str(guild_id), [])
        for item in guild_cases:
            if str(item.get("case_id")) == str(case_id):
                item.update(dict(updates))
                _save_config_unlocked(config)
                return item
    return None


def set_ticket_settings(guild_id, payload):
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault("ticket_settings", {})[str(guild_id)] = dict(payload)
        _save_config_unlocked(config)


def append_ticket(guild_id, payload):
    # Keep recent ticket intake in the same JSON store used by the dashboard and bot.
    with config_lock():
        config = _load_config_unlocked()
        guild_tickets = config.setdefault("tickets", {}).setdefault(str(guild_id), [])
        guild_tickets.insert(0, dict(payload))
        del guild_tickets[250:]
        _save_config_unlocked(config)
    return payload


def update_ticket(guild_id, ticket_id, updates):
    with config_lock():
        config = _load_config_unlocked()
        guild_tickets = config.setdefault("tickets", {}).setdefault(str(guild_id), [])
        for item in guild_tickets:
            if str(item.get("ticket_id")) == str(ticket_id):
                item.update(dict(updates))
                _save_config_unlocked(config)
                return item
    return None


def delete_record(section, guild_id, message_id):
    section = "messages" if section == "messages" else "reaction_roles"
    with config_lock():
        config = _load_config_unlocked()
        config.setdefault(section, {}).setdefault(str(guild_id), {}).pop(str(message_id), None)
        _save_config_unlocked(config)


def append_audit_log(action, section, guild_id="", message_id="", payload=None, actor="dashboard"):
    entry = {
        "ts": int(time.time()),
        "actor": actor or "dashboard",
        "action": action,
        "section": section,
        "guild_id": str(guild_id or ""),
        "message_id": str(message_id or ""),
        "payload": payload or {},
    }
    with config_lock():
        config = _load_config_unlocked()
        logs = config.setdefault("audit_logs", [])
        logs.insert(0, entry)
        del logs[100:]
        _save_config_unlocked(config)
    return entry
