import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "dc_gra_vt_bot.db"

DEFAULT_CONFIG = {
    "reaction_roles": {},
    "messages": {},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                guild_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS reaction_roles (
                guild_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, message_id)
            );
            """
        )
    migrate_config_json()


def _read_config_json():
    if not CONFIG_FILE.exists():
        return deepcopy(DEFAULT_CONFIG)
    try:
        content = CONFIG_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return deepcopy(DEFAULT_CONFIG)
        data = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_CONFIG)

    return {
        "reaction_roles": data.get("reaction_roles", {}) if isinstance(data.get("reaction_roles", {}), dict) else {},
        "messages": data.get("messages", {}) if isinstance(data.get("messages", {}), dict) else {},
    }


def migrate_config_json():
    data = _read_config_json()
    now = utc_now()
    with connect() as conn:
        for guild_id, messages in data.get("messages", {}).items():
            if not isinstance(messages, dict):
                continue
            for message_id, payload in messages.items():
                if not isinstance(payload, dict):
                    continue
                channel_id = str(payload.get("channel_id", ""))
                if not channel_id:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                    (guild_id, message_id, channel_id, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(guild_id), str(message_id), channel_id, json.dumps(payload, ensure_ascii=False), now, now),
                )

        for guild_id, messages in data.get("reaction_roles", {}).items():
            if not isinstance(messages, dict):
                continue
            for message_id, payload in messages.items():
                if not isinstance(payload, dict):
                    continue
                channel_id = str(payload.get("channel_id", ""))
                if not channel_id:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reaction_roles
                    (guild_id, message_id, channel_id, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(guild_id), str(message_id), channel_id, json.dumps(payload, ensure_ascii=False), now, now),
                )


def load_config():
    init_db()
    config = deepcopy(DEFAULT_CONFIG)
    with connect() as conn:
        for row in conn.execute("SELECT guild_id, message_id, payload FROM messages ORDER BY updated_at DESC"):
            config["messages"].setdefault(row["guild_id"], {})[row["message_id"]] = json.loads(row["payload"])
        for row in conn.execute("SELECT guild_id, message_id, payload FROM reaction_roles ORDER BY updated_at DESC"):
            config["reaction_roles"].setdefault(row["guild_id"], {})[row["message_id"]] = json.loads(row["payload"])
    return config


def save_config(data):
    init_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM reaction_roles")
        for guild_id, messages in data.get("messages", {}).items():
            for message_id, payload in messages.items():
                payload = dict(payload)
                channel_id = str(payload.get("channel_id", ""))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO messages
                    (guild_id, message_id, channel_id, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(guild_id), str(message_id), channel_id, json.dumps(payload, ensure_ascii=False), now, now),
                )
        for guild_id, messages in data.get("reaction_roles", {}).items():
            for message_id, payload in messages.items():
                payload = dict(payload)
                channel_id = str(payload.get("channel_id", ""))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO reaction_roles
                    (guild_id, message_id, channel_id, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(guild_id), str(message_id), channel_id, json.dumps(payload, ensure_ascii=False), now, now),
                )
    export_config_json()


def export_config_json():
    data = load_config_without_migration()
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_config_without_migration():
    config = deepcopy(DEFAULT_CONFIG)
    with connect() as conn:
        for row in conn.execute("SELECT guild_id, message_id, payload FROM messages ORDER BY updated_at DESC"):
            config["messages"].setdefault(row["guild_id"], {})[row["message_id"]] = json.loads(row["payload"])
        for row in conn.execute("SELECT guild_id, message_id, payload FROM reaction_roles ORDER BY updated_at DESC"):
            config["reaction_roles"].setdefault(row["guild_id"], {})[row["message_id"]] = json.loads(row["payload"])
    return config


def upsert_message(guild_id, message_id, payload):
    config = load_config()
    config.setdefault("messages", {}).setdefault(str(guild_id), {})[str(message_id)] = dict(payload)
    save_config(config)


def upsert_reaction_role(guild_id, message_id, payload):
    config = load_config()
    config.setdefault("reaction_roles", {}).setdefault(str(guild_id), {})[str(message_id)] = dict(payload)
    save_config(config)


def delete_record(section, guild_id, message_id):
    table = "messages" if section == "messages" else "reaction_roles"
    init_db()
    with connect() as conn:
        conn.execute(
            f"DELETE FROM {table} WHERE guild_id = ? AND message_id = ?",
            (str(guild_id), str(message_id)),
        )
    export_config_json()
