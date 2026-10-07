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
import project_ai


router = APIRouter()
store.init_project_db()


class DraftPayload(BaseModel):
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


def _http_error(exc):
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/projects", dependencies=[Depends(require_admin)])
def get_projects():
    return store.list_projects()


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
        embed["fields"].append({"name": "活动时间", "value": project["draft_date"][:1024], "inline": True})
    for key, label in (("draft_illustrator", "画师"), ("draft_funding_goal", "募资目标"),
                       ("draft_minimum_donation", "最低捐款")):
        value = project.get(key, "").strip()
        if value:
            embed["fields"].append({"name": label, "value": value[:1024], "inline": True})
    donation_url = project.get("draft_donation_url", "").strip()
    if donation_url:
        embed["fields"].append({"name": "捐款方式", "value": f"[前往捐款]({donation_url})"[:1024], "inline": True})
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
