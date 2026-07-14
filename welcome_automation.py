DELAY_UNITS = {"minutes": 60, "hours": 3600, "days": 86400}
MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 30 * 86400


def default_welcome_config():
    return {
        "enabled": False,
        "channel_id": "",
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
    if uses_rules_channel or config.get("follow_up_enabled"):
        if not str(onboarding.get("channel_id") or "").isdigit():
            raise ValueError("Configure the New Member Rules channel first")
        if not str(onboarding.get("fan_role_id") or onboarding.get("member_role_id") or "").isdigit():
            raise ValueError("Configure the New Member Rules fan role first")

    if config.get("follow_up_enabled"):
        delay_seconds = follow_up_delay_seconds(config)
        if delay_seconds < MIN_DELAY_SECONDS or delay_seconds > MAX_DELAY_SECONDS:
            raise ValueError("Follow-up delay must be between 1 minute and 30 days")


def render_welcome_template(content, member_id, server_name, rules_channel_id=""):
    values = {
        "{member}": f"<@{member_id}>",
        "{server}": str(server_name or ""),
        "{rules_channel}": f"<#{rules_channel_id}>" if rules_channel_id else "",
    }
    result = str(content or "")
    for token, value in values.items():
        result = result.replace(token, value)
    return result


def build_follow_up_job(guild_id, user_id, channel_id, content, rules_channel_id, fan_role_id, joined_at, delay_seconds):
    return {
        "job_id": f"{guild_id}:{user_id}:{int(float(joined_at) * 1000)}",
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "channel_id": str(channel_id),
        "content": str(content),
        "rules_channel_id": str(rules_channel_id or ""),
        "fan_role_id": str(fan_role_id or ""),
        "due_at": float(joined_at) + int(delay_seconds),
        "status": "pending",
        "lease_until": 0,
        "attempts": 0,
    }
