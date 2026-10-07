import asyncio
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from storage import (
    append_audit_log,
    append_moderation_case,
    append_ticket,
    claim_moderation_draft,
    claim_due_welcome_jobs,
    enqueue_welcome_job,
    delete_moderation_draft,
    finish_welcome_job,
    get_moderation_draft,
    init_db,
    load_config,
    retry_welcome_job,
    save_moderation_draft,
    save_config,
    update_moderation_case,
    upsert_message,
)
from moderation_tools import evidence_snapshot_from_message, normalize_moderation_rules
from welcome_automation import (
    build_follow_up_job,
    follow_up_delay_seconds,
    normalize_welcome_config,
    onboarding_completion_role_ids,
    render_welcome_template,
)
from request_limits import SharedRateCoordinator

load_dotenv()
init_db()

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BOT_LOG_PATH = LOG_DIR / "dashboard_bot.log"
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.FileHandler(BOT_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
RATE_COORDINATOR = SharedRateCoordinator(BASE_DIR / "data" / "request_limits.sqlite3")
BOT_ACTION_INFLIGHT = set()
BOT_ACTION_LOCK = asyncio.Lock()
MEMBER_FETCHES = {}
MEMBER_NEGATIVE_CACHE = {}
REACTION_PENDING = {}
REACTION_TASKS = {}


async def await_shared_discord_permit():
    try:
        limit = max(1, int(os.getenv("DISCORD_SHARED_REQUESTS_PER_SECOND", "25")))
    except (TypeError, ValueError):
        limit = 25
    while True:
        allowed, retry_after = RATE_COORDINATOR.check_window("discord_api_global_budget", limit, 1)
        if allowed:
            return
        RATE_COORDINATOR.increment("shared_discord_budget_waited")
        await asyncio.sleep(max(0.1, retry_after))

COLOR_MAP = {
    "blurple": 0x5865F2,
    "green": 0x57F287,
    "red": 0xED4245,
    "yellow": 0xFEE75C,
    "white": 0xFFFFFF,
}
DISPLAY_COLOR_MAP = {
    "Blurple": 0x5865F2,
    "Green": 0x57F287,
    "Red": 0xED4245,
    "Yellow": 0xFEE75C,
    "White": 0xFFFFFF,
}


def get_rr_entry(config, guild_id, message_id):
    return (
        config.get("reaction_roles", {})
        .get(str(guild_id), {})
        .get(str(message_id))
    )


def find_select_entry(config, guild_id, custom_id):
    if not custom_id.startswith("role_select:"):
        return None, None
    message_id = custom_id.split(":", 1)[1]
    entry = get_rr_entry(config, guild_id, message_id)
    if not entry or entry.get("mode", "dropdown") not in ("dropdown", "multi_select"):
        return None, None
    return message_id, entry


def find_button_entry(config, guild_id, custom_id):
    if not custom_id.startswith("role_button:"):
        return None, None, None
    parts = custom_id.split(":")
    if len(parts) < 3:
        return None, None, None
    message_id, role_id = parts[1], parts[2]
    entry = get_rr_entry(config, guild_id, message_id)
    if not entry or entry.get("mode") != "button":
        return None, None, None
    return message_id, role_id, entry


def ensure_guild_rr(config, guild_id):
    return config.setdefault("reaction_roles", {}).setdefault(str(guild_id), {})


def bot_can_manage_role(guild, role):
    me = guild.me
    if not me:
        return False, "Bot member is not available yet."
    if role.is_default():
        return False, "I cannot manage the @everyone role."
    if role.managed:
        return False, "That role is managed by an integration and cannot be assigned manually."
    if role >= me.top_role:
        return False, f"My highest role must be above **{role.name}**."
    if not guild.me.guild_permissions.manage_roles:
        return False, "I need the **Manage Roles** permission."
    return True, ""


async def apply_role_selection(interaction, entry, selected_role_ids):
    guild = interaction.guild
    member = guild.get_member(interaction.user.id) or await fetch_member(guild, interaction.user.id)
    if not member:
        return "I could not find your server member profile. Please try again."

    mapped_role_ids = set(entry.get("mappings", {}).values())
    selected_role_ids = set(selected_role_ids)
    roles_to_add = []
    roles_to_remove = []
    skipped = []

    for role_id in mapped_role_ids:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            skipped.append(f"invalid role {role_id}")
            continue
        if not role:
            skipped.append(f"missing role {role_id}")
            continue

        ok, reason = bot_can_manage_role(guild, role)
        if not ok:
            skipped.append(f"{role.name}: {reason}")
            continue

        has_role = role in member.roles
        should_have = role_id in selected_role_ids

        if should_have and not has_role:
            roles_to_add.append(role)
        elif not should_have and has_role:
            roles_to_remove.append(role)

    added = []
    removed = []
    if roles_to_add:
        try:
            await await_shared_discord_permit()
            await member.add_roles(*roles_to_add, reason="Dropdown role selection")
            added = [role.name for role in roles_to_add]
        except discord.Forbidden:
            skipped.append("could not add roles: missing permission")
        except discord.HTTPException:
            skipped.append("could not add roles right now")
    if roles_to_remove:
        try:
            await await_shared_discord_permit()
            await member.remove_roles(*roles_to_remove, reason="Dropdown role selection")
            removed = [role.name for role in roles_to_remove]
        except discord.Forbidden:
            skipped.append("could not remove roles: missing permission")
        except discord.HTTPException:
            skipped.append("could not remove roles right now")

    parts = []
    if added:
        parts.append("Added: " + ", ".join(added))
    if removed:
        parts.append("Removed: " + ", ".join(removed))
    if skipped:
        parts.append("Skipped: " + "; ".join(skipped[:3]))
    return "\n".join(parts) or "No role changes needed."


async def apply_role_button(interaction, role_id):
    guild = interaction.guild
    member = guild.get_member(interaction.user.id) or await fetch_member(guild, interaction.user.id)
    if not member:
        return "I could not find your server member profile. Please try again."
    try:
        role = guild.get_role(int(role_id))
    except (TypeError, ValueError):
        return "This button is linked to an invalid role."
    if not role:
        return "This role no longer exists."
    ok, reason = bot_can_manage_role(guild, role)
    if not ok:
        return reason
    if role in member.roles:
        return f"You already have **{role.name}**."
    try:
        await await_shared_discord_permit()
        await member.add_roles(role, reason="One-time role button")
        return f"Added **{role.name}**."
    except discord.Forbidden:
        return f"I do not have permission to give **{role.name}**."
    except discord.HTTPException:
        return f"Discord could not give **{role.name}** right now. Please try again later."


async def fetch_member(guild, user_id):
    member = guild.get_member(user_id)
    if member:
        return member
    key = (str(guild.id), str(user_id))
    if MEMBER_NEGATIVE_CACHE.get(key, 0) > time.time():
        return None
    existing = MEMBER_FETCHES.get(key)
    if existing:
        RATE_COORDINATOR.increment("bot_member_fetch_merged")
        return await asyncio.shield(existing)

    async def run():
        try:
            await await_shared_discord_permit()
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            MEMBER_NEGATIVE_CACHE[key] = time.time() + 30
            return None
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[MEMBER] fetch failed for {user_id} in {guild.id}: {exc}")
            return None

    task = asyncio.create_task(run())
    MEMBER_FETCHES[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if MEMBER_FETCHES.get(key) is task:
            MEMBER_FETCHES.pop(key, None)


async def begin_bot_action(interaction, family, cooldown_seconds, resource=""):
    key = f"bot:{interaction.guild.id}:{interaction.user.id}:{family}:{resource}"
    async with BOT_ACTION_LOCK:
        if key in BOT_ACTION_INFLIGHT:
            RATE_COORDINATOR.increment("bot_inflight_rejected")
            return None, 1
        allowed, retry_after = RATE_COORDINATOR.acquire(key, cooldown_seconds)
        if not allowed:
            RATE_COORDINATOR.increment("bot_cooldown_rejected")
            return None, retry_after
        BOT_ACTION_INFLIGHT.add(key)
    return key, 0


async def finish_bot_action(key):
    if not key:
        return
    async with BOT_ACTION_LOCK:
        BOT_ACTION_INFLIGHT.discard(key)


def emoji_key(emoji: discord.PartialEmoji) -> str:
    """
    Return a consistent string key for an emoji.
    Unicode emoji  -> the character itself, e.g. "🎮"
    Custom emoji   -> "<:name:id>" or "<a:name:id>" for animated
    This must match whatever the GUI / slash commands store in config mappings.
    """
    if emoji.id:
        prefix = "a" if emoji.animated else ""
        return f"<{prefix}:{emoji.name}:{emoji.id}>"
    return str(emoji)


def emoji_name_from_text(value):
    raw = value.strip()
    if raw.startswith(":") and raw.endswith(":") and len(raw) > 2:
        return raw[1:-1].lower()
    return ""


def resolve_guild_emoji(guild, value):
    raw = value.strip()
    target = emoji_name_from_text(raw)
    if not target:
        return raw
    for emoji in guild.emojis:
        if emoji.name.lower() == target:
            prefix = "a" if emoji.animated else ""
            return f"<{prefix}:{emoji.name}:{emoji.id}>"
    return raw


def build_embed(title, description, color_name="blurple", footer=None):
    color = COLOR_MAP.get(str(color_name).lower(), COLOR_MAP["blurple"])
    embed = discord.Embed(title=title or None, description=description or None, color=color)
    if footer:
        embed.set_footer(text=footer)
    return embed


def first_non_empty_line(value):
    for line in str(value or "").splitlines():
        clean = line.strip()
        if clean:
            return clean
    return ""


def next_case_id(config, guild_id):
    cases = config.get("moderation_cases", {}).get(str(guild_id), [])
    max_seen = 0
    for item in cases:
        raw = str(item.get("case_id", "")).removeprefix("CASE-")
        if raw.isdigit():
            max_seen = max(max_seen, int(raw))
    return f"CASE-{max_seen + 1:04d}"


def next_ticket_id(config, guild_id):
    tickets = config.get("tickets", {}).get(str(guild_id), [])
    max_seen = 0
    for item in tickets:
        raw = str(item.get("ticket_id", "")).removeprefix("TICKET-")
        if raw.isdigit():
            max_seen = max(max_seen, int(raw))
    return f"TICKET-{max_seen + 1:04d}"


def ticket_log_embed(ticket):
    description = (
        f"User: <@{ticket.get('user_id')}> ({ticket.get('user_id')})\n"
        f"Status: {ticket.get('status')}\n"
        f"Channel: <#{ticket.get('channel_id')}>\n\n"
        f"{ticket.get('content')}"
    )[:4096]
    embed = discord.Embed(
        title=f"{ticket.get('ticket_id')} · {ticket.get('subject')}",
        description=description,
        color=DISPLAY_COLOR_MAP["Blurple"],
    )
    embed.set_footer(text=f"Submitted by {ticket.get('user_display')}")
    return embed


async def send_ticket_log(guild, ticket, channel_id):
    channel = guild.get_channel(int(channel_id)) if str(channel_id or "").isdigit() else None
    if not channel:
        return
    try:
        await await_shared_discord_permit()
        await channel.send(embed=ticket_log_embed(ticket), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        print(f"[TICKET] Could not send ticket log: {exc}")


def create_moderation_case(ctx, member, action, reason, rule_number="", severity="normal", evidence_url="", notes="", status="open"):
    config = load_config()
    case = {
        "case_id": next_case_id(config, ctx.guild.id),
        "guild_id": str(ctx.guild.id),
        "target_user_id": str(member.id),
        "target_display": member.display_name,
        "rule_number": str(rule_number or ""),
        "violation_type": str(action or ""),
        "severity": severity if severity in ("normal", "serious", "red_line") else "normal",
        "action": action,
        "reason": str(reason or "").strip(),
        "evidence_url": str(evidence_url or "").strip(),
        "notes": str(notes or "").strip(),
        "status": status,
        "actor": str(ctx.author),
        "ts": int(time.time()),
    }
    append_moderation_case(ctx.guild.id, case)
    return case


async def send_moderation_case_log(ctx, case):
    await send_moderation_case_log_for_guild(ctx.guild, case)


async def send_moderation_case_log_for_guild(guild, case):
    settings = load_config().get("moderation_settings", {}).get(str(guild.id), {})
    channel_id = str(settings.get("log_channel_id") or "")
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if not channel:
        return
    embed = discord.Embed(
        title=f"Moderation {case.get('case_id')}",
        description=(
            f"Target: <@{case.get('target_user_id')}>\n"
            f"Action: {case.get('action')}\n"
            f"Rule: {case.get('rule_number') or 'unspecified'}\n"
            f"Severity: {case.get('severity')}\n"
            f"Status: {case.get('status')}\n\n"
            f"{case.get('reason')}"
        )[:4096],
        color=DISPLAY_COLOR_MAP["Red"] if case.get("severity") == "red_line" else DISPLAY_COLOR_MAP["Yellow"],
    )
    if case.get("evidence_url"):
        embed.add_field(name="Evidence", value=case["evidence_url"][:1024], inline=False)
    if case.get("notes"):
        embed.add_field(name="Notes", value=case["notes"][:1024], inline=False)
    embed.set_footer(text=f"Actor: {case.get('actor')}")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        print(f"[MOD] Could not send moderation log: {exc}")


async def apply_bot_moderation_action(guild, member, rule):
    action = rule.get("action") or "warning"
    reason = f"Rule {rule.get('number')}: {rule.get('name')}"
    settings = load_config().get("moderation_settings", {}).get(str(guild.id), {})
    if action == "probation":
        role_id = str(settings.get("probation_role_id") or "")
        role = guild.get_role(int(role_id)) if role_id.isdigit() else None
        if not role:
            raise ValueError("The probation role is not configured")
        ok, error = bot_can_manage_role(guild, role)
        if not ok:
            raise ValueError(error)
        await member.add_roles(role, reason=reason)
    elif action == "timeout":
        minutes = int(rule.get("timeout_minutes") or 0)
        if minutes <= 0:
            raise ValueError("Timeout minutes are not configured")
        await member.timeout_for(timedelta(minutes=minutes), reason=reason)
    elif action == "remove_role":
        role_id = str(rule.get("remove_role_id") or "")
        role = guild.get_role(int(role_id)) if role_id.isdigit() else None
        if not role:
            raise ValueError("The role to remove is not available")
        await member.remove_roles(role, reason=reason)
    return action


def moderation_draft_embed(draft, rule=None, confirm=False):
    evidence = draft.get("evidence") or {}
    description = str(evidence.get("content") or "(message has no text)")[:900]
    lines = [
        f"Target: <@{evidence.get('author_id')}> ({evidence.get('author_id')})",
        f"Evidence: {evidence.get('jump_url')}",
        "",
        description,
    ]
    attachments = evidence.get("attachments") or []
    if attachments:
        lines.append("Attachments: " + " · ".join(f"[{item.get('filename')}]({item.get('url')})" for item in attachments[:5]))
    if rule:
        lines.extend([
            "",
            f"Rule: {rule.get('number')} · {rule.get('name')}",
            f"Severity: {rule.get('severity')}",
            f"Action: {rule.get('action')}",
            f"Reason: {rule.get('reason')}",
        ])
    return discord.Embed(
        title="Confirm Moderation Case" if confirm else "Choose Moderation Rule",
        description="\n".join(lines)[:4096],
        color=DISPLAY_COLOR_MAP["Red"] if rule and rule.get("severity") == "red_line" else DISPLAY_COLOR_MAP["Yellow"],
    )


class ModerationRuleSelect(discord.ui.Select):
    def __init__(self, draft_id, rules):
        self.draft_id = str(draft_id)
        options = [
            discord.SelectOption(
                label=f"{item['number']} · {item['name']}"[:100],
                value=item["rule_id"],
                description=f"{item['severity']} · {item['action']}"[:100],
            )
            for item in rules[:25]
        ]
        super().__init__(placeholder="Choose a rule", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        draft = get_valid_moderation_draft(self.draft_id, interaction)
        if not draft:
            await interaction.response.send_message("This moderation draft expired.", ephemeral=True)
            return
        rules = normalize_moderation_rules(load_config().get("moderation_rules", {}).get(str(interaction.guild.id), []))
        rule = next((item for item in rules if item.get("enabled") and item["rule_id"] == self.values[0]), None)
        if not rule:
            await interaction.response.send_message("That rule is no longer available.", ephemeral=True)
            return
        draft["selected_rule"] = rule
        draft["status"] = "pending"
        save_moderation_draft(self.draft_id, draft)
        await interaction.response.edit_message(
            embed=moderation_draft_embed(draft, rule, confirm=True),
            view=ModerationConfirmView(self.draft_id),
        )


class ModerationRuleSelectView(discord.ui.View):
    def __init__(self, draft_id, rules):
        super().__init__(timeout=1800)
        self.add_item(ModerationRuleSelect(draft_id, rules))


def get_valid_moderation_draft(draft_id, interaction):
    draft = get_moderation_draft(draft_id, time.time())
    if not draft or draft.get("status") != "pending" or str(draft.get("moderator_id")) != str(interaction.user.id):
        return None
    return draft


class ModerationConfirmView(discord.ui.View):
    def __init__(self, draft_id):
        super().__init__(timeout=1800)
        self.draft_id = str(draft_id)

    @discord.ui.button(label="Confirm Case", style=discord.ButtonStyle.danger)
    async def confirm(self, button, interaction: discord.Interaction):
        draft = get_valid_moderation_draft(self.draft_id, interaction)
        if not draft:
            await interaction.response.send_message("This draft expired or was already handled.", ephemeral=True)
            return
        claimed = claim_moderation_draft(self.draft_id, time.time())
        if not claimed:
            await interaction.response.send_message("This draft is already being processed.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        evidence = claimed.get("evidence") or {}
        rule = claimed.get("selected_rule") or {}
        member = await fetch_member(interaction.guild, int(evidence.get("author_id") or 0))
        if not member:
            claimed["status"] = "pending"
            save_moderation_draft(self.draft_id, claimed)
            await interaction.followup.send("The message author is no longer in this server.", ephemeral=True)
            return
        try:
            action = await apply_bot_moderation_action(interaction.guild, member, rule)
            config = load_config()
            case = {
                "case_id": next_case_id(config, interaction.guild.id),
                "guild_id": str(interaction.guild.id),
                "target_user_id": str(member.id),
                "target_display": member.display_name,
                "rule_id": rule.get("rule_id", ""),
                "rule_name": rule.get("name", ""),
                "rule_number": rule.get("number", ""),
                "rule_snapshot": dict(rule),
                "violation_type": rule.get("name", ""),
                "severity": rule.get("severity", "normal"),
                "action": action,
                "reason": rule.get("reason", ""),
                "evidence_url": evidence.get("jump_url", ""),
                "evidence_snapshot": evidence,
                "notes": "Created from Discord message context command.",
                "status": "open",
                "actor": str(interaction.user),
                "ts": int(time.time()),
                "status_history": [],
            }
            append_moderation_case(interaction.guild.id, case)
            append_audit_log("created_case", "moderation", interaction.guild.id, case["case_id"], {"source": "discord_context", "target": str(member.id)}, str(interaction.user))
            await send_moderation_case_log_for_guild(interaction.guild, case)
        except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
            claimed["status"] = "pending"
            save_moderation_draft(self.draft_id, claimed)
            await interaction.followup.send(f"Case could not be created: {exc}", ephemeral=True)
            return
        delete_moderation_draft(self.draft_id)
        await interaction.edit_original_response(content=f"Case **{case['case_id']}** created.", embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button, interaction: discord.Interaction):
        draft = get_valid_moderation_draft(self.draft_id, interaction)
        if not draft:
            await interaction.response.send_message("This draft expired or was already handled.", ephemeral=True)
            return
        delete_moderation_draft(self.draft_id)
        await interaction.response.edit_message(content="Moderation case cancelled.", embed=None, view=None)


ALLOWED_MENTIONS = discord.AllowedMentions(users=True, roles=True, everyone=False)


class TicketModal(discord.ui.Modal):
    def __init__(self, guild_id):
        super().__init__(title="Open Ticket")
        self.guild_id = str(guild_id)
        self.subject = discord.ui.InputText(label="Subject", placeholder="Short title", max_length=100)
        self.content = discord.ui.InputText(
            label="Content",
            placeholder="Describe what staff should review",
            style=discord.InputTextStyle.long,
            max_length=1000,
        )
        self.add_item(self.subject)
        self.add_item(self.content)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or str(interaction.guild.id) != self.guild_id:
            await interaction.response.send_message("This ticket panel is not available here.", ephemeral=True)
            return
        config = load_config()
        settings = config.get("ticket_settings", {}).get(self.guild_id, {})
        ticket = {
            "ticket_id": next_ticket_id(config, self.guild_id),
            "guild_id": self.guild_id,
            "user_id": str(interaction.user.id),
            "user_display": getattr(interaction.user, "display_name", str(interaction.user)),
            "subject": str(self.subject.value or "").strip(),
            "content": str(self.content.value or "").strip(),
            "status": "open",
            "channel_id": str(getattr(interaction.channel, "id", "")),
            "ts": int(time.time()),
        }
        if not ticket["subject"] or not ticket["content"]:
            await interaction.response.send_message("Subject and content are required.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        action_key, retry_after = await begin_bot_action(interaction, "ticket_submit", 3, self.guild_id)
        if not action_key:
            await interaction.followup.send(f"Your ticket is already being handled. Please wait {retry_after}s.", ephemeral=True)
            return
        try:
            # Store before staff notification so the dashboard remains the source of truth.
            append_ticket(self.guild_id, ticket)
            await send_ticket_log(interaction.guild, ticket, settings.get("log_channel_id"))
            await interaction.followup.send(f"Ticket **{ticket['ticket_id']}** submitted. Staff can review it now.", ephemeral=True)
        finally:
            await finish_bot_action(action_key)


def select_emoji_value(value):
    raw = str(value).strip()
    if raw.startswith("<") and raw.endswith(">"):
        try:
            return discord.PartialEmoji.from_str(raw)
        except Exception:
            return None
    return raw or None


def build_select_options(guild, entry):
    options = []
    for emoji, role_id in list(entry.get("mappings", {}).items())[:25]:
        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            role = None
        label = role.name if role else f"Role {role_id}"
        option_kwargs = {
            "label": label[:100],
            "value": str(role_id),
            "description": f"Toggle {label}"[:100],
        }
        emoji_value = select_emoji_value(emoji)
        if emoji_value:
            option_kwargs["emoji"] = emoji_value
        options.append(discord.SelectOption(**option_kwargs))
    if not options:
        options.append(discord.SelectOption(label="No roles configured", value="none"))
    return options


class RoleSelect(discord.ui.Select):
    def __init__(self, message_id, entry, guild):
        options = build_select_options(guild, entry)
        super().__init__(
            custom_id=f"role_select:{message_id}",
            placeholder="Select your roles",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
        )
        self.message_id = str(message_id)

    async def callback(self, interaction: discord.Interaction):
        try:
            if "none" in self.values:
                await interaction.response.send_message("No roles are configured for this menu.", ephemeral=True)
                return
            config = load_config()
            entry = get_rr_entry(config, interaction.guild.id, self.message_id)
            if not entry:
                await interaction.response.send_message("This role panel is no longer configured.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            result = await apply_role_selection(interaction, entry, self.values)
            await interaction.followup.send(result, ephemeral=True)
        except Exception as exc:
            logger.exception("[RR] Persistent select failed")
            if interaction.response.is_done():
                await interaction.followup.send(f"Role update failed: {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Role update failed: {exc}", ephemeral=True)


class RoleSelectView(discord.ui.View):
    def __init__(self, message_id, entry, guild):
        super().__init__(timeout=None)
        self.add_item(RoleSelect(message_id, entry, guild))


class OnboardingAgreeView(discord.ui.View):
    def __init__(self, guild_id, language, label="Agree"):
        super().__init__(timeout=900)
        self.add_item(
            discord.ui.Button(
                label=str(label or "Agree")[:80],
                style=discord.ButtonStyle.success,
                custom_id=f"onboarding_agree:{guild_id}:{language}",
            )
        )


registered_role_views = set()


def register_role_views():
    """
    Legacy persistent-view registration kept for slash-created/old panels.
    New dashboard/GUI panels are handled immediately by on_interaction below,
    so a bot restart is no longer required after creating a dropdown panel.
    """
    config = load_config()
    registered = 0
    for guild_id, messages in config.get("reaction_roles", {}).items():
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        for message_id, entry in messages.items():
            if entry.get("mode", "dropdown") not in ("dropdown", "multi_select"):
                continue
            key = (str(guild_id), str(message_id))
            if key in registered_role_views:
                continue
            bot.add_view(RoleSelectView(str(message_id), entry, guild), message_id=int(message_id))
            registered_role_views.add(key)
            registered += 1
    if registered:
        print(f"[RR] Registered {registered} persistent role select view(s)")


def get_onboarding_entry(config, guild_id):
    entry = config.get("onboarding", {}).get(str(guild_id), {})
    if not entry or not entry.get("enabled"):
        return None
    return entry


def onboarding_language(entry, language):
    item = (entry.get("languages") or {}).get(str(language))
    if not item or not item.get("enabled"):
        return None
    if not str(item.get("rules") or "").strip():
        return None
    return item


def selected_onboarding_language(values):
    clean_values = [str(value) for value in values if str(value)]
    return clean_values[0] if len(clean_values) == 1 else ""


def configured_language_role_ids(entry):
    role_ids = set()
    for item in (entry.get("languages") or {}).values():
        if not isinstance(item, dict) or not item.get("enabled"):
            continue
        role_id = str(item.get("language_role_id") or "")
        if role_id.isdigit():
            role_ids.add(int(role_id))
    return role_ids


def configured_onboarding_completion_role_ids(entry):
    role_ids = set(configured_language_role_ids(entry))
    for key in ("fan_role_id", "member_role_id"):
        role_id = str(entry.get(key) or "")
        if role_id.isdigit():
            role_ids.add(int(role_id))
    return role_ids


def member_onboarding_read_only(member, entry):
    """True when member already has Traveler/common fan role or any language fan role."""
    configured = configured_onboarding_completion_role_ids(entry)
    if not configured:
        return False
    return any(getattr(role, "id", None) in configured for role in getattr(member, "roles", []))


def interaction_select_values(interaction):
    data = getattr(interaction, "data", None) or {}
    if isinstance(data, dict):
        raw_values = data.get("values")
        if raw_values:
            return [str(value) for value in raw_values]
    for attr in ("values", "selected_values"):
        raw_values = getattr(interaction, attr, None)
        if raw_values:
            return [str(value) for value in raw_values]
    return []


def onboarding_role_id(entry, language):
    item = onboarding_language(entry, language)
    if not item:
        return ""
    # Language-specific fan roles are preferred. The common role remains a
    # backwards-compatible fallback for panels published before this upgrade.
    return str(item.get("language_role_id") or entry.get("fan_role_id") or entry.get("member_role_id") or "")


async def onboarding_member_and_role(guild, user_id, entry, language):
    role_id = onboarding_role_id(entry, language)
    if not role_id:
        return None, None, "This language is missing its fan role. Please contact an admin."
    try:
        role = guild.get_role(int(role_id))
    except (TypeError, ValueError):
        role = None
    if not role:
        return None, None, "The configured fan role no longer exists. Please contact an admin."
    member = guild.get_member(user_id) or await fetch_member(guild, user_id)
    if not member:
        return None, None, "I could not find your server member profile. Please try again."
    return member, role, ""


async def send_onboarding_rules(interaction, entry, language):
    item = onboarding_language(entry, language)
    if not item:
        await interaction.followup.send("This language is not available anymore.", ephemeral=True)
        return
    member, role, error = await onboarding_member_and_role(interaction.guild, interaction.user.id, entry, language)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    label = item.get("label") or language
    rules = str(item.get("rules") or "").strip()
    title_template = str(entry.get("rules_title") or "{label} Rules")
    title = title_template.replace("{label}", str(label)).replace("{language}", str(label))[:256]
    embed = discord.Embed(
        title=title or f"{label} Rules",
        description=rules[:4096],
        color=DISPLAY_COLOR_MAP.get(entry.get("rules_color"), DISPLAY_COLOR_MAP["Blurple"]),
    )
    footer = str(entry.get("rules_footer") or "").strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    # Traveler/common fan role or any language fan role => rules only, no Agree.
    read_only = member_onboarding_read_only(member, entry)
    view = None
    if not read_only:
        view = OnboardingAgreeView(interaction.guild.id, language, entry.get("agree_label") or "Agree")
    if view is None:
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def apply_onboarding_agreement(interaction, entry, language, guild=None):
    guild = guild or interaction.guild
    member, role, error = await onboarding_member_and_role(guild, interaction.user.id, entry, language)
    if error:
        return error
    if role in member.roles:
        return f"You already completed onboarding and have **{role.name}**."
    roles_to_add = [role]
    common_role_id = str(entry.get("fan_role_id") or entry.get("member_role_id") or "")
    if common_role_id.isdigit() and common_role_id != str(role.id):
        common_role = guild.get_role(int(common_role_id))
        if common_role and common_role not in member.roles:
            roles_to_add.append(common_role)
    for target_role in roles_to_add:
        ok, reason = bot_can_manage_role(guild, target_role)
        if not ok:
            return reason
    try:
        await await_shared_discord_permit()
        await member.add_roles(*roles_to_add, reason=f"Accepted {language} onboarding rules")
        names = ", ".join(f"**{target.name}**" for target in roles_to_add)
        return f"Welcome! {names} has been added."
    except discord.Forbidden:
        return f"I do not have permission to give **{role.name}**."
    except discord.HTTPException:
        return f"Discord could not update **{role.name}** right now. Please try again later."


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True

bot = discord.Bot(intents=intents)
reactionrole = bot.create_group("reactionrole", "Manage reaction role messages")


@bot.event
async def on_application_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    if isinstance(error, commands.BotMissingPermissions):
        await ctx.respond("I am missing permissions needed for that command.", ephemeral=True)
        return
    raise error


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
