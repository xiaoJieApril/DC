"""Dashboard API routes for moderation feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/moderation/{guild_id}", dependencies=[Depends(require_admin)])
def get_moderation(
    guild_id: str,
    limit: int = Query(50, ge=1, le=100),
    view: str = Query("active", pattern="^(active|archive|all)$"),
):
    config = load_config()
    all_cases = config.get("moderation_cases", {}).get(str(guild_id), [])
    now = int(time.time())
    cases = []
    for item in filter_status_view(all_cases, view, "case")[:limit]:
        case = dict(item)
        target_user_id = case.get("target_user_id")
        case["strike_summary"] = moderation_strike_summary(all_cases, target_user_id, now=now) if target_user_id else {}
        cases.append(case)
    return {
        "settings": normalize_moderation_settings(config.get("moderation_settings", {}).get(str(guild_id), {})),
        "rules": normalize_moderation_rules(config.get("moderation_rules", {}).get(str(guild_id), [])),
        "cases": cases,
        "counts": status_counts(all_cases, "case"),
        "view": view,
    }


@router.get("/api/moderation/{guild_id}/history/{target_user_id}", dependencies=[Depends(require_admin)])
def get_moderation_history(guild_id: str, target_user_id: str):
    if not str(guild_id).isdigit():
        raise HTTPException(status_code=400, detail="Choose a server")
    if not str(target_user_id).isdigit():
        raise HTTPException(status_code=400, detail="Target user ID must be numeric")
    config = load_config()
    all_cases = config.get("moderation_cases", {}).get(str(guild_id), [])
    return moderation_strike_summary(all_cases, target_user_id)


@router.put("/api/moderation/{guild_id}/settings", dependencies=[Depends(require_admin)])
def save_moderation_settings(guild_id: str, payload: ModerationSettingsPayload):
    settings = normalize_moderation_settings(model_to_dict(payload))
    set_moderation_settings(guild_id, settings)
    append_audit_log("saved_settings", "moderation", guild_id, "", settings, request_actor())
    return settings


@router.get("/api/moderation/{guild_id}/rules", dependencies=[Depends(require_admin)])
def get_moderation_rules(guild_id: str):
    config = load_config()
    return {"rules": normalize_moderation_rules(config.get("moderation_rules", {}).get(str(guild_id), []))}


@router.put("/api/moderation/{guild_id}/rules", dependencies=[Depends(require_admin)])
def save_moderation_rules(guild_id: str, payload: ModerationRulesPayload):
    rules = normalize_moderation_rules([model_to_dict(item) for item in payload.rules])
    try:
        validate_moderation_rules(rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_moderation_rules(guild_id, rules)
    append_audit_log("saved_rules", "moderation", guild_id, "", {"count": len(rules)}, request_actor())
    return {"rules": rules}


@router.post("/api/moderation/{guild_id}/evidence/resolve", dependencies=[Depends(require_admin)])
def resolve_moderation_evidence(guild_id: str, payload: EvidenceResolvePayload):
    try:
        ids = parse_discord_message_url(payload.message_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ids["guild_id"] != str(guild_id):
        raise HTTPException(status_code=400, detail="The message belongs to another server")
    result = discord_cached_get(
        f"/channels/{ids['channel_id']}/messages/{ids['message_id']}",
        f"message:{ids['guild_id']}:{ids['channel_id']}:{ids['message_id']}",
        persist=False,
        ttl=30,
    )
    snapshot = evidence_snapshot_from_api(result["data"], ids["guild_id"], ids["channel_id"])
    if not snapshot["author_id"]:
        raise HTTPException(status_code=400, detail="The Discord message has no author")
    return {"evidence": snapshot, "stale": bool(result.get("stale")), "cached_at": result.get("cached_at")}


@router.post("/api/moderation/cases", dependencies=[Depends(require_admin)])
def create_moderation_case(payload: ModerationCasePayload):
    if not str(payload.guild_id).isdigit():
        raise HTTPException(status_code=400, detail="Choose a server")
    if not str(payload.target_user_id).isdigit():
        raise HTTPException(status_code=400, detail="Target user ID must be numeric")
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Reason is required")
    config = load_config()
    rules = normalize_moderation_rules(config.get("moderation_rules", {}).get(str(payload.guild_id), []))
    selected_rule = next((item for item in rules if item["rule_id"] == payload.rule_id), None) if payload.rule_id else None
    if payload.rule_id and not selected_rule:
        raise HTTPException(status_code=400, detail="The selected moderation rule no longer exists")
    evidence = dict(payload.evidence_snapshot or {})
    if evidence:
        if str(evidence.get("guild_id") or "") != str(payload.guild_id):
            raise HTTPException(status_code=400, detail="Evidence belongs to another server")
        if str(evidence.get("author_id") or "") != str(payload.target_user_id):
            raise HTTPException(status_code=400, detail="Evidence author does not match the target user")
    settings = normalize_moderation_settings(config.get("moderation_settings", {}).get(str(payload.guild_id), {}))
    action = apply_moderation_action(payload, settings)
    case = {
        "case_id": next_case_id(config, payload.guild_id),
        "guild_id": str(payload.guild_id),
        "target_user_id": str(payload.target_user_id),
        "target_display": payload.target_display.strip(),
        "rule_id": payload.rule_id.strip(),
        "rule_name": payload.rule_name.strip(),
        "rule_snapshot": {
            "rule_id": payload.rule_id.strip(),
            "number": payload.rule_number.strip(),
            "name": payload.rule_name.strip() or payload.violation_type.strip(),
            "reason": payload.reason.strip(),
            "severity": payload.severity,
            "action": action,
            "timeout_minutes": int(payload.timeout_minutes or 0),
            "remove_role_id": str(payload.remove_role_id or ""),
        } if payload.rule_id else {},
        "rule_number": payload.rule_number.strip(),
        "violation_type": payload.violation_type.strip(),
        "severity": payload.severity if payload.severity in ("normal", "serious", "red_line") else "normal",
        "action": action,
        "reason": payload.reason.strip(),
        "evidence_url": str(evidence.get("jump_url") or payload.evidence_url).strip(),
        "evidence_snapshot": evidence,
        "notes": payload.notes.strip(),
        "status": "open",
        "status_history": [],
        "actor": request_actor(),
        "ts": int(time.time()),
    }
    append_moderation_case(payload.guild_id, case)
    log_channel_id = payload.log_channel_id or settings.get("log_channel_id")
    if log_channel_id:
        send_moderation_log(case, log_channel_id)
    append_audit_log("created_case", "moderation", payload.guild_id, case["case_id"], {"action": action, "target": case["target_user_id"]}, request_actor())
    return case


@router.patch("/api/moderation/{guild_id}/cases/{case_id}", dependencies=[Depends(require_admin)])
def resolve_moderation_case(guild_id: str, case_id: str, payload: ModerationResolvePayload):
    status = payload.status if payload.status in ("open", "accepted", "rejected", "escalated", "resolved") else "resolved"
    config = load_config()
    existing = next(
        (item for item in config.get("moderation_cases", {}).get(str(guild_id), []) if str(item.get("case_id")) == str(case_id)),
        None,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Moderation case not found")
    updated = update_moderation_case(
        guild_id,
        case_id,
        status_update(existing, status, request_actor(), payload.notes.strip()),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Moderation case not found")
    append_audit_log("updated_case", "moderation", guild_id, case_id, {"status": status}, request_actor())
    return updated



