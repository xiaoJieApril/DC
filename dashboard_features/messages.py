"""Dashboard API routes for messages feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.post("/api/messages", dependencies=[Depends(require_admin)])
def send_message(payload: MessagePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if payload.guild_id.isdigit():
        cached_guild_channel(payload.guild_id, payload.channel_id)
        guild_id = payload.guild_id
    else:
        channel = cached_channel(payload.channel_id)
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



@router.patch("/api/messages/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
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



