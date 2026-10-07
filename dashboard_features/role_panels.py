"""Dashboard API routes for role_panels feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.post("/api/reaction-roles", dependencies=[Depends(require_admin)])
def create_reaction_role(payload: ReactionRolePayload):
    if not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Channel ID must be numeric")
    if not payload.mappings:
        raise HTTPException(status_code=400, detail="Add at least one role mapping")
    if payload.guild_id.isdigit():
        cached_guild_channel(payload.guild_id, payload.channel_id)
        guild_id = payload.guild_id
    else:
        channel = cached_channel(payload.channel_id)
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

    # Mapping data controls the role component only; visible panel text is written manually.
    footer_text = payload.description.strip()
    description = footer_text
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
        "include_role_mentions": False,
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



@router.patch("/api/reaction-roles/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
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

    # Mapping data controls the role component only; visible panel text is written manually.
    footer_text = payload.description.strip()
    description = footer_text
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
        "include_role_mentions": False,
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



