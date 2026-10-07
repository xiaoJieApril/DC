"""Protected monitoring and error-report endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query

from dashboard_api import *

router = APIRouter()


@router.get("/api/observability/summary", dependencies=[Depends(require_admin)])
def get_observability_summary():
    return observability.summary()


@router.get("/api/errors", dependencies=[Depends(require_admin)])
def get_errors(
    level: str = Query("", pattern="^(|ERROR|WARN)$"),
    status: str = Query("", pattern="^(|open|in_progress|resolved)$"),
    limit: int = Query(50, ge=1, le=100),
    cursor: int | None = Query(None, ge=1),
):
    return observability.list_errors(level=level, status=status, limit=limit, cursor=cursor)


@router.patch("/api/errors/{error_id}", dependencies=[Depends(require_admin)])
def set_error_status(error_id: int, payload: ErrorStatusPayload):
    updated = observability.update_error_status(error_id, payload.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="错误记录不存在或处理状态无效")
    return updated
