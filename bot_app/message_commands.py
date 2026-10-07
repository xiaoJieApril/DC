"""Discord command registrations for this feature area."""
from .core import *

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

HISTORY_WINDOW_DAYS = 90  # 3 個月回溯期，超過這個天數的案件不列入累計

STRIKE_ACTIONS = {
    1: "警告紀錄（Warning）",
    2: "警告 + 觀察期身分組（Warning + Probation）",
    3: "禁言一週（1-week timeout）",
    4: "踢出伺服器（Kick，可申訴）",
}



