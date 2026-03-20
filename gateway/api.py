import os
import re
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.auth import (
    AuthError,
    BootstrapCompletedError,
    InvalidCredentialsError,
    UsernameTakenError,
    auth_manager,
)
from core.bus import event_bus
from core.engine import ZeroHitlEngine
from core.models import (
    AuthBootstrapRequest,
    AuthCreateUserRequest,
    AuthLoginRequest,
    SessionPermissionRequest,
)
from core.runner import runner
from core.session_store import prepare_session, resolve_session_ids, sanitize_session_id, sessions
from core.telegram_connector import TelegramConnector


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv_env(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def load_cors_settings_from_env() -> dict | None:
    origins = _parse_csv_env("HITL_CORS_ALLOW_ORIGINS")
    if not origins:
        return None

    allow_credentials = _parse_bool_env("HITL_CORS_ALLOW_CREDENTIALS", default=True)
    if "*" in origins and allow_credentials:
        allow_credentials = False

    settings = {
        "allow_origins": origins,
        "allow_methods": _parse_csv_env("HITL_CORS_ALLOW_METHODS", "GET,POST,OPTIONS"),
        "allow_headers": _parse_csv_env("HITL_CORS_ALLOW_HEADERS", "Content-Type"),
        "allow_credentials": allow_credentials,
    }

    expose_headers = _parse_csv_env("HITL_CORS_EXPOSE_HEADERS")
    if expose_headers:
        settings["expose_headers"] = expose_headers

    return settings


app = FastAPI()
cors_settings = load_cors_settings_from_env()
if cors_settings:
    app.add_middleware(CORSMiddleware, **cors_settings)

engine = ZeroHitlEngine()
telegram_connector = TelegramConnector(
    engine=engine,
    session_preparer=prepare_session,
    auth=auth_manager,
)
SESSION_PERMISSION_LEVELS = {"viewer": 1, "operator": 2, "owner": 3}


class ChatReq(BaseModel):
    user_input: str
    session_id: str | None = None


def _format_session_reference(owner_username: str, public_sid: str, is_owner_session: bool) -> str:
    if is_owner_session:
        return public_sid
    return f"{owner_username}:{public_sid}"


def _parse_session_reference(requested_sid: str | None, allow_generated: bool = False) -> tuple[str | None, str]:
    raw = (requested_sid or "").strip()
    if not raw:
        if allow_generated:
            return None, sanitize_session_id(None)
        raise HTTPException(status_code=400, detail="Session ID is required.")

    if ":" in raw:
        owner_username, public_sid = raw.split(":", 1)
        try:
            normalized_owner = auth_manager.normalize_username(owner_username)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return normalized_owner, sanitize_session_id(public_sid)

    return None, sanitize_session_id(raw)


async def _resolve_session_access(
    user: dict,
    requested_sid: str | None,
    required_permission: str = "viewer",
    allow_create: bool = False,
) -> dict:
    owner_username, public_sid = _parse_session_reference(
        requested_sid,
        allow_generated=allow_create,
    )

    if required_permission not in SESSION_PERMISSION_LEVELS:
        raise HTTPException(status_code=500, detail="Invalid session permission configuration.")

    is_owner_session = owner_username is None or owner_username == user["username"]
    owner_user = user
    access_level = "owner"

    if not is_owner_session:
        owner_user = await auth_manager.get_user_by_username(owner_username)
        if owner_user is None:
            raise HTTPException(status_code=404, detail="Shared session owner not found.")

        access_level = await auth_manager.get_session_permission(
            owner_user["id"],
            public_sid,
            user["id"],
        )
        if access_level is None:
            raise HTTPException(status_code=403, detail="You do not have access to this shared session.")

    if SESSION_PERMISSION_LEVELS[access_level] < SESSION_PERMISSION_LEVELS[required_permission]:
        raise HTTPException(
            status_code=403,
            detail="You do not have the required permission for this session.",
        )

    internal_sid = resolve_session_ids(owner_user, public_sid)[1]
    session = sessions.get(internal_sid)
    if allow_create and access_level == "owner":
        public_sid, internal_sid, session = prepare_session(owner_user, public_sid)

    if session is not None:
        session.metadata.update(
            {
                "session_owner_user_id": owner_user["id"],
                "session_owner_username": owner_user["username"],
                "session_access_level": access_level,
                "auth_actor_user_id": user["id"],
                "auth_actor_username": user["username"],
            }
        )

    return {
        "public_sid": public_sid,
        "internal_sid": internal_sid,
        "session": session,
        "access_level": access_level,
        "owner_user": owner_user,
        "session_reference": _format_session_reference(
            owner_user["username"],
            public_sid,
            is_owner_session,
        ),
        "is_owner_session": is_owner_session,
    }


def _normalize_owned_session_id(user: dict, requested_sid: str | None) -> str:
    owner_username, public_sid = _parse_session_reference(requested_sid)
    if owner_username is not None and owner_username != user["username"]:
        raise HTTPException(status_code=403, detail="You can only manage permissions for your own sessions.")
    return public_sid


def _rewrite_public_file_urls(text: str | None, internal_sid: str, public_sid: str) -> str | None:
    if not text:
        return text
    return text.replace(f"/session-files/{internal_sid}/", f"/session-files/{public_sid}/")


def _apply_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key=auth_manager.cookie_name,
        value=token,
        httponly=True,
        secure=auth_manager.secure_cookie,
        samesite="lax",
        path="/",
        max_age=auth_manager.session_days * 24 * 60 * 60,
    )


def _clear_auth_cookie(response: Response):
    response.delete_cookie(
        key=auth_manager.cookie_name,
        path="/",
        secure=auth_manager.secure_cookie,
        samesite="lax",
    )


async def get_current_user(request: Request) -> dict:
    if await auth_manager.bootstrap_required():
        raise HTTPException(status_code=503, detail="Setup required. Create the owner account first.")

    token = request.cookies.get(auth_manager.cookie_name)
    user = await auth_manager.get_user_by_session_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


async def require_owner_user(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner permissions required.")
    return user


async def authenticate_websocket(websocket: WebSocket) -> dict | None:
    if await auth_manager.bootstrap_required():
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Setup required.")
        return None

    token = websocket.cookies.get(auth_manager.cookie_name)
    user = await auth_manager.get_user_by_session_token(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required.")
        return None
    return user


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "0-hitl",
        "active_sessions": len(sessions),
        "models": {
            "agent": engine.model,
            "memory": engine.memory_model,
        },
        "telegram": telegram_connector.status(),
    }


@app.get("/auth/setup-status")
async def auth_setup_status():
    return {"bootstrap_required": await auth_manager.bootstrap_required()}


@app.post("/auth/bootstrap")
async def auth_bootstrap(req: AuthBootstrapRequest, response: Response):
    try:
        user = await auth_manager.bootstrap_owner(req.username, req.password, req.display_name)
    except BootstrapCompletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token, _ = await auth_manager.create_session(user["id"])
    _apply_auth_cookie(response, token)
    return {"user": user, "bootstrap_required": False}


@app.post("/auth/login")
async def auth_login(req: AuthLoginRequest, response: Response):
    if await auth_manager.bootstrap_required():
        raise HTTPException(status_code=503, detail="Setup required. Create the owner account first.")

    try:
        user = await auth_manager.authenticate(req.username, req.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token, _ = await auth_manager.create_session(user["id"])
    _apply_auth_cookie(response, token)
    return {"user": user}


@app.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    await auth_manager.revoke_session(request.cookies.get(auth_manager.cookie_name))
    _clear_auth_cookie(response)
    return {"ok": True}


@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return {"user": user}


@app.get("/integrations/telegram")
async def telegram_integration_status(user: dict = Depends(get_current_user)):
    return {
        "telegram": {
            **telegram_connector.status(),
            "link_code_ttl_minutes": telegram_connector.link_code_ttl_minutes,
            "links": await auth_manager.list_telegram_links(user["id"]),
        }
    }


@app.post("/integrations/telegram/link-code")
async def create_telegram_link_code(user: dict = Depends(get_current_user)):
    return {
        "telegram": {
            **telegram_connector.status(),
            "link": await auth_manager.create_telegram_link_code(
                user["id"],
                ttl_minutes=telegram_connector.link_code_ttl_minutes,
            ),
        }
    }


@app.delete("/integrations/telegram/links/{chat_id}")
async def delete_telegram_link(chat_id: str, user: dict = Depends(get_current_user)):
    removed = await auth_manager.delete_telegram_link(user["id"], chat_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Telegram link not found.")
    return {"removed": True, "chat_id": str(chat_id)}


@app.get("/auth/users")
async def auth_list_users(user: dict = Depends(require_owner_user)):
    del user
    return {"users": await auth_manager.list_users()}


@app.post("/auth/users")
async def auth_create_user(req: AuthCreateUserRequest, user: dict = Depends(require_owner_user)):
    del user
    try:
        created = await auth_manager.create_user(
            req.username,
            req.password,
            display_name=req.display_name,
            role=req.role,
        )
    except UsernameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"user": created}


@app.get("/sessions/{sid}/permissions")
async def list_session_permissions(sid: str, user: dict = Depends(get_current_user)):
    public_sid = _normalize_owned_session_id(user, sid)
    return {
        "session_id": public_sid,
        "permissions": await auth_manager.list_session_permissions(user["id"], public_sid),
    }


@app.post("/sessions/{sid}/permissions")
async def grant_session_permission(
    sid: str,
    req: SessionPermissionRequest,
    user: dict = Depends(get_current_user),
):
    public_sid = _normalize_owned_session_id(user, sid)
    try:
        share = await auth_manager.grant_session_permission(
            user["id"],
            public_sid,
            req.username,
            req.permission,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"session_id": public_sid, "share": share}


@app.delete("/sessions/{sid}/permissions/{username}")
async def revoke_session_permission(sid: str, username: str, user: dict = Depends(get_current_user)):
    public_sid = _normalize_owned_session_id(user, sid)
    removed = await auth_manager.revoke_session_permission(user["id"], public_sid, username)
    if not removed:
        raise HTTPException(status_code=404, detail="Shared permission not found.")
    return {"session_id": public_sid, "removed": True}


@app.post("/sessions/{sid}/emergency-stop")
async def emergency_stop_session(sid: str, user: dict = Depends(get_current_user)):
    session_access = await _resolve_session_access(
        user,
        sid,
        required_permission="operator",
        allow_create=False,
    )
    public_sid = session_access["public_sid"]
    internal_sid = session_access["internal_sid"]
    session = sessions.pop(internal_sid, None)
    if session is not None:
        session.metadata["emergency_stop_requested"] = True

    runner.shutdown_session(internal_sid)

    timestamp = datetime.utcnow().isoformat()
    await event_bus.broadcast(
        internal_sid,
        "EMERGENCY_STOP",
        {
            "session_id": session_access["session_reference"],
            "stopped": True,
            "timestamp": timestamp,
            "message": "Emergency stop executed. Session runtime shutdown requested.",
        },
    )
    await event_bus.broadcast(
        internal_sid,
        "RUNTIME_STATUS",
        {
            **runner.runtime_status_snapshot(internal_sid),
            "created": False,
            "stopped": True,
            "timestamp": timestamp,
        },
    )

    return {
        "session_id": session_access["session_reference"],
        "stopped": True,
        "had_active_session": session is not None,
    }


@app.post("/chat")
async def chat(req: ChatReq, user: dict = Depends(get_current_user)):
    session_access = await _resolve_session_access(
        user,
        req.session_id,
        required_permission="owner",
        allow_create=True,
    )
    public_sid = session_access["public_sid"]
    internal_sid = session_access["internal_sid"]
    session = session_access["session"]
    resp = await engine.chat(session, req.user_input)
    return {
        "session_id": session_access["session_reference"],
        "response": _rewrite_public_file_urls(resp, internal_sid, public_sid),
    }


@app.websocket("/ws/mission-control/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):
    user = await authenticate_websocket(websocket)
    if user is None:
        return

    try:
        session_access = await _resolve_session_access(
            user,
            sid,
            required_permission="viewer",
            allow_create=True,
        )
    except HTTPException as exc:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc.detail))
        return

    internal_sid = session_access["internal_sid"]
    await websocket.accept()
    await event_bus.subscribe(internal_sid, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print(f"Client disconnected from session {internal_sid}")


@app.get("/session-files/{sid}/{file_path:path}")
async def get_session_file(sid: str, file_path: str, user: dict = Depends(get_current_user)):
    session_access = await _resolve_session_access(
        user,
        sid,
        required_permission="viewer",
        allow_create=False,
    )
    internal_sid = session_access["internal_sid"]
    session_root = os.path.abspath(runner.get_session_root(internal_sid))
    target_path = os.path.abspath(os.path.join(session_root, file_path))

    try:
        inside_session = os.path.commonpath([session_root, target_path]) == session_root
    except ValueError:
        inside_session = False

    if not inside_session:
        raise HTTPException(status_code=403, detail="Forbidden path")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target_path)


static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="static")
