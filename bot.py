import asyncio
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
            print(f"[RR] Persistent select failed: {exc}")
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
    # Existing fan-role members can re-read the rules without receiving an
    # action they no longer need. New members get exactly one Agree button.
    view = None
    if role not in member.roles:
        view = OnboardingAgreeView(interaction.guild.id, language, entry.get("agree_label") or "Agree")
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
            print(f"[WELCOME] Unexpected follow-up failure: {exc}")
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
            values = [str(value) for value in data.get("values", [])]
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
        print(f"[INTERACTION] Component interaction failed: {exc}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("This action could not be completed right now. Please try again later.", ephemeral=True)
            else:
                await interaction.response.send_message("This action could not be completed right now. Please try again later.", ephemeral=True)
        except Exception as nested_exc:
            print(f"[INTERACTION] Could not report interaction failure: {nested_exc}")
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


@bot.slash_command(name="sendmessage", description="Send a plain message or embed")
@commands.has_permissions(manage_messages=True)
async def sendmessage(
    ctx,
    channel: discord.TextChannel,
    content: str,
    embed: bool = False,
    title: str = "",
    color: str = "blurple",
    footer: str = "",
):
    if not content.strip():
        await ctx.respond("Message cannot be empty.", ephemeral=True)
        return

    try:
        if embed:
            message = await channel.send(
                embed=build_embed(title, content, color, footer or None),
                allowed_mentions=ALLOWED_MENTIONS,
            )
            upsert_message(
                ctx.guild.id,
                message.id,
                {
                    "channel_id": str(channel.id),
                    "type": "embed",
                    "title": title or "Announcement",
                    "content": content,
                    "color": color,
                    "footer": footer or "",
                },
            )
        else:
            message = await channel.send(content, allowed_mentions=ALLOWED_MENTIONS)
            upsert_message(
                ctx.guild.id,
                message.id,
                {
                    "channel_id": str(channel.id),
                    "type": "plain",
                    "title": "",
                    "content": content,
                    "color": "",
                    "footer": "",
                },
            )
    except discord.Forbidden:
        await ctx.respond("I do not have permission to send messages in that channel.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await ctx.respond(f"Discord rejected the message: {exc}", ephemeral=True)
        return

    await ctx.respond(f"Sent message to {channel.mention}.", ephemeral=True)


@bot.slash_command(name="giverole", description="Give a role to a member")
@commands.has_permissions(manage_roles=True)
async def giverole(ctx, member: discord.Member, role: discord.Role):
    ok, reason = bot_can_manage_role(ctx.guild, role)
    if not ok:
        await ctx.respond(reason, ephemeral=True)
        return
    await member.add_roles(role, reason=f"Given by {ctx.author}")
    await ctx.respond(f"Gave **{role.name}** to {member.mention}.", ephemeral=True)


@bot.slash_command(name="removerole", description="Remove a role from a member")
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, role: discord.Role):
    ok, reason = bot_can_manage_role(ctx.guild, role)
    if not ok:
        await ctx.respond(reason, ephemeral=True)
        return
    await member.remove_roles(role, reason=f"Removed by {ctx.author}")
    await ctx.respond(f"Removed **{role.name}** from {member.mention}.", ephemeral=True)


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


@reactionrole.command(name="create", description="Create a reaction role message")
@commands.has_permissions(manage_roles=True)
async def reactionrole_create(
    ctx,
    channel: discord.TextChannel,
    title: str = "",
    description: str = "React below to get roles.",
):
    try:
        message = await channel.send(embed=build_embed(title, description), allowed_mentions=ALLOWED_MENTIONS)
    except discord.Forbidden:
        await ctx.respond("I do not have permission to send messages in that channel.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await ctx.respond(f"Could not create the message: {exc}", ephemeral=True)
        return

    config = load_config()
    guild_rr = ensure_guild_rr(config, ctx.guild.id)
    guild_rr[str(message.id)] = {
        "channel_id": str(channel.id),
        "title": title,
        "panel_name": first_non_empty_line(description) or "Untitled role panel",
        "description": description,
        "mode": "reaction",
        "kind": "reaction_role",
        "mappings": {},
    }
    save_config(config)
    await ctx.respond(f"Reaction role message created: `{message.id}`.", ephemeral=True)


@reactionrole.command(name="add", description="Add an emoji-role mapping")
@commands.has_permissions(manage_roles=True)
async def reactionrole_add(ctx, message_id: str, emoji: str, role: discord.Role):
    ok, reason = bot_can_manage_role(ctx.guild, role)
    if not ok:
        await ctx.respond(reason, ephemeral=True)
        return

    config = load_config()
    entry = get_rr_entry(config, ctx.guild.id, message_id)
    if not entry:
        await ctx.respond("That message is not configured. Use `/reactionrole create` first.", ephemeral=True)
        return

    channel = ctx.guild.get_channel(int(entry["channel_id"]))
    if not channel:
        await ctx.respond("The saved channel no longer exists.", ephemeral=True)
        return

    resolved_emoji = resolve_guild_emoji(ctx.guild, emoji)

    try:
        message = await channel.fetch_message(int(message_id))
        await message.add_reaction(resolved_emoji)
    except discord.NotFound:
        await ctx.respond("The reaction role message no longer exists.", ephemeral=True)
        return
    except discord.Forbidden:
        await ctx.respond("I need permission to read the message and add reactions.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await ctx.respond(f"Could not add that reaction: {exc}", ephemeral=True)
        return

    entry.setdefault("mappings", {})[resolved_emoji] = str(role.id)
    save_config(config)
    await ctx.respond(f"Mapped {resolved_emoji} to **{role.name}**.", ephemeral=True)


@reactionrole.command(name="remove", description="Remove an emoji-role mapping")
@commands.has_permissions(manage_roles=True)
async def reactionrole_remove(ctx, message_id: str, emoji: str):
    config = load_config()
    entry = get_rr_entry(config, ctx.guild.id, message_id)
    if not entry:
        await ctx.respond("That reaction role message is not configured.", ephemeral=True)
        return

    mappings = entry.setdefault("mappings", {})
    if emoji not in mappings:
        await ctx.respond("That emoji is not mapped on this message.", ephemeral=True)
        return

    mappings.pop(emoji)
    save_config(config)
    await ctx.respond(f"Removed mapping for {emoji}.", ephemeral=True)


@reactionrole.command(name="list", description="List reaction role messages")
@commands.has_permissions(manage_roles=True)
async def reactionrole_list(ctx):
    config = load_config()
    guild_rr = config.get("reaction_roles", {}).get(str(ctx.guild.id), {})
    if not guild_rr:
        await ctx.respond("No reaction role messages are configured for this server.", ephemeral=True)
        return

    lines = []
    for message_id, entry in guild_rr.items():
        mappings = entry.get("mappings", {})
        pairs = ", ".join(f"{emoji} -> <@&{role_id}>" for emoji, role_id in mappings.items()) or "no mappings"
        lines.append(f"`{message_id}` in <#{entry.get('channel_id')}>: {pairs}")

    await ctx.respond("\n".join(lines[:10]), ephemeral=True)


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
    if not token:
        print("[ERROR] DISCORD_TOKEN not found in .env")
        raise SystemExit(1)
    else:
        bot.run(token)
