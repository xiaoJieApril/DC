"""Dashboard API routes for onboarding feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/onboarding/{guild_id}", dependencies=[Depends(require_admin)])
def get_onboarding(guild_id: str):
    # Load the fan-role gate settings for the selected Discord server.
    config = load_config()
    return normalize_onboarding_config(config.get("onboarding", {}).get(str(guild_id), {}))


@router.put("/api/onboarding/{guild_id}", dependencies=[Depends(require_admin)])
def save_onboarding(guild_id: str, payload: OnboardingPayload):
    existing = load_config().get("onboarding", {}).get(str(guild_id), {})
    data = model_to_dict(payload)
    if not data.get("panel_message_id"):
        data["panel_message_id"] = existing.get("panel_message_id", "")
    # Save the dashboard-facing gate without dropping older panel metadata.
    config = normalize_onboarding_config(data)
    upsert_onboarding(guild_id, config)
    append_audit_log(
        "saved",
        "onboarding",
        guild_id,
        config.get("panel_message_id", ""),
        {"channel_id": config.get("channel_id"), "enabled": config.get("enabled")},
        request_actor(),
    )
    return config


@router.post("/api/onboarding/{guild_id}/server-rules-defaults", dependencies=[Depends(require_admin)])
def apply_server_rules_defaults(guild_id: str):
    existing = normalize_onboarding_config(load_config().get("onboarding", {}).get(str(guild_id), {}))
    defaults = server_rules_onboarding_defaults()
    existing.update({key: value for key, value in defaults.items() if key != "languages"})
    existing["languages"] = defaults["languages"]
    config = normalize_onboarding_config(existing)
    upsert_onboarding(guild_id, config)
    append_audit_log("loaded_defaults", "onboarding", guild_id, config.get("panel_message_id", ""), {}, request_actor())
    return config


@router.post("/api/onboarding/{guild_id}/publish", dependencies=[Depends(require_admin)])
def publish_onboarding(guild_id: str):
    config = normalize_onboarding_config(load_config().get("onboarding", {}).get(str(guild_id), {}))
    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Enable onboarding before publishing")
    channel_id = str(config.get("channel_id") or "")
    if not channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Choose a rules channel")
    missing_language_roles = [
        label
        for code, label, _ in enabled_onboarding_languages(config)
        if not str((config.get("languages", {}).get(code) or {}).get("language_role_id") or "").isdigit()
    ]
    if missing_language_roles:
        raise HTTPException(status_code=400, detail=f"Choose a role for: {', '.join(missing_language_roles)}")
    cached_guild_channel(guild_id, channel_id)

    payload = onboarding_panel_payload(guild_id, config)
    panel_message_id = str(config.get("panel_message_id") or "")
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

    config["panel_message_id"] = panel_message_id
    upsert_onboarding(guild_id, config)
    append_audit_log(
        "published",
        "onboarding",
        guild_id,
        panel_message_id,
        {"channel_id": channel_id, "languages": [code for code, _, _ in enabled_onboarding_languages(config)]},
        request_actor(),
    )
    return {"ok": True, "message_id": panel_message_id, "guild_id": guild_id, "record": config}



