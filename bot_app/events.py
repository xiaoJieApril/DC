"""Bot lifecycle and Discord event handlers."""
from .core import *

def welcome_allowed_mentions(member):
    return discord.AllowedMentions(everyone=False, roles=False, users=[member], replied_user=False)


def log_welcome_result(action, job=None, guild_id="", user_id="", detail=""):
    payload = {"user_id": str(user_id or (job or {}).get("user_id") or "")}
    if detail:
        payload["detail"] = str(detail)[:500]
    append_audit_log(
        action,
        "welcome_automation",
        str(guild_id or (job or {}).get("guild_id") or ""),
        "",
        payload,
        "bot",
    )


async def process_welcome_follow_up(job):
    job_id = str(job.get("job_id") or "")
    guild = bot.get_guild(int(job.get("guild_id") or 0))
    if not guild:
        log_welcome_result("follow_up_skipped", job, detail="Server is no longer available")
        finish_welcome_job(job_id)
        return

    try:
        member = await fetch_member(guild, int(job.get("user_id") or 0))
    except (discord.Forbidden, discord.HTTPException) as exc:
        member = None
        member_error = exc
    else:
        member_error = None
    if member_error:
        await retry_or_finish_welcome_job(job, member_error)
        return
    if not member:
        log_welcome_result("follow_up_skipped", job, detail="Member left the server")
        finish_welcome_job(job_id)
        return

    role_ids = [str(role_id) for role_id in (job.get("fan_role_ids") or [])]
    legacy_role_id = str(job.get("fan_role_id") or "")
    if legacy_role_id and legacy_role_id not in role_ids:
        role_ids.append(legacy_role_id)
    completed = any(
        role_id.isdigit() and (guild.get_role(int(role_id)) in member.roles)
        for role_id in role_ids
    )
    if completed:
        log_welcome_result("follow_up_skipped", job, detail="Member already completed onboarding")
        finish_welcome_job(job_id)
        return

    channel_id = str(job.get("channel_id") or "")
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if not channel:
        log_welcome_result("follow_up_failed", job, detail="Welcome channel is no longer available")
        finish_welcome_job(job_id)
        return

    content = render_welcome_template(
        job.get("content"), member.id, guild.name, job.get("rules_channel_id", ""), job.get("roles_channel_id", "")
    ).strip()
    if not content:
        log_welcome_result("follow_up_failed", job, detail="Follow-up message rendered empty")
        finish_welcome_job(job_id)
        return
    try:
        await channel.send(content, allowed_mentions=welcome_allowed_mentions(member))
    except (discord.Forbidden, discord.NotFound) as exc:
        log_welcome_result("follow_up_failed", job, detail=exc)
        finish_welcome_job(job_id)
    except discord.HTTPException as exc:
        await retry_or_finish_welcome_job(job, exc)
    else:
        log_welcome_result("follow_up_sent", job)
        finish_welcome_job(job_id)


async def retry_or_finish_welcome_job(job, error):
    attempts = int(job.get("attempts") or 0) + 1
    if attempts < 3:
        retry_welcome_job(job.get("job_id"), time.time() + 300, error)
        log_welcome_result("follow_up_retry", job, detail=f"Attempt {attempts}: {error}")
        return
    log_welcome_result("follow_up_failed", job, detail=f"Failed after {attempts} attempts: {error}")
    finish_welcome_job(job.get("job_id"))


@tasks.loop(seconds=30)
async def welcome_follow_up_worker():
    for job in claim_due_welcome_jobs(time.time()):
        try:
            await process_welcome_follow_up(job)
        except Exception as exc:
            logger.exception("[WELCOME] Unexpected follow-up failure")
            await retry_or_finish_welcome_job(job, exc)


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    config = load_config()
    welcome = normalize_welcome_config(config.get("welcome_automation", {}).get(str(member.guild.id), {}))
    if not welcome.get("enabled"):
        return

    channel_id = str(welcome.get("channel_id") or "")
    channel = member.guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    onboarding = config.get("onboarding", {}).get(str(member.guild.id), {})
    rules_channel_id = str(onboarding.get("channel_id") or "")
    roles_channel_id = str(welcome.get("roles_channel_id") or onboarding.get("roles_channel_id") or "")
    fan_role_id = str(onboarding.get("fan_role_id") or onboarding.get("member_role_id") or "")
    fan_role_ids = onboarding_completion_role_ids(onboarding)
    content = render_welcome_template(
        welcome.get("welcome_content"), member.id, member.guild.name, rules_channel_id, roles_channel_id
    ).strip()

    if channel and content:
        try:
            await channel.send(content, allowed_mentions=welcome_allowed_mentions(member))
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[WELCOME] Could not welcome {member.id}: {exc}")
            log_welcome_result("welcome_failed", guild_id=member.guild.id, user_id=member.id, detail=exc)
        else:
            log_welcome_result("welcome_sent", guild_id=member.guild.id, user_id=member.id)
    else:
        log_welcome_result(
            "welcome_failed", guild_id=member.guild.id, user_id=member.id, detail="Welcome channel or message unavailable"
        )

    if welcome.get("follow_up_enabled") and welcome.get("follow_up_content", "").strip():
        joined_at = time.time()
        job = build_follow_up_job(
            member.guild.id,
            member.id,
            channel_id,
            welcome["follow_up_content"],
            rules_channel_id,
            fan_role_id,
            joined_at,
            follow_up_delay_seconds(welcome),
            fan_role_ids=fan_role_ids,
            roles_channel_id=roles_channel_id,
        )
        enqueue_welcome_job(job)


@bot.event
async def on_ready():
    print(f"[BOT] Online as {bot.user} (ID: {bot.user.id})")
    print(f"[BOT] Serving {len(bot.guilds)} guild(s)")
    print("[ONBOARDING] Server Members Intent is required for rules-gate role assignment")
    print("[RR] Dropdown role panels are handled by live interaction routing")
    if not welcome_follow_up_worker.is_running():
        welcome_follow_up_worker.start()
    print("[WELCOME] Follow-up worker is running")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    data = getattr(interaction, "data", {}) or {}
    custom_id = str(data.get("custom_id", ""))
    is_role_interaction = custom_id.startswith("role_select:") or custom_id.startswith("role_button:")
    is_onboarding_interaction = custom_id.startswith("onboarding_language:") or custom_id.startswith("onboarding_agree:")
    is_ticket_interaction = custom_id.startswith("ticket_open:")
    if not (is_role_interaction or is_onboarding_interaction or is_ticket_interaction):
        return

    action_key = None
    try:
        if not interaction.guild:
            await interaction.response.send_message("This action only works inside a server.", ephemeral=True)
            return

        if custom_id.startswith("ticket_open:"):
            guild_id = custom_id.split(":", 1)[1]
            if str(interaction.guild.id) != str(guild_id):
                await interaction.response.send_message("This ticket panel belongs to another server.", ephemeral=True)
                return
            await interaction.response.send_modal(TicketModal(guild_id))
            return

        await interaction.response.defer(ephemeral=True)
        if custom_id.startswith("onboarding_language:"):
            action_key, retry_after = await begin_bot_action(interaction, "rules_view", 2, custom_id)
        else:
            action_key, retry_after = await begin_bot_action(interaction, "role_mutation", 3, custom_id)
        if not action_key:
            await interaction.followup.send(
                f"This action is already being handled. Please wait {retry_after}s and try again.",
                ephemeral=True,
            )
            return
        config = load_config()
        if custom_id.startswith("onboarding_language:"):
            entry = get_onboarding_entry(config, interaction.guild.id)
            if not entry:
                await interaction.followup.send("Onboarding is not configured right now.", ephemeral=True)
                return
            values = interaction_select_values(interaction)
            language = selected_onboarding_language(values)
            if not language:
                await interaction.followup.send("Choose exactly one language.", ephemeral=True)
                return
            await send_onboarding_rules(interaction, entry, language)
            return

        if custom_id.startswith("onboarding_agree:"):
            parts = custom_id.split(":", 2)
            language = parts[2] if len(parts) > 2 else ""
            entry = get_onboarding_entry(config, interaction.guild.id)
            if not entry or not onboarding_language(entry, language):
                await interaction.followup.send("This onboarding option is not available anymore.", ephemeral=True)
                return
            result = await apply_onboarding_agreement(interaction, entry, language)
            await interaction.followup.send(result, ephemeral=True)
            return

        if custom_id.startswith("role_button:"):
            _, role_id, entry = find_button_entry(config, interaction.guild.id, custom_id)
            if not entry:
                await interaction.followup.send("This role button is not configured anymore.", ephemeral=True)
                return
            result = await apply_role_button(interaction, role_id)
            await interaction.followup.send(result, ephemeral=True)
            return

        _, entry = find_select_entry(config, interaction.guild.id, custom_id)
        if not entry:
            await interaction.followup.send("This role panel is not configured anymore.", ephemeral=True)
            return

        values = [str(value) for value in data.get("values", [])]
        if "none" in values:
            await interaction.followup.send("No roles are configured for this menu.", ephemeral=True)
            return

        result = await apply_role_selection(interaction, entry, values)
        await interaction.followup.send(result, ephemeral=True)
    except Exception as exc:
        logger.exception("[INTERACTION] Component interaction failed")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("This action could not be completed right now. Please try again later.", ephemeral=True)
            else:
                await interaction.response.send_message("This action could not be completed right now. Please try again later.", ephemeral=True)
        except Exception as nested_exc:
            logger.exception("[INTERACTION] Could not report interaction failure")
    finally:
        await finish_bot_action(action_key)

async def queue_reaction_change(payload, should_have):
    if not payload.guild_id or payload.user_id == bot.user.id:
        return
    config = load_config()
    entry = get_rr_entry(config, payload.guild_id, payload.message_id)
    if not entry:
        return
    role_id = entry.get("mappings", {}).get(emoji_key(payload.emoji))
    if not role_id:
        return
    key = (str(payload.guild_id), str(payload.user_id), str(role_id))
    if key in REACTION_PENDING or key in REACTION_TASKS:
        RATE_COORDINATOR.increment("bot_reaction_merged")
    REACTION_PENDING[key] = {
        "guild_id": payload.guild_id,
        "user_id": payload.user_id,
        "role_id": role_id,
        "member": getattr(payload, "member", None),
        "should_have": bool(should_have),
    }
    if key not in REACTION_TASKS:
        REACTION_TASKS[key] = asyncio.create_task(flush_reaction_change(key))


async def flush_reaction_change(key):
    try:
        while True:
            await asyncio.sleep(0.75)
            state = REACTION_PENDING.pop(key, None)
            if not state:
                return
            allowed, retry_after = RATE_COORDINATOR.acquire(f"bot:reaction:{':'.join(key)}", 1)
            if not allowed:
                RATE_COORDINATOR.increment("bot_reaction_cooldown")
                REACTION_PENDING.setdefault(key, state)
                await asyncio.sleep(retry_after)
                continue

            guild = bot.get_guild(state["guild_id"])
            if not guild:
                continue
            role = guild.get_role(int(state["role_id"]))
            member = state.get("member") or await fetch_member(guild, state["user_id"])
            if not role or not member or member.bot:
                continue
            ok, reason = bot_can_manage_role(guild, role)
            if not ok:
                print(f"[RR] Cannot update {role.name}: {reason}")
                continue
            try:
                if state["should_have"] and role not in member.roles:
                    await await_shared_discord_permit()
                    await member.add_roles(role, reason="Reaction role added")
                    print(f"[RR] Gave {role.name} to {member.display_name}")
                elif not state["should_have"] and role in member.roles:
                    await await_shared_discord_permit()
                    await member.remove_roles(role, reason="Reaction role removed")
                    print(f"[RR] Removed {role.name} from {member.display_name}")
            except discord.Forbidden:
                print(f"[RR] Missing permission to update {role.name}")
            except discord.HTTPException:
                print(f"[RR] Discord could not update {role.name} right now")
            if key not in REACTION_PENDING:
                return
    finally:
        REACTION_TASKS.pop(key, None)
        if key in REACTION_PENDING and key not in REACTION_TASKS:
            REACTION_TASKS[key] = asyncio.create_task(flush_reaction_change(key))


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await queue_reaction_change(payload, True)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await queue_reaction_change(payload, False)



