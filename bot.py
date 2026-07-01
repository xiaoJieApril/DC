import os

import discord
from discord.ext import commands
from dotenv import load_dotenv
from storage import init_db, load_config, save_config, upsert_message

load_dotenv()
init_db()

COLOR_MAP = {
    "blurple": 0x5865F2,
    "green": 0x57F287,
    "red": 0xED4245,
    "yellow": 0xFEE75C,
    "white": 0xFFFFFF,
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
    added = []
    removed = []
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

        try:
            if should_have and not has_role:
                await member.add_roles(role, reason="Dropdown role selection")
                added.append(role.name)
            elif not should_have and has_role:
                await member.remove_roles(role, reason="Dropdown role selection")
                removed.append(role.name)
        except discord.Forbidden:
            skipped.append(f"{role.name}: missing permission")
        except discord.HTTPException as exc:
            skipped.append(f"{role.name}: {exc}")

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
        await member.add_roles(role, reason="One-time role button")
        return f"Added **{role.name}**."
    except discord.Forbidden:
        return f"I do not have permission to give **{role.name}**."
    except discord.HTTPException as exc:
        return f"Could not give **{role.name}**: {exc}"


async def fetch_member(guild, user_id):
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


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


ALLOWED_MENTIONS = discord.AllowedMentions(users=True, roles=True, everyone=False)


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
    def __init__(self, guild_id, language):
        super().__init__(timeout=900)
        self.add_item(
            discord.ui.Button(
                label="Agree",
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


async def send_onboarding_rules(interaction, entry, language):
    item = onboarding_language(entry, language)
    if not item:
        await interaction.followup.send("This language is not available anymore.", ephemeral=True)
        return
    label = item.get("label") or language
    rules = str(item.get("rules") or "").strip()
    await interaction.followup.send(
        content=f"**{label} Rules**\n\n{rules}",
        view=OnboardingAgreeView(interaction.guild.id, language),
        ephemeral=True,
    )


async def apply_onboarding_agreement(interaction, entry):
    role_id = str(entry.get("member_role_id") or "")
    if not role_id:
        return "Onboarding is missing the member role. Please contact an admin."
    try:
        role = interaction.guild.get_role(int(role_id))
    except (TypeError, ValueError):
        role = None
    if not role:
        return "The configured member role no longer exists. Please contact an admin."
    member = interaction.guild.get_member(interaction.user.id) or await fetch_member(interaction.guild, interaction.user.id)
    if not member:
        return "I could not find your server member profile. Please try again."
    if role in member.roles:
        return f"You already completed onboarding and have **{role.name}**."
    ok, reason = bot_can_manage_role(interaction.guild, role)
    if not ok:
        return reason
    try:
        await member.add_roles(role, reason="Accepted onboarding rules")
        return f"Welcome! **{role.name}** has been added."
    except discord.Forbidden:
        return f"I do not have permission to give **{role.name}**."
    except discord.HTTPException as exc:
        return f"Could not give **{role.name}**: {exc}"


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True

bot = discord.Bot(intents=intents)
reactionrole = bot.create_group("reactionrole", "Manage reaction role messages")


@bot.event
async def on_ready():
    print(f"[BOT] Online as {bot.user} (ID: {bot.user.id})")
    print(f"[BOT] Serving {len(bot.guilds)} guild(s)")
    print("[ONBOARDING] Server Members Intent is required for rules-gate role assignment")
    print("[RR] Dropdown role panels are handled by live interaction routing")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    data = getattr(interaction, "data", {}) or {}
    custom_id = str(data.get("custom_id", ""))
    is_role_interaction = custom_id.startswith("role_select:") or custom_id.startswith("role_button:")
    is_onboarding_interaction = custom_id.startswith("onboarding_language:") or custom_id.startswith("onboarding_agree:")
    if not (is_role_interaction or is_onboarding_interaction):
        return

    try:
        if not interaction.guild:
            await interaction.response.send_message("This action only works inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        config = load_config()
        if custom_id.startswith("onboarding_language:"):
            entry = get_onboarding_entry(config, interaction.guild.id)
            if not entry:
                await interaction.followup.send("Onboarding is not configured right now.", ephemeral=True)
                return
            values = [str(value) for value in data.get("values", [])]
            language = values[0] if values else ""
            await send_onboarding_rules(interaction, entry, language)
            return

        if custom_id.startswith("onboarding_agree:"):
            parts = custom_id.split(":", 2)
            language = parts[2] if len(parts) > 2 else ""
            entry = get_onboarding_entry(config, interaction.guild.id)
            if not entry or not onboarding_language(entry, language):
                await interaction.followup.send("This onboarding option is not available anymore.", ephemeral=True)
                return
            result = await apply_onboarding_agreement(interaction, entry)
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
                await interaction.followup.send(f"Action failed: {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Action failed: {exc}", ephemeral=True)
        except Exception as nested_exc:
            print(f"[INTERACTION] Could not report interaction failure: {nested_exc}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if not payload.guild_id or payload.user_id == bot.user.id:
        return

    config = load_config()
    entry = get_rr_entry(config, payload.guild_id, payload.message_id)
    if not entry:
        return

    role_id = entry.get("mappings", {}).get(emoji_key(payload.emoji))
    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(int(role_id))
    member = payload.member or await fetch_member(guild, payload.user_id)
    if not role or not member or member.bot:
        return

    ok, reason = bot_can_manage_role(guild, role)
    if not ok:
        print(f"[RR] Cannot add {role.name}: {reason}")
        return

    try:
        await member.add_roles(role, reason="Reaction role added")
        print(f"[RR] Gave {role.name} to {member.display_name}")
    except discord.Forbidden:
        print(f"[RR] Missing permission to give {role.name}")
    except discord.HTTPException as exc:
        print(f"[RR] Failed to give {role.name}: {exc}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if not payload.guild_id or payload.user_id == bot.user.id:
        return

    config = load_config()
    entry = get_rr_entry(config, payload.guild_id, payload.message_id)
    if not entry:
        return

    role_id = entry.get("mappings", {}).get(emoji_key(payload.emoji))
    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(int(role_id))
    member = await fetch_member(guild, payload.user_id)
    if not role or not member or member.bot:
        return

    ok, reason = bot_can_manage_role(guild, role)
    if not ok:
        print(f"[RR] Cannot remove {role.name}: {reason}")
        return

    try:
        await member.remove_roles(role, reason="Reaction role removed")
        print(f"[RR] Removed {role.name} from {member.display_name}")
    except discord.Forbidden:
        print(f"[RR] Missing permission to remove {role.name}")
    except discord.HTTPException as exc:
        print(f"[RR] Failed to remove {role.name}: {exc}")


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
