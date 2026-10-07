"""Dashboard API routes for saved_items feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/saved", dependencies=[Depends(require_admin)])
def saved():
    return load_config()


@router.get("/api/audit-logs", dependencies=[Depends(require_admin)])
def audit_logs(limit: int = Query(50, ge=1, le=100)):
    return load_config().get("audit_logs", [])[:limit]



@router.patch("/api/saved", dependencies=[Depends(require_admin)])
def update_saved(payload: SavedUpdatePayload):
    config = load_config()
    section = "messages" if payload.section == "messages" else "reaction_roles"
    config.setdefault(section, {}).setdefault(str(payload.guild_id), {})[str(payload.message_id)] = payload.payload
    save_config(config)
    append_audit_log("updated_record", section, payload.guild_id, payload.message_id, {}, request_actor())
    return {"ok": True}


@router.delete("/api/saved/{section}/{guild_id}/{message_id}", dependencies=[Depends(require_admin)])
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


