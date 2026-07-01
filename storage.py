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
    return {
        "reaction_roles": data.get("reaction_roles", {}) if isinstance(data.get("reaction_roles", {}), dict) else {},
        "messages": data.get("messages", {}) if isinstance(data.get("messages", {}), dict) else {},
        "onboarding": data.get("onboarding", {}) if isinstance(data.get("onboarding", {}), dict) else {},
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
