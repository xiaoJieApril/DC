"""Discord command registrations for this feature area."""
from .core import *

HISTORY_WINDOW_DAYS = 90  # Rolling violation lookback period.
STRIKE_ACTIONS = {
    1: "警告紀錄（Warning）",
    2: "警告 + 觀察期身分組（Warning + Probation）",
    3: "禁言一週（1-week timeout）",
    4: "踢出伺服器（Kick，可申訴）",
}


@bot.message_command(name="Create Moderation Case", guild_only=True)
async def create_moderation_case_from_message(ctx, message: discord.Message):
    permissions = getattr(ctx.author, "guild_permissions", None)
    if not permissions or not (permissions.manage_messages or permissions.moderate_members):
        await ctx.respond("You need Manage Messages or Moderate Members permission.", ephemeral=True)
        return
    if not message.guild or message.author.bot:
        await ctx.respond("Only messages from server members can become moderation cases.", ephemeral=True)
        return
    rules = [
        item
        for item in normalize_moderation_rules(load_config().get("moderation_rules", {}).get(str(ctx.guild.id), []))
        if item.get("enabled")
    ]
    if not rules:
        await ctx.respond("No moderation rules are enabled. Configure rules in the dashboard first.", ephemeral=True)
        return
    draft_id = secrets.token_hex(8)
    draft = {
        "draft_id": draft_id,
        "guild_id": str(ctx.guild.id),
        "moderator_id": str(ctx.author.id),
        "evidence": evidence_snapshot_from_message(message),
        "selected_rule": {},
        "status": "pending",
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 1800,
    }
    save_moderation_draft(draft_id, draft)
    await ctx.respond(
        embed=moderation_draft_embed(draft),
        view=ModerationRuleSelectView(draft_id, rules),
        ephemeral=True,
    )



def strike_suggestion(previous_count):
    """Return the action to take if a new ordinary violation is confirmed."""
    occurrence = previous_count + 1
    if occurrence in STRIKE_ACTIONS:
        return f"本次若成立為第 {occurrence} 次 → {STRIKE_ACTIONS[occurrence]}"
    return f"本次若成立為第 {occurrence} 次 → 已超過踢出門檻，建議永久封鎖（Permanent ban）"


def case_timestamp(case):
    """Safely read a legacy or malformed case timestamp without breaking /history."""
    try:
        return int(case.get("ts") or 0)
    except (TypeError, ValueError):
        return 0


@bot.slash_command(name="history", description="Check a member's violation count within the rolling window")
@commands.has_permissions(manage_messages=True)
async def history(ctx, member: discord.Member):
    cutoff = int(time.time()) - HISTORY_WINDOW_DAYS * 86400
    cases = load_config().get("moderation_cases", {}).get(str(ctx.guild.id), [])
    if not isinstance(cases, list):
        cases = []
    recent = [
        item
        for item in cases
        if isinstance(item, dict)
        and str(item.get("target_user_id")) == str(member.id)
        and case_timestamp(item) >= cutoff
        and item.get("severity") != "red_line"
        and item.get("status") != "accepted"
    ]
    recent.sort(key=case_timestamp, reverse=True)
    count = len(recent)

    lines = [
        f"**{member.mention}** 近 {HISTORY_WINDOW_DAYS} 天內累計 **{count}** 次一般違規"
        f"（不含紅線案件與申訴成功案件）",
        f"建議處分：{strike_suggestion(count)}",
        "",
    ]
    if recent:
        lines.append("__案件紀錄__")
        for item in recent[:10]:
            when = datetime.fromtimestamp(case_timestamp(item), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
            case_id = item.get("case_id") or "未編號"
            action = item.get("action") or "未記錄處分"
            status = item.get("status") or "未記錄狀態"
            lines.append(f"`{case_id}` {when} · {action} · {status}")
        if len(recent) > 10:
            lines.append(f"...還有 {len(recent) - 10} 筆未顯示，請用 /case 查單筆")
    else:
        lines.append("此區間內沒有違規紀錄。")

    await ctx.respond("\n".join(lines), ephemeral=True)

@bot.slash_command(name="warn", description="Create a moderation warning case")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, reason: str, rule_number: str = "", evidence_url: str = ""):
    if not reason.strip():
        await ctx.respond("Reason is required.", ephemeral=True)
        return
    case = create_moderation_case(ctx, member, "warning", reason, rule_number, "normal", evidence_url)
    await send_moderation_case_log(ctx, case)
    await ctx.respond(f"Created warning case **{case['case_id']}** for {member.mention}.", ephemeral=True)


@bot.slash_command(name="probation", description="Give a probation role and create a moderation case")
@commands.has_permissions(manage_roles=True)
async def probation(ctx, member: discord.Member, role: discord.Role, reason: str, rule_number: str = "", evidence_url: str = ""):
    ok, role_reason = bot_can_manage_role(ctx.guild, role)
    if not ok:
        await ctx.respond(role_reason, ephemeral=True)
        return
    await member.add_roles(role, reason=f"Probation by {ctx.author}: {reason}")
    case = create_moderation_case(ctx, member, "probation", reason, rule_number, "serious", evidence_url, f"Probation role: {role.name}")
    await send_moderation_case_log(ctx, case)
    await ctx.respond(f"Created probation case **{case['case_id']}** and gave **{role.name}** to {member.mention}.", ephemeral=True)


@bot.slash_command(name="timeout", description="Timeout a member and create a moderation case")
@commands.has_permissions(moderate_members=True)
async def timeout_member(ctx, member: discord.Member, minutes: int, reason: str, rule_number: str = "", evidence_url: str = ""):
    if minutes <= 0:
        await ctx.respond("Minutes must be greater than 0.", ephemeral=True)
        return
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    try:
        if hasattr(member, "timeout_for"):
            await member.timeout_for(timedelta(minutes=minutes), reason=f"Timeout by {ctx.author}: {reason}")
        else:
            await member.edit(timed_out_until=until, reason=f"Timeout by {ctx.author}: {reason}")
    except discord.Forbidden:
        await ctx.respond("I do not have permission to timeout that member.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await ctx.respond(f"Could not timeout that member: {exc}", ephemeral=True)
        return
    case = create_moderation_case(ctx, member, "timeout", reason, rule_number, "serious", evidence_url, f"Timeout minutes: {minutes}")
    await send_moderation_case_log(ctx, case)
    await ctx.respond(f"Created timeout case **{case['case_id']}** for {member.mention}.", ephemeral=True)


@bot.slash_command(name="case", description="Look up a moderation case")
@commands.has_permissions(manage_messages=True)
async def case_lookup(ctx, case_id: str):
    cases = load_config().get("moderation_cases", {}).get(str(ctx.guild.id), [])
    case = next((item for item in cases if str(item.get("case_id")).lower() == case_id.lower()), None)
    if not case:
        await ctx.respond("Moderation case not found.", ephemeral=True)
        return
    lines = [
        f"**{case.get('case_id')}** · {case.get('status')}",
        f"Target: <@{case.get('target_user_id')}>",
        f"Action: {case.get('action')}",
        f"Rule: {case.get('rule_number') or 'unspecified'}",
        f"Reason: {case.get('reason')}",
    ]
    if case.get("evidence_url"):
        lines.append(f"Evidence: {case.get('evidence_url')}")
    await ctx.respond("\n".join(lines), ephemeral=True)


@bot.slash_command(name="resolvecase", description="Update a moderation case appeal/status")
@commands.has_permissions(manage_messages=True)
async def resolve_case(ctx, case_id: str, status: str = "resolved", notes: str = ""):
    clean_status = status if status in ("open", "accepted", "rejected", "escalated", "resolved") else "resolved"
    updated = update_moderation_case(
        ctx.guild.id,
        case_id,
        {"status": clean_status, "resolution_notes": notes, "resolved_ts": int(time.time()), "resolved_by": str(ctx.author)},
    )
    if not updated:
        await ctx.respond("Moderation case not found.", ephemeral=True)
        return
    await ctx.respond(f"Updated **{case_id}** to **{clean_status}**.", ephemeral=True)




