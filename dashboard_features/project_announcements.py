"""Dashboard API for Gra-VT project announcement drafts."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from dashboard_api import (
    append_audit_log,
    cached_guild_channel,
    discord_request,
    require_admin,
    request_actor,
)
from announcement_types import ANNOUNCEMENT_TYPES
from observability import observability
import project_announcements as store
import project_ai


router = APIRouter()
store.init_project_db()
COMMUNITY_NEW_ROLE_ID = "1515369580900388884"


class DraftPayload(BaseModel):
    announcement_type: str | None = None
    draft_extra: str | None = None
    draft_title: str = ""
    draft_body: str = ""
    draft_date: str = ""
    draft_image_url: str = ""
    draft_source_url: str = ""
    draft_illustrator: str = ""
    draft_funding_goal: str = ""
    draft_minimum_donation: str = ""
    draft_donation_url: str = ""


class AISettingsPayload(BaseModel):
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    clear_api_key: bool = False


class ChannelPayload(BaseModel):
    guild_id: str
    channel_id: str


class PublishPayload(BaseModel):
    guild_id: str
    channel_id: str


class ResetPublishPayload(BaseModel):
    guild_id: str
    channel_id: str
    checked_channel: bool = False


class RefreshPayload(BaseModel):
    project_ids: list[int] = []
    new_urls: list[str] | None = None


def _http_error(exc):
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/projects", dependencies=[Depends(require_admin)])
def get_projects():
    return store.list_projects()


@router.get("/api/projects/types", dependencies=[Depends(require_admin)])
def get_announcement_types():
    return {"types": ANNOUNCEMENT_TYPES}


@router.get("/api/projects/ai-settings", dependencies=[Depends(require_admin)])
def get_project_ai_settings():
    return project_ai.get_settings()


@router.put("/api/projects/ai-settings", dependencies=[Depends(require_admin)])
def save_project_ai_settings(payload: AISettingsPayload):
    try:
        return project_ai.update_settings(payload.dict())
    except ValueError as exc:
        raise _http_error(exc) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法保存服务器 AI 设置：{exc}") from exc


@router.post("/api/projects/scrape", dependencies=[Depends(require_admin)])
def scrape_projects(payload: RefreshPayload, request: Request):
    try:
        result = store.scrape_new_projects(payload.project_ids, payload.new_urls)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        observability.record_error(
            "ERROR", "project_announcements", "POST /api/projects/scrape", exc
        )
        request.state.observability_error_recorded = True
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**result, **store.list_projects()}


@router.get("/api/projects/discover", dependencies=[Depends(require_admin)])
def discover_projects(request: Request):
    try:
        return {"projects": store.discover_projects()}
    except Exception as exc:
        observability.record_error(
            "ERROR", "project_announcements", "GET /api/projects/discover", exc
        )
        request.state.observability_error_recorded = True
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/api/projects/{project_id}", dependencies=[Depends(require_admin)])
def delete_project(project_id: int):
    try:
        deleted = store.delete_project_draft(project_id)
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到该项目草稿。")
    append_audit_log("deleted", "project_announcements", "", str(project_id), {"project_id": project_id}, request_actor())
    return {"id": project_id, "deleted": True}


@router.delete("/api/projects/{project_id}/publications/{guild_id}", dependencies=[Depends(require_admin)])
def delete_project_publication(project_id: int, guild_id: str):
    if not guild_id.isdigit():
        raise HTTPException(status_code=400, detail="请选择有效的 Discord 服务器。")
    publication = store.get_project_publication(project_id, guild_id)
    if not publication or publication["status"] != "published":
        raise HTTPException(status_code=404, detail="找不到该服务器上的已发布公告。")
    channel_id = str(publication.get("channel_id") or "")
    message_id = str(publication.get("discord_message_id") or "")
    if not channel_id.isdigit() or not message_id.isdigit():
        raise HTTPException(status_code=409, detail="发布记录缺少 Discord 消息 ID，无法安全删除。")
    # A 404 means the message was already removed in Discord (for example,
    # manually by a moderator). Clear the stale local claim in that case too.
    discord_message_missing = False
    try:
        discord_request("DELETE", f"/channels/{channel_id}/messages/{message_id}")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        discord_message_missing = True
    deleted = store.delete_project_publication(project_id, guild_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="Discord 消息已删除，但本地发布记录未能更新。请刷新公告列表。")
    append_audit_log("deleted", "project_announcements", guild_id, message_id,
                     {"project_id": project_id, "channel_id": channel_id,
                      "deleted_discord": not discord_message_missing,
                      "discord_message_missing": discord_message_missing}, request_actor())
    return {"project_id": project_id, "guild_id": guild_id, "message_id": message_id,
            "deleted": True, "discord_message_missing": discord_message_missing}


@router.patch("/api/projects/{project_id}", dependencies=[Depends(require_admin)])
def save_project_draft(project_id: int, payload: DraftPayload):
    try:
        project = store.update_draft(project_id, payload.dict())
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not project:
        raise HTTPException(status_code=404, detail="找不到该项目草稿。")
    return next(item for item in store.list_projects()["projects"] if item["id"] == project_id)


@router.post("/api/projects/{project_id}/summarize", dependencies=[Depends(require_admin)])
def summarize_project_draft(project_id: int):
    settings = project_ai.get_settings()
    if not settings["enabled"] or not settings["api_key_configured"]:
        raise HTTPException(status_code=400, detail="请先在 Dashboard 设置中配置并启用 AI 总结。")
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="找不到该项目草稿。")
    try:
        result = project_ai.summarize_project({
            "title": project["title"], "event_date": project["event_date"], "image_url": project["image_url"],
            "illustrator": project["illustrator"], "funding_goal": project["funding_goal"],
            "minimum_donation": project["minimum_donation"], "donation_url": project["donation_url"],
            "source_text": project["description"],
        })
        store.update_draft(project_id, {
            "draft_title": result["activity_name"] or project["draft_title"] or project["title"],
            "draft_body": result["summary"] or project["draft_body"] or project["description"],
            "draft_date": result["date"] or project["draft_date"] or project["event_date"],
            "draft_image_url": result["image_url"] or project["draft_image_url"] or project["image_url"],
            "draft_source_url": project["draft_source_url"] or project["url"],
            "draft_illustrator": result["illustrator"] or project["draft_illustrator"] or project["illustrator"],
            "draft_funding_goal": result["funding_goal"] or project["draft_funding_goal"] or project["funding_goal"],
            "draft_minimum_donation": result["minimum_donation"] or project["draft_minimum_donation"] or project["minimum_donation"],
            "draft_donation_url": result["donation_url"] or project["draft_donation_url"] or project["donation_url"],
        })
        store.set_ai_status(project_id, "summarized")
    except Exception as exc:
        store.set_ai_status(project_id, "failed", str(exc))
        raise HTTPException(status_code=502, detail=f"AI 总结失败：{exc}") from exc
    return next(item for item in store.list_projects()["projects"] if item["id"] == project_id)


@router.put("/api/projects/settings/channel", dependencies=[Depends(require_admin)])
def save_project_channel(payload: ChannelPayload):
    if not payload.guild_id.isdigit() or not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="请选择有效的服务器和频道。")
    try:
        cached_guild_channel(payload.guild_id, payload.channel_id)
    except HTTPException:
        raise
    store.set_announcement_channel(payload.guild_id, payload.channel_id)
    append_audit_log("updated", "project_announcements", payload.guild_id, payload.channel_id,
                     {"channel_id": payload.channel_id}, request_actor())
    return {"guild_id": payload.guild_id, "channel_id": payload.channel_id}


@router.post("/api/projects/{project_id}/publish", dependencies=[Depends(require_admin)])
def publish_project(project_id: int, payload: PublishPayload):
    if not payload.guild_id.isdigit() or not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="请选择服务器和公告频道。")
    project = None
    try:
        cached_guild_channel(payload.guild_id, payload.channel_id)
        project = store.publish_project(project_id, payload.guild_id, payload.channel_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not project:
        raise HTTPException(status_code=404, detail="找不到该项目草稿。")

    embed = {
        "title": project["draft_title"],
        "description": project["draft_body"],
        "color": 0x087F70,
    }
    if project["draft_source_url"]:
        embed["url"] = project["draft_source_url"]
    embed["fields"] = []
    if project["draft_date"]:
        embed["fields"].append({"name": "Event Time", "value": project["draft_date"][:1024], "inline": True})
    for key, label in (("draft_illustrator", "Illustrator"), ("draft_funding_goal", "Funding Goal"),
                       ("draft_minimum_donation", "Minimum Donation")):
        value = project.get(key, "").strip()
        if value:
            embed["fields"].append({"name": label, "value": value[:1024], "inline": True})
    donation_url = project.get("draft_donation_url", "").strip()
    if donation_url:
        embed["fields"].append({"name": "Donation", "value": f"[Donate]({donation_url})"[:1024], "inline": True})
    if project["draft_image_url"]:
        embed["image"] = {"url": project["draft_image_url"]}
    role_ping_sent = False
    try:
        discord_request("POST", f"/channels/{payload.channel_id}/messages",
                        {"content": f"<@&{COMMUNITY_NEW_ROLE_ID}>",
                         "allowed_mentions": {"parse": [], "roles": [COMMUNITY_NEW_ROLE_ID]}})
        role_ping_sent = True
        result = discord_request("POST", f"/channels/{payload.channel_id}/messages",
                                 {"embeds": [embed], "allowed_mentions": {"parse": []}})
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code", "")
        # Requests that failed before an upstream response are ambiguous. Keep
        # the claim to prevent a retry from duplicating a message Discord got.
        ambiguous = code in {"discord_unreachable", "discord_invalid_response"} or (
            500 <= exc.status_code <= 599 and code not in {
                "discord_circuit_open", "discord_upstream_busy", "discord_auth_paused", "discord_shared_budget",
            }
        )
        if not ambiguous and not role_ping_sent:
            store.release_project_publish(project_id, payload.guild_id)
        raise
    except Exception as exc:
        # An unexpected transport failure may occur after Discord accepted it.
        raise HTTPException(status_code=502, detail=f"Discord 发布失败：{exc}") from exc
    if not result or not result.get("id"):
        raise HTTPException(status_code=502, detail="Discord 未返回消息 ID，无法确认发布结果。")
    if not store.mark_published(project_id, payload.guild_id, payload.channel_id, result["id"], embed):
        raise HTTPException(status_code=409, detail="Discord 已收到消息，但本地状态更新失败。请核对频道后再处理，避免重复发送。")
    append_audit_log("sent", "project_announcements", payload.guild_id, result["id"],
                     {"channel_id": payload.channel_id, "project_id": project_id,
                      "title": project["draft_title"]}, request_actor())
    return {"message_id": result["id"], "guild_id": payload.guild_id,
            "channel_id": payload.channel_id, "status": "published"}


@router.post("/api/projects/{project_id}/publish/reset", dependencies=[Depends(require_admin)])
def reset_project_publish_claim(project_id: int, payload: ResetPublishPayload):
    if not payload.checked_channel:
        raise HTTPException(status_code=400, detail="Check the configured Discord channel for an existing announcement before resetting this claim.")
    if not payload.guild_id.isdigit() or not payload.channel_id.isdigit():
        raise HTTPException(status_code=400, detail="Select a valid server and announcement channel.")
    try:
        cached_guild_channel(payload.guild_id, payload.channel_id)
    except HTTPException:
        raise
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project draft not found.")
    publication = store.get_project_publication(project_id, payload.guild_id)
    if publication and publication["status"] == "published":
        raise HTTPException(status_code=409, detail="This project already has a completed publication in that server.")
    if publication and publication["channel_id"] != payload.channel_id:
        raise HTTPException(status_code=409, detail="The selected channel does not match the channel recorded on the stale claim.")
    if not store.reset_stale_project_publish(project_id, payload.guild_id):
        raise HTTPException(status_code=409, detail="No stale publish claim is available to reset.")
    return {"id": project_id, "status": "claim_reset"}
