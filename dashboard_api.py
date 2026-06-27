import os
import secrets
import hmac
import hashlib
import base64
import time
import urllib.parse
import re
import subprocess
import sys
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from storage import append_audit_log, delete_record, init_db, load_config, save_config, storage_name, upsert_message, upsert_reaction_role


load_dotenv()
init_db()

DISCORD_API = "https://discord.com/api/v10"
COLOR_MAP = {
    "Blurple": 0x5865F2,
    "Green": 0x57F287,
    "Red": 0xED4245,
    "Yellow": 0xFEE75C,
    "White": 0xFFFFFF,
}
DEFAULT_RR_DESCRIPTION = "使用下拉式選單來更改名字顏色"
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
BOT_LOG_PATH = LOG_DIR / "dashboard_bot.log"
BOT_PID_PATH = LOG_DIR / "bot.pid"
BOT_LOCK = threading.Lock()
BOT_PROCESS = None
BOT_STARTED_AT = 0.0


def env(name, default=""):
    return os.getenv(name, default).strip()


def allowed_origins():
    origins = {
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    public_origin = env("PUBLIC_FRONTEND_ORIGIN")
    if public_origin:
        origins.add(public_origin.rstrip("/"))
    return sorted(origins)


app = FastAPI(title="DC-Gra-vt-bot Dashboard API")
app.add_middleware(
    SessionMiddleware,
    secret_key=env("SESSION_SECRET") or secrets.token_urlsafe(32),
    same_site="lax",
    https_only=False,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginPayload(BaseModel):
    username: str
    password: str


class MessagePayload(BaseModel):
    channel_id: str
    content: str
    use_embed: bool = True
    title: str = "Announcement"
    color: str = "Blurple"
    footer: str = ""


class MappingPayload(BaseModel):
    emoji: str
    role_id: str
    role_name: str = ""


class ReactionRolePayload(BaseModel):
    channel_id: str
    panel_name: str = ""
    title: str = ""
    description: str = DEFAULT_RR_DESCRIPTION
    mode: str = "dropdown"
    use_embed: bool = True
    include_role_mentions: bool = True
    color: str = "Blurple"
    mappings: list[MappingPayload]


class SavedUpdatePayload(BaseModel):
    section: str
    guild_id: str
    message_id: str
    payload: dict


def bot_returncode():
    global BOT_PROCESS
    if BOT_PROCESS is None:
        return None
    return BOT_PROCESS.poll()


def bot_control_mode():
    return env("BOT_CONTROL_MODE", "process").lower()


def dashboard_bot_control_enabled():
    return bot_control_mode() not in ("systemd", "disabled", "off", "false", "0")


def systemd_bot_status():
    service_name = env("SYSTEMD_BOT_SERVICE", "dc-gra-vt-bot")
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            cwd=str(BASE_DIR),
            timeout=4,
            check=False,
        )
        pid = subprocess.run(
            ["systemctl", "show", service_name, "--property=MainPID", "--value"],
            cwd=str(BASE_DIR),
            timeout=4,
            check=False,
            capture_output=True,
            text=True,
        )
        raw_pid = (pid.stdout or "").strip()
        return {
            "running": active.returncode == 0,
            "pid": int(raw_pid) if raw_pid.isdigit() and raw_pid != "0" else None,
            "service": service_name,
            "status_available": True,
        }
    except Exception as exc:
        return {
            "running": None,
            "pid": None,
            "service": service_name,
            "status_available": False,
            "status_error": str(exc),
        }


def tail_text(path, max_lines=80):
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-max_lines:])
    except OSError:
        return ""


def bot_status_payload():
    returncode = bot_returncode()
    running = BOT_PROCESS is not None and returncode is None
    control_enabled = dashboard_bot_control_enabled()
    systemd_status = systemd_bot_status() if bot_control_mode() == "systemd" else {}
    return {
        "running": systemd_status.get("running", running),
        "pid": systemd_status.get("pid") if systemd_status else (BOT_PROCESS.pid if BOT_PROCESS is not None and running else None),
        "returncode": returncode,
        "started_at": BOT_STARTED_AT if running else None,
        "mode": "dashboard-managed" if control_enabled else bot_control_mode(),
        "control_enabled": control_enabled,
        "service": systemd_status.get("service"),
        "status_available": systemd_status.get("status_available", True),
        "status_error": systemd_status.get("status_error", ""),
        "log_path": str(BOT_LOG_PATH),
        "last_log": tail_text(BOT_LOG_PATH),
    }


def start_bot_process():
    global BOT_PROCESS, BOT_STARTED_AT
    if not dashboard_bot_control_enabled():
        raise HTTPException(status_code=409, detail="Bot is managed by systemd on this host")
    if not env("DISCORD_TOKEN"):
        raise HTTPException(status_code=500, detail="DISCORD_TOKEN is missing on the server")
    with BOT_LOCK:
        if BOT_PROCESS is not None and BOT_PROCESS.poll() is None:
            return bot_status_payload()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_handle = BOT_LOG_PATH.open("a", encoding="utf-8", errors="replace")
        log_handle.write(f"\n--- Starting bot from dashboard at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.flush()
        try:
            BOT_PROCESS = subprocess.Popen(
                [sys.executable, "bot.py"],
                cwd=str(BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        finally:
            log_handle.close()
        BOT_STARTED_AT = time.time()
        BOT_PID_PATH.write_text(str(BOT_PROCESS.pid), encoding="utf-8")
        return bot_status_payload()


def stop_bot_process():
    global BOT_PROCESS, BOT_STARTED_AT
    if not dashboard_bot_control_enabled():
        raise HTTPException(status_code=409, detail="Bot is managed by systemd on this host")
    with BOT_LOCK:
        if BOT_PROCESS is None or BOT_PROCESS.poll() is not None:
            BOT_PROCESS = None
            BOT_STARTED_AT = 0.0
            return bot_status_payload()
        BOT_PROCESS.terminate()
        try:
            BOT_PROCESS.wait(timeout=12)
        except subprocess.TimeoutExpired:
            BOT_PROCESS.kill()
            BOT_PROCESS.wait(timeout=5)
        status = bot_status_payload()
        BOT_PROCESS = None
        BOT_STARTED_AT = 0.0
        BOT_PID_PATH.unlink(missing_ok=True)
        return status


def require_admin(request: Request):
    if request.session.get("admin") or verify_bearer(request):
        return True
    raise HTTPException(status_code=401, detail="Not logged in")


def auth_secret():
    value = env("SESSION_SECRET")
    if not value:
        raise HTTPException(status_code=500, detail="SESSION_SECRET is missing on the server")
    return value.encode("utf-8")


def create_access_token(username):
    issued = str(int(time.time()))
    body = f"{username}:{issued}"
    sig = hmac.new(auth_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}:{sig}".encode("utf-8")).decode("ascii")


def verify_bearer(request: Request):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    try:
        raw = base64.urlsafe_b64decode(header.split(" ", 1)[1].encode("ascii")).decode("utf-8")
        username, issued, sig = raw.rsplit(":", 2)
        body = f"{username}:{issued}"
        expected = hmac.new(auth_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return int(time.time()) - int(issued) < 60 * 60 * 24 * 14
    except Exception:
        return False


def is_admin_request(request: Request):
    try:
        return bool(request.session.get("admin") or verify_bearer(request))
    except HTTPException:
        return False


def require_configured_auth():
    if not env("ADMIN_PASSWORD"):
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD is missing on the server")
    if not env("SESSION_SECRET"):
        raise HTTPException(status_code=500, detail="SESSION_SECRET is missing on the server")


def require_logged_in(request: Request):
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="Not logged in")
    return True


def request_actor():
    return env("ADMIN_USERNAME", "admin") or "admin"


def token():
    value = env("DISCORD_TOKEN")
    if not value:
        raise HTTPException(status_code=500, detail="DISCORD_TOKEN is missing on the server")
    return value


def discord_request(method, path, payload=None):
    headers = {
        "Authorization": f"Bot {token()}",
        "Content-Type": "application/json",
    }
    response = requests.request(
        method,
        f"{DISCORD_API}{path}",
        headers=headers,
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    if response.text:
        return response.json()
    return None


def first_non_empty_line(value):
    for line in str(value or "").splitlines():
        clean = line.strip()
        if clean:
            return clean
    return ""


def parse_custom_emoji(value):
    raw = value.strip()
    if raw.startswith("<:") and raw.endswith(">"):
        name, emoji_id = raw[2:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": False}
    if raw.startswith("<a:") and raw.endswith(">"):
        name, emoji_id = raw[3:-1].split(":", 1)
        return {"name": name, "id": emoji_id, "animated": True}
    return None


def custom_emoji_value(emoji):
    prefix = "a" if emoji.get("animated") else ""
    return f"<{prefix}:{emoji['name']}:{emoji['id']}>"


SHORTCODE_EMOJI = {
    "white_flag": "🏳️",
    "black_flag": "🏴",
    "pirate_flag": "🏴‍☠️",
    "checkered_flag": "🏁",
    "triangular_flag_on_post": "🚩",
    "crossed_flags": "🎌",
    "rainbow_flag": "🏳️‍🌈",
    "transgender_flag": "🏳️‍⚧️",
    "united_nations": "🇺🇳",
}


def flag_shortcode_to_unicode(name):
    normalized = name.lower()
    if normalized in SHORTCODE_EMOJI:
        return SHORTCODE_EMOJI[normalized]
    if normalized.startswith("flag_"):
        code = normalized.removeprefix("flag_")
        if re.fullmatch(r"[a-z]{2}", code):
            return "".join(chr(0x1F1E6 + ord(ch) - ord("a")) for ch in code)
    return ""


def emoji_name_from_text(value):
    raw = value.strip()
    if raw.startswith(":") and raw.endswith(":") and len(raw) > 2:
        return raw[1:-1].lower()
    if re.fullmatch(r"[A-Za-z0-9_]{2,32}", raw):
        return raw.lower()
    return ""


def resolve_emoji_value(guild_id, value):
    raw = value.strip()
    if parse_custom_emoji(raw) or not emoji_name_from_text(raw):
        return raw
    target = emoji_name_from_text(raw)
    shortcode = flag_shortcode_to_unicode(target)
    if shortcode:
        return shortcode
    for emoji in discord_request("GET", f"/guilds/{guild_id}/emojis"):
        if emoji.get("name", "").lower() == target:
            return custom_emoji_value(emoji)
    return raw


def resolve_emoji_detail(guild_id, value):
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Emoji cannot be empty")
    if parse_custom_emoji(raw):
        return {"input": raw, "resolved": raw, "found": True, "kind": "custom"}
    target = emoji_name_from_text(raw)
    if target:
        shortcode = flag_shortcode_to_unicode(target)
        if shortcode:
            return {"input": raw, "resolved": shortcode, "found": True, "kind": "unicode_shortcode", "name": target}
        for emoji in discord_request("GET", f"/guilds/{guild_id}/emojis"):
            if emoji.get("name", "").lower() == target:
                resolved = custom_emoji_value(emoji)
                return {
                    "input": raw,
                    "resolved": resolved,
                    "found": True,
                    "kind": "server",
                    "name": emoji.get("name"),
                    "id": emoji.get("id"),
                    "animated": bool(emoji.get("animated")),
                }
        raise HTTPException(status_code=404, detail=f"Server emoji '{raw}' was not found")
    if any(ord(ch) > 127 for ch in raw):
        return {"input": raw, "resolved": raw, "found": True, "kind": "unicode"}
    raise HTTPException(
        status_code=400,
        detail="Use a Unicode emoji, :server_emoji_name:, server_emoji_name, or <:name:id>.",
    )


def reaction_route_emoji(value):
    parsed = parse_custom_emoji(value)
    if parsed:
        return f"{parsed['name']}:{parsed['id']}"
    return value


def component_emoji(value):
    parsed = parse_custom_emoji(value)
    if parsed:
        payload = {"name": parsed["name"], "id": parsed["id"]}
        if parsed["animated"]:
            payload["animated"] = True
        return payload
    if any(ord(ch) > 127 for ch in value):
        return {"name": value}
    return None


def role_select_components(message_id, mappings):
    options = []
    for item in mappings[:25]:
        option = {
            "label": (item.get("role_name") or item["role_id"])[:100],
            "value": str(item["role_id"]),
            "description": f"Toggle {(item.get('role_name') or item['role_id'])}"[:100],
        }
        if item.get("emoji"):
            emoji_payload = component_emoji(item["emoji"])
            if emoji_payload:
                option["emoji"] = emoji_payload
        options.append(option)
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": f"role_select:{message_id}",
                    "placeholder": "Select your roles",
                    "min_values": 0,
                    "max_values": min(25, max(1, len(options))),
                    "options": options,
                }
            ],
        }
    ]


def role_button_components(message_id, mappings):
    if not mappings:
        return []
    item = mappings[0]
    button = {
        "type": 2,
        "style": 3,
        "label": (item.get("role_name") or "Accept")[:80],
        "custom_id": f"role_button:{message_id}:{item['role_id']}",
    }
    if item.get("emoji"):
        emoji_payload = component_emoji(item["emoji"])
        if emoji_payload:
            button["emoji"] = emoji_payload
    return [{"type": 1, "components": [button]}]


@app.get("/api/health")
def health():
    return {"ok": True, "storage": storage_name(), "bot": bot_status_payload()}


@app.get("/api/bot/status", dependencies=[Depends(require_admin)])
def get_bot_status():
    return bot_status_payload()


@app.post("/api/bot/start", dependencies=[Depends(require_admin)])
def start_bot():
    return start_bot_process()


@app.post("/api/bot/stop", dependencies=[Depends(require_admin)])
def stop_bot():
    return stop_bot_process()


@app.post("/api/login")
def login(payload: LoginPayload, request: Request):
    require_configured_auth()
    username = env("ADMIN_USERNAME", "admin")
    password = env("ADMIN_PASSWORD")
    if secrets.compare_digest(payload.username, username) and secrets.compare_digest(payload.password, password):
        request.session["admin"] = True
        return {"ok": True, "access_token": create_access_token(username)}
    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"logged_in": is_admin_request(request)}


@app.get("/api/discord/guilds", dependencies=[Depends(require_admin)])
def guilds():
    return discord_request("GET", "/users/@me/guilds")


@app.get("/api/discord/guilds/{guild_id}/channels", dependencies=[Depends(require_admin)])
def channels(guild_id: str):
    data = discord_request("GET", f"/guilds/{guild_id}/channels")
    return [item for item in data if item.get("type") in (0, 5)]


@app.get("/api/discord/guilds/{guild_id}/roles", dependencies=[Depends(require_admin)])
def roles(guild_id: str):
    data = discord_request("GET", f"/guilds/{guild_id}/roles")
    return [item for item in data if item.get("name") != "@everyone" and not item.get("managed")]


@app.get("/api/discord/guilds/{guild_id}/emojis", dependencies=[Depends(require_admin)])
def emojis(guild_id: str):
    return discord_request("GET", f"/guilds/{guild_id}/emojis")


@app.get("/api/discord/guilds/{guild_id}/emojis/resolve", dependencies=[Depends(require_admin)])
def resolve_emoji(guild_id: str, value: str = Query(..., min_length=1)):
    return resolve_emoji_detail(guild_id, value)


@app.get("/api/saved", dependencies=[Depends(require_admin)])
def saved():
    return load_config()


@app.get("/api/audit-logs", dependencies=[Depends(require_admin)])
def audit_logs(limit: int = Query(50, ge=1, le=100)):
    return load_config().get("audit_logs", [])[:limit]


@app.post("/api/messages", dependencies=[Depends(require_admin)])
def send_message(payload: MessagePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    channel = discord_request("GET", f"/channels/{payload.channel_id}")
    guild_id = channel.get("guild_id", "dm")
    body = {}
    record = {
        "channel_id": payload.channel_id,
        "type": "embed" if payload.use_embed else "plain",
        "title": payload.title if payload.use_embed else "",
        "content": payload.content,
        "color": payload.color if payload.use_embed else "",
        "footer": payload.footer if payload.use_embed else "",
    }
    if payload.use_embed:
        embed = {
            "description": payload.content,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title:
            embed["title"] = payload.title
        if payload.footer:
            embed["footer"] = {"text": payload.footer}
        body["embeds"] = [embed]
    else:
        body["content"] = payload.content

    body["allowed_mentions"] = {"parse": ["users", "roles"]}
    result = discord_request("POST", f"/channels/{payload.channel_id}/messages", body)
    upsert_message(guild_id, result["id"], record)
    append_audit_log(
        "sent",
        "messages",
        guild_id,
        result["id"],
        {"channel_id": payload.channel_id, "title": record["title"], "type": record["type"]},
        request_actor(),
    )
    return {"message_id": result["id"], "guild_id": guild_id, "record": record}


@app.post("/api/reaction-roles", dependencies=[Depends(require_admin)])
def create_reaction_role(payload: ReactionRolePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.mappings:
        raise HTTPException(status_code=400, detail="Add at least one role mapping")
    channel = discord_request("GET", f"/channels/{payload.channel_id}")
    guild_id = channel.get("guild_id")
    if not guild_id:
        raise HTTPException(status_code=400, detail="Reaction roles must be in a server channel")

    mappings = []
    for item in payload.mappings:
        mappings.append(
            {
                "emoji": resolve_emoji_value(guild_id, item.emoji),
                "role_id": str(item.role_id),
                "role_name": item.role_name or str(item.role_id),
            }
        )
    if payload.mode == "button":
        mappings = mappings[:1]

    mapping_lines = "\n".join(f"<@&{item['role_id']}>" for item in mappings) if payload.include_role_mentions else ""
    footer_text = payload.description.strip()
    description = ((footer_text + "\n\n") if footer_text and mapping_lines else footer_text) + mapping_lines
    body = {}
    if payload.use_embed:
        embed_payload = {
            "description": description,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title.strip():
            embed_payload["title"] = payload.title.strip()
        body["embeds"] = [embed_payload]
    else:
        body["content"] = f"# {payload.title.strip()}\n{description}" if payload.title.strip() else description
    body["allowed_mentions"] = {"parse": ["users", "roles"]}

    message = discord_request("POST", f"/channels/{payload.channel_id}/messages", body)
    message_id = message["id"]
    failed_reactions = []
    mode = payload.mode if payload.mode in ("reaction", "button") else "dropdown"
    if mode == "reaction":
        for item in mappings:
            route_emoji = urllib.parse.quote(reaction_route_emoji(item["emoji"]), safe="")
            try:
                discord_request("PUT", f"/channels/{payload.channel_id}/messages/{message_id}/reactions/{route_emoji}/@me")
            except HTTPException as exc:
                failed_reactions.append(f"{item['emoji']}: {exc.detail}")
    elif mode == "dropdown":
        discord_request(
            "PATCH",
            f"/channels/{payload.channel_id}/messages/{message_id}",
            {"components": role_select_components(message_id, mappings)},
        )
    else:
        discord_request(
            "PATCH",
            f"/channels/{payload.channel_id}/messages/{message_id}",
            {"components": role_button_components(message_id, mappings)},
        )

    if mode == "reaction" and len(failed_reactions) == len(mappings):
        raise HTTPException(
            status_code=400,
            detail="Message was sent, but no reactions could be added. Check Add Reactions, Read Message History, and Use External Emoji.",
        )

    record = {
        "channel_id": payload.channel_id,
        "title": payload.title.strip(),
        "panel_name": payload.panel_name.strip() or first_non_empty_line(payload.description) or "Untitled role panel",
        "description": description,
        "include_role_mentions": payload.include_role_mentions,
        "mode": mode,
        "kind": "reaction_role",
        "mappings": {item["emoji"]: item["role_id"] for item in mappings},
    }
    upsert_reaction_role(guild_id, message_id, record)
    append_audit_log(
        "posted",
        "reaction_roles",
        guild_id,
        message_id,
        {"channel_id": payload.channel_id, "panel_name": record["panel_name"], "mode": mode},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record, "failed_reactions": failed_reactions}


@app.patch("/api/messages/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def edit_message(guild_id: str, message_id: str, payload: MessagePayload):
    config = load_config()
    existing = config.get("messages", {}).get(str(guild_id), {}).get(str(message_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Saved message not found")
    body = {"allowed_mentions": {"parse": ["users", "roles"]}}
    record = {
        "channel_id": existing.get("channel_id", payload.channel_id),
        "type": "embed" if payload.use_embed else "plain",
        "title": payload.title if payload.use_embed else "",
        "content": payload.content,
        "color": payload.color if payload.use_embed else "",
        "footer": payload.footer if payload.use_embed else "",
    }
    if payload.use_embed:
        embed = {
            "description": payload.content,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title:
            embed["title"] = payload.title
        if payload.footer:
            embed["footer"] = {"text": payload.footer}
        body["content"] = None
        body["embeds"] = [embed]
    else:
        body["content"] = payload.content
        body["embeds"] = []
    discord_request("PATCH", f"/channels/{record['channel_id']}/messages/{message_id}", body)
    upsert_message(guild_id, message_id, record)
    append_audit_log(
        "updated",
        "messages",
        guild_id,
        message_id,
        {"channel_id": record["channel_id"], "title": record["title"], "type": record["type"]},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record}


@app.patch("/api/reaction-roles/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def edit_reaction_role(guild_id: str, message_id: str, payload: ReactionRolePayload):
    config = load_config()
    existing = config.get("reaction_roles", {}).get(str(guild_id), {}).get(str(message_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Saved role panel not found")

    mappings = []
    for item in payload.mappings:
        mappings.append(
            {
                "emoji": resolve_emoji_value(guild_id, item.emoji),
                "role_id": str(item.role_id),
                "role_name": item.role_name or str(item.role_id),
            }
        )
    if payload.mode == "button":
        mappings = mappings[:1]

    mapping_lines = "\n".join(f"<@&{item['role_id']}>" for item in mappings) if payload.include_role_mentions else ""
    footer_text = payload.description.strip()
    description = ((footer_text + "\n\n") if footer_text and mapping_lines else footer_text) + mapping_lines
    mode = payload.mode if payload.mode in ("reaction", "button") else "dropdown"
    channel_id = existing.get("channel_id", payload.channel_id)

    body = {"allowed_mentions": {"parse": ["users", "roles"]}}
    if payload.use_embed:
        embed_payload = {
            "description": description,
            "color": COLOR_MAP.get(payload.color, COLOR_MAP["Blurple"]),
        }
        if payload.title.strip():
            embed_payload["title"] = payload.title.strip()
        body["content"] = None
        body["embeds"] = [embed_payload]
    else:
        body["content"] = f"# {payload.title.strip()}\n{description}" if payload.title.strip() else description
        body["embeds"] = []

    if mode == "dropdown":
        body["components"] = role_select_components(message_id, mappings)
    elif mode == "button":
        body["components"] = role_button_components(message_id, mappings)
    else:
        body["components"] = []

    discord_request("PATCH", f"/channels/{channel_id}/messages/{message_id}", body)

    failed_reactions = []
    if mode == "reaction":
        for item in mappings:
            route_emoji = urllib.parse.quote(reaction_route_emoji(item["emoji"]), safe="")
            try:
                discord_request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{route_emoji}/@me")
            except HTTPException as exc:
                failed_reactions.append(f"{item['emoji']}: {exc.detail}")

    record = {
        "channel_id": channel_id,
        "title": payload.title.strip(),
        "panel_name": payload.panel_name.strip() or first_non_empty_line(payload.description) or "Untitled role panel",
        "description": description,
        "include_role_mentions": payload.include_role_mentions,
        "mode": mode,
        "kind": "reaction_role",
        "mappings": {item["emoji"]: item["role_id"] for item in mappings},
    }
    upsert_reaction_role(guild_id, message_id, record)
    append_audit_log(
        "updated",
        "reaction_roles",
        guild_id,
        message_id,
        {"channel_id": channel_id, "panel_name": record["panel_name"], "mode": mode},
        request_actor(),
    )
    return {"message_id": message_id, "guild_id": guild_id, "record": record, "failed_reactions": failed_reactions}


@app.patch("/api/saved", dependencies=[Depends(require_admin)])
def update_saved(payload: SavedUpdatePayload):
    config = load_config()
    section = "messages" if payload.section == "messages" else "reaction_roles"
    config.setdefault(section, {}).setdefault(str(payload.guild_id), {})[str(payload.message_id)] = payload.payload
    save_config(config)
    append_audit_log("updated_record", section, payload.guild_id, payload.message_id, {}, request_actor())
    return {"ok": True}


@app.delete("/api/saved/{section}/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
def delete_saved(section: str, guild_id: str, message_id: str, delete_discord: bool = False):
    config = load_config()
    table = "messages" if section == "messages" else "reaction_roles"
    item = config.get(table, {}).get(str(guild_id), {}).get(str(message_id))
    if delete_discord and item:
        discord_request("DELETE", f"/channels/{item.get('channel_id')}/messages/{message_id}")
    delete_record(table, guild_id, message_id)
    append_audit_log(
        "deleted" if delete_discord else "deleted_record",
        table,
        guild_id,
        message_id,
        {"channel_id": item.get("channel_id") if item else "", "deleted_discord": delete_discord},
        request_actor(),
    )
    return {"ok": True}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
