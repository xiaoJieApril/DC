"""Discord command registrations for this feature area."""
from .core import *

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



