"""Dashboard API for Gra-VT project announcement drafts."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard_api import (
    append_audit_log,
    cached_guild_channel,
    discord_request,
    require_admin,
    request_actor,
)
import project_announcements as store


router = APIRouter()
store.init_project_db()


class DraftPayload(BaseModel):
    draft_title: str = ""
    draft_body: str = ""
    draft_date: str = ""
    draft_image_url: str = ""
    draft_source_url: str = ""


class ChannelPayload(BaseModel):
    guild_id: str
    channel_id: str


class PublishPayload(BaseModel):
    guild_id: str
    channel_id: str


def _http_error(exc):
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/projects", dependencies=[Depends(require_admin)])
def get_projects():
    return store.list_projects()


@router.post("/api/projects/scrape", dependencies=[Depends(require_admin)])
def scrape_projects():
    try:
        result = store.scrape_new_projects()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**result, **store.list_projects()}


@router.patch("/api/projects/{project_id}", dependencies=[Depends(require_admin)])
def save_project_draft(project_id: int, payload: DraftPayload):
    try:
        project = store.update_draft(project_id, payload.dict())
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not project:
        raise HTTPException(status_code=404, detail="找不到该项目草稿。")
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
    if project["draft_date"]:
        embed["fields"] = [{"name": "日期", "value": project["draft_date"][:1024], "inline": True}]
    if project["draft_image_url"]:
        embed["image"] = {"url": project["draft_image_url"]}
    try:
        result = discord_request("POST", f"/channels/{payload.channel_id}/messages",
                                 {"embeds": [embed], "allowed_mentions": {"parse": []}})
    except HTTPException:
        store.release_project_publish(project_id, payload.guild_id)
        raise
    except Exception as exc:
        store.release_project_publish(project_id, payload.guild_id)
        raise HTTPException(status_code=502, detail=f"Discord 发布失败：{exc}") from exc
    if not result or not result.get("id"):
        store.release_project_publish(project_id, payload.guild_id)
        raise HTTPException(status_code=502, detail="Discord 未返回消息 ID，无法确认发布结果。")
    if not store.mark_published(project_id, payload.guild_id, payload.channel_id, result["id"]):
        raise HTTPException(status_code=409, detail="Discord 已收到消息，但本地状态更新失败。请核对频道后再处理，避免重复发送。")
    append_audit_log("sent", "project_announcements", payload.guild_id, result["id"],
                     {"channel_id": payload.channel_id, "project_id": project_id,
                      "title": project["draft_title"]}, request_actor())
    return {"message_id": result["id"], "guild_id": payload.guild_id,
            "channel_id": payload.channel_id, "status": "published"}
