DELAY_UNITS = {"minutes": 60, "hours": 3600, "days": 86400}
MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 30 * 86400


def default_welcome_config():
    return {
        "enabled": False,
        "channel_id": "",
        "roles_channel_id": "",
        "welcome_content": "",
        "follow_up_enabled": False,
        "follow_up_content": "",
        "delay_value": 1,
        "delay_unit": "hours",
    }


def normalize_welcome_config(value):
    config = default_welcome_config()
    if not isinstance(value, dict):
        return config
    config["enabled"] = bool(value.get("enabled", False))
    config["channel_id"] = str(value.get("channel_id") or "")
    config["roles_channel_id"] = str(value.get("roles_channel_id") or "")
    config["welcome_content"] = str(value.get("welcome_content") or "")
    config["follow_up_enabled"] = bool(value.get("follow_up_enabled", False))
    config["follow_up_content"] = str(value.get("follow_up_content") or "")
    try:
        config["delay_value"] = int(value.get("delay_value", 1))
    except (TypeError, ValueError):
        config["delay_value"] = 1
    unit = str(value.get("delay_unit") or "hours").lower()
    config["delay_unit"] = unit if unit in DELAY_UNITS else "hours"
    return config


def follow_up_delay_seconds(config):
    return int(config.get("delay_value", 0)) * DELAY_UNITS.get(str(config.get("delay_unit")), 0)


def validate_welcome_config(config, onboarding):
    if not config.get("enabled"):
        return
    if not str(config.get("channel_id") or "").isdigit():
        raise ValueError("Choose a welcome channel")
    if not str(config.get("welcome_content") or "").strip():
        raise ValueError("Welcome message is required")
    if config.get("follow_up_enabled") and not str(config.get("follow_up_content") or "").strip():
        raise ValueError("Follow-up message is required")

    uses_rules_channel = "{rules_channel}" in str(config.get("welcome_content") or "") or (
        config.get("follow_up_enabled") and "{rules_channel}" in str(config.get("follow_up_content") or "")
    )
    uses_roles_channel = "{roles_channel}" in str(config.get("welcome_content") or "") or (
        config.get("follow_up_enabled") and "{roles_channel}" in str(config.get("follow_up_content") or "")
    )
    if uses_roles_channel and not str(config.get("roles_channel_id") or "").isdigit():
        raise ValueError("Choose a role channel")
    if uses_rules_channel or config.get("follow_up_enabled"):
        if not str(onboarding.get("channel_id") or "").isdigit():
            raise ValueError("Configure the New Member Rules channel first")
        role_ids = onboarding_completion_role_ids(onboarding)
        if not role_ids:
            raise ValueError("Configure at least one New Member Rules fan role first")

    if config.get("follow_up_enabled"):
        delay_seconds = follow_up_delay_seconds(config)
        if delay_seconds < MIN_DELAY_SECONDS or delay_seconds > MAX_DELAY_SECONDS:
            raise ValueError("Follow-up delay must be between 1 minute and 30 days")


def render_welcome_template(content, member_id, server_name, rules_channel_id="", roles_channel_id=""):
    values = {
        "{member}": f"<@{member_id}>",
        "{server}": str(server_name or ""),
        "{rules_channel}": f"<#{rules_channel_id}>" if rules_channel_id else "",
        "{roles_channel}": f"<#{roles_channel_id}>" if roles_channel_id else "",
    }
    result = str(content or "")
    for token, value in values.items():
        result = result.replace(token, value)
    return result


def onboarding_completion_role_ids(onboarding):
    role_ids = []
    common_role_id = str(onboarding.get("fan_role_id") or onboarding.get("member_role_id") or "")
    if common_role_id.isdigit():
        role_ids.append(common_role_id)
    for item in (onboarding.get("languages") or {}).values():
        if not isinstance(item, dict) or not item.get("enabled"):
            continue
        role_id = str(item.get("language_role_id") or "")
        if role_id.isdigit() and role_id not in role_ids:
            role_ids.append(role_id)
    return role_ids


def build_follow_up_job(
    guild_id, user_id, channel_id, content, rules_channel_id, fan_role_id,
    joined_at, delay_seconds, fan_role_ids=None, roles_channel_id="",
):
    role_ids = [str(role_id) for role_id in (fan_role_ids or []) if str(role_id).isdigit()]
    if str(fan_role_id or "").isdigit() and str(fan_role_id) not in role_ids:
        role_ids.insert(0, str(fan_role_id))
    return {
        "job_id": f"{guild_id}:{user_id}:{int(float(joined_at) * 1000)}",
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "channel_id": str(channel_id),
        "content": str(content),
        "rules_channel_id": str(rules_channel_id or ""),
        "roles_channel_id": str(roles_channel_id or ""),
        "fan_role_id": str(fan_role_id or ""),
        "fan_role_ids": role_ids,
        "due_at": float(joined_at) + int(delay_seconds),
        "status": "pending",
        "lease_until": 0,
        "attempts": 0,
    }
