"""Dashboard API routes for system feature."""
from fastapi import APIRouter
from dashboard_api import *

router = APIRouter()

@router.get("/api/health")
def health():
    # Public liveness probe. Detailed bot status is available only through the
    # authenticated observability endpoint.
    return {"ok": True}


@router.get("/api/bot/status", dependencies=[Depends(require_admin)])
def get_bot_status():
    return bot_status_payload()


@router.post("/api/bot/start", dependencies=[Depends(require_admin)])
def start_bot():
    return start_bot_process()


@router.post("/api/bot/stop", dependencies=[Depends(require_admin)])
def stop_bot():
    return stop_bot_process()


@router.post("/api/login")
def login(payload: LoginPayload, request: Request):
    require_configured_auth()
    username = env("ADMIN_USERNAME", "admin")
    password = env("ADMIN_PASSWORD")
    if secrets.compare_digest(payload.username, username) and secrets.compare_digest(payload.password, password):
        request.session["admin"] = True
        return {"ok": True, "access_token": create_access_token(username)}
    raise HTTPException(status_code=401, detail="Invalid username or password")


@router.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/api/me")
def me(request: Request):
    return {"logged_in": is_admin_request(request)}


