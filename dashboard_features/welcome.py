"""Dashboard API routes for welcome feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/welcome-automation/{guild_id}", dependencies=[Depends(require_admin)])
def get_welcome_automation(guild_id: str):
    config = load_config()
    return normalize_welcome_config(config.get("welcome_automation", {}).get(str(guild_id), {}))


@router.put("/api/welcome-automation/{guild_id}", dependencies=[Depends(require_admin)])
def save_welcome_automation(guild_id: str, payload: WelcomeAutomationPayload):
    welcome = normalize_welcome_config(model_to_dict(payload))
    onboarding = normalize_onboarding_config(load_config().get("onboarding", {}).get(str(guild_id), {}))

    if welcome["enabled"]:
        try:
            validate_welcome_config(welcome, onboarding)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cached_guild_channel(guild_id, welcome["channel_id"])
        if welcome["roles_channel_id"]:
            cached_guild_channel(guild_id, welcome["roles_channel_id"])

    upsert_welcome_automation(guild_id, welcome)
    cancelled = 0
    if not welcome["enabled"]:
        cancelled = cancel_pending_welcome_jobs(guild_id)
    append_audit_log(
        "saved",
        "welcome_automation",
        guild_id,
        "",
        {
            "channel_id": welcome["channel_id"],
            "enabled": welcome["enabled"],
            "follow_up_enabled": welcome["follow_up_enabled"],
            "cancelled_jobs": cancelled,
        },
        request_actor(),
    )
    return {**welcome, "cancelled_jobs": cancelled}



