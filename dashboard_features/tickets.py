"""Dashboard API routes for tickets feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/tickets/{guild_id}", dependencies=[Depends(require_admin)])
def get_tickets(
    guild_id: str,
    limit: int = Query(50, ge=1, le=100),
    view: str = Query("active", pattern="^(active|archive|all)$"),
):
    config = load_config()
    all_tickets = config.get("tickets", {}).get(str(guild_id), [])
    return {
        "settings": normalize_ticket_settings(config.get("ticket_settings", {}).get(str(guild_id), {})),
        "tickets": filter_status_view(all_tickets, view, "ticket")[:limit],
        "counts": status_counts(all_tickets, "ticket"),
        "view": view,
    }


@router.put("/api/tickets/{guild_id}/settings", dependencies=[Depends(require_admin)])
def save_ticket_settings(guild_id: str, payload: TicketSettingsPayload):
    existing = normalize_ticket_settings(load_config().get("ticket_settings", {}).get(str(guild_id), {}))
    data = model_to_dict(payload)
    if not data.get("panel_message_id"):
        data["panel_message_id"] = existing.get("panel_message_id", "")
    settings = normalize_ticket_settings(data)
    set_ticket_settings(guild_id, settings)
    append_audit_log("saved_settings", "tickets", guild_id, settings.get("panel_message_id", ""), settings, request_actor())
    return settings


@router.post("/api/tickets/{guild_id}/publish", dependencies=[Depends(require_admin)])
def publish_ticket_panel(guild_id: str):
    settings = normalize_ticket_settings(load_config().get("ticket_settings", {}).get(str(guild_id), {}))
    channel_id = str(settings.get("ticket_channel_id") or "")
    if not channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Choose a ticket channel")
    cached_guild_channel(guild_id, channel_id)

    payload = ticket_panel_payload(guild_id, settings)
    panel_message_id = str(settings.get("panel_message_id") or "")
    if panel_message_id:
        try:
            discord_request("PATCH", f"/channels/{channel_id}/messages/{panel_message_id}", payload)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            panel_message_id = ""
    if not panel_message_id:
        message = discord_request("POST", f"/channels/{channel_id}/messages", payload)
        panel_message_id = message["id"]

    settings["panel_message_id"] = panel_message_id
    set_ticket_settings(guild_id, settings)
    append_audit_log("published_panel", "tickets", guild_id, panel_message_id, {"channel_id": channel_id}, request_actor())
    return {"ok": True, "message_id": panel_message_id, "settings": settings}


@router.patch("/api/tickets/{guild_id}/{ticket_id}", dependencies=[Depends(require_admin)])
def update_ticket_status(guild_id: str, ticket_id: str, payload: TicketStatusPayload):
    status = payload.status if payload.status in ("open", "resolved", "rejected", "escalated") else "resolved"
    config = load_config()
    existing = next(
        (item for item in config.get("tickets", {}).get(str(guild_id), []) if str(item.get("ticket_id")) == str(ticket_id)),
        None,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Ticket not found")
    updated = update_ticket(
        guild_id,
        ticket_id,
        status_update(existing, status, request_actor(), payload.notes.strip(), kind="ticket"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found")
    append_audit_log("updated_ticket", "tickets", guild_id, ticket_id, {"status": status}, request_actor())
    return updated



