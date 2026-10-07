"""Dashboard API routes for discord feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/discord/guilds", dependencies=[Depends(require_admin)])
def guilds():
    return discord_cached_get("/users/@me/guilds", "guilds")


@router.get("/api/discord/guilds/{guild_id}/channels", dependencies=[Depends(require_admin)])
def channels(guild_id: str):
    result = discord_cached_get(f"/guilds/{guild_id}/channels", f"channels:{guild_id}")
    result["data"] = [item for item in result["data"] if item.get("type") in (0, 5)]
    return result


@router.get("/api/discord/guilds/{guild_id}/roles", dependencies=[Depends(require_admin)])
def roles(guild_id: str):
    result = discord_cached_get(f"/guilds/{guild_id}/roles", f"roles:{guild_id}")
    result["data"] = [item for item in result["data"] if item.get("name") != "@everyone" and not item.get("managed")]
    return result


@router.get("/api/discord/guilds/{guild_id}/members/search", dependencies=[Depends(require_admin)])
def search_members(guild_id: str, q: str = Query(..., min_length=2), limit: int = Query(10, ge=1, le=25)):
    # Search guild members for the dashboard mention picker.
    query = urllib.parse.urlencode({"query": q.strip(), "limit": limit})
    result = discord_cached_get(
        f"/guilds/{guild_id}/members/search?{query}",
        f"members:{guild_id}:{query}",
        persist=False,
        ttl=60,
    )
    data = result["data"]
    rows = []
    for item in data:
        user = item.get("user") or {}
        user_id = user.get("id")
        if not user_id:
            continue
        username = user.get("global_name") or user.get("username") or user_id
        display_name = item.get("nick") or username
        rows.append(
            {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "avatar": user.get("avatar"),
            }
        )
    result["data"] = rows
    return result


@router.get("/api/discord/guilds/{guild_id}/emojis", dependencies=[Depends(require_admin)])
def emojis(guild_id: str):
    return discord_cached_get(f"/guilds/{guild_id}/emojis", f"emojis:{guild_id}")


@router.get("/api/discord/guilds/{guild_id}/emojis/resolve", dependencies=[Depends(require_admin)])
def resolve_emoji(guild_id: str, value: str = Query(..., min_length=1)):
    return resolve_emoji_detail(guild_id, value)



