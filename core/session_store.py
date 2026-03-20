import hashlib
import re
import uuid

from core.models import AgentSession


sessions: dict[str, AgentSession] = {}


def session_scope_prefix(user_id: str) -> str:
    return "u" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


def sanitize_session_id(value: str | None) -> str:
    raw = value or str(uuid.uuid4())
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-.")
    return safe or "default"


def resolve_session_ids(user: dict, requested_sid: str | None) -> tuple[str, str]:
    safe_sid = sanitize_session_id(requested_sid)
    prefix = f"{session_scope_prefix(user['id'])}--"
    if safe_sid.startswith(prefix):
        public_sid = safe_sid[len(prefix) :] or "default"
        return public_sid, safe_sid
    return safe_sid, f"{prefix}{safe_sid}"


def prepare_session(user: dict, requested_sid: str | None) -> tuple[str, str, AgentSession]:
    public_sid, internal_sid = resolve_session_ids(user, requested_sid)
    session = sessions.get(internal_sid)
    if session is None:
        session = AgentSession(session_id=internal_sid)
        sessions[internal_sid] = session

    session.metadata.update(
        {
            "auth_user_id": user["id"],
            "auth_username": user["username"],
            "auth_role": user["role"],
            "public_session_id": public_sid,
            "session_owner_user_id": user["id"],
            "session_owner_username": user["username"],
            "session_access_level": "owner",
            "auth_actor_user_id": user["id"],
            "auth_actor_username": user["username"],
        }
    )
    return public_sid, internal_sid, session
