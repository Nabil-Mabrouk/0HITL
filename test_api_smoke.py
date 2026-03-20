import os
import tempfile
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.auth import auth_manager
from core.bus import event_bus
from core.runner import runner
from gateway.api import app, load_cors_settings_from_env, prepare_session, sessions


def run_cors_config_tests():
    print("Testing CORS configuration parser...")

    keys = [
        "HITL_CORS_ALLOW_ORIGINS",
        "HITL_CORS_ALLOW_METHODS",
        "HITL_CORS_ALLOW_HEADERS",
        "HITL_CORS_EXPOSE_HEADERS",
        "HITL_CORS_ALLOW_CREDENTIALS",
    ]
    previous = {key: os.environ.get(key) for key in keys}

    try:
        os.environ["HITL_CORS_ALLOW_ORIGINS"] = "https://console.example.com,https://ops.example.com"
        os.environ["HITL_CORS_ALLOW_METHODS"] = "GET,POST,OPTIONS"
        os.environ["HITL_CORS_ALLOW_HEADERS"] = "Content-Type,X-API-Key"
        os.environ["HITL_CORS_EXPOSE_HEADERS"] = "X-Request-Id"
        os.environ["HITL_CORS_ALLOW_CREDENTIALS"] = "true"

        settings = load_cors_settings_from_env()
        assert settings is not None
        assert settings["allow_origins"] == [
            "https://console.example.com",
            "https://ops.example.com",
        ]
        assert settings["allow_credentials"] is True
        assert settings["allow_headers"] == ["Content-Type", "X-API-Key"]
        print("PASS explicit CORS origins are parsed correctly.")

        os.environ["HITL_CORS_ALLOW_ORIGINS"] = "*"
        wildcard_settings = load_cors_settings_from_env()
        assert wildcard_settings is not None
        assert wildcard_settings["allow_origins"] == ["*"]
        assert wildcard_settings["allow_credentials"] is False
        print("PASS wildcard CORS disables credentials automatically.")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_smoke_tests():
    print("Testing authenticated API smoke flow...")

    sessions.clear()
    event_bus.connections.clear()
    original_auth_db_path = auth_manager.db_path

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_manager.db_path = os.path.join(temp_dir, "auth.db")

            with patch("gateway.api.engine.chat", new_callable=AsyncMock) as mock_chat, patch(
                "gateway.api.runner.shutdown_session"
            ) as mock_shutdown_session:
                async def chat_side_effect(session, user_input, profile_name="orchestrateur"):
                    del profile_name

                    if session.metadata.get("public_session_id") == "websocket-smoke":
                        await event_bus.broadcast(
                            session.session_id,
                            "THOUGHT",
                            {"content": "websocket smoke event"},
                        )
                        return "Websocket smoke response"

                    if session.metadata.get("public_session_id") == "shared-session":
                        await event_bus.broadcast(
                            session.session_id,
                            "THOUGHT",
                            {"content": "shared session event"},
                        )
                        return "Shared session response"

                    return "Smoke test response"

                mock_chat.side_effect = chat_side_effect

                with TestClient(app) as client:
                    health_response = client.get("/health")
                    assert health_response.status_code == 200
                    assert health_response.json()["status"] == "ok"
                    assert health_response.json()["active_sessions"] == 0
                    print("PASS /health responds with status ok.")

                    setup_response = client.get("/auth/setup-status")
                    assert setup_response.status_code == 200
                    assert setup_response.json()["bootstrap_required"] is True
                    print("PASS /auth/setup-status reports bootstrap required on a fresh instance.")

                    bootstrap_response = client.post(
                        "/auth/bootstrap",
                        json={
                            "username": "owner",
                            "password": "super-secret-pass",
                            "display_name": "Owner",
                        },
                    )
                    assert bootstrap_response.status_code == 200
                    bootstrap_payload = bootstrap_response.json()
                    assert bootstrap_payload["user"]["username"] == "owner"
                    assert bootstrap_payload["user"]["role"] == "owner"
                    print("PASS /auth/bootstrap creates the owner and returns an authenticated session.")

                    me_response = client.get("/auth/me")
                    assert me_response.status_code == 200
                    current_user = me_response.json()["user"]
                    assert current_user["username"] == "owner"
                    print("PASS /auth/me returns the authenticated user from the cookie session.")

                    owner_users_response = client.get("/auth/users")
                    assert owner_users_response.status_code == 200
                    assert len(owner_users_response.json()["users"]) == 1
                    print("PASS /auth/users lists the initial owner account.")

                    create_member_response = client.post(
                        "/auth/users",
                        json={
                            "username": "alice",
                            "password": "member-secret-pass",
                            "display_name": "Alice",
                            "role": "member",
                        },
                    )
                    assert create_member_response.status_code == 200
                    assert create_member_response.json()["user"]["username"] == "alice"
                    assert create_member_response.json()["user"]["role"] == "member"
                    print("PASS /auth/users lets the owner create a local member account.")

                    owner_users_after_create = client.get("/auth/users")
                    assert owner_users_after_create.status_code == 200
                    assert len(owner_users_after_create.json()["users"]) == 2
                    print("PASS /auth/users reflects newly created local accounts.")

                    member_client = TestClient(app)
                    try:
                        member_login = member_client.post(
                            "/auth/login",
                            json={"username": "alice", "password": "member-secret-pass"},
                        )
                        assert member_login.status_code == 200

                        member_users_response = member_client.get("/auth/users")
                        assert member_users_response.status_code == 403
                        print("PASS non-owner accounts cannot access owner-only account administration.")

                        grant_shared_viewer = client.post(
                            "/sessions/shared-session/permissions",
                            json={"username": "alice", "permission": "viewer"},
                        )
                        assert grant_shared_viewer.status_code == 200
                        assert grant_shared_viewer.json()["share"]["permission"] == "viewer"

                        shared_permissions = client.get("/sessions/shared-session/permissions")
                        assert shared_permissions.status_code == 200
                        assert len(shared_permissions.json()["permissions"]) == 1
                        print("PASS session owners can grant explicit viewer access to a session.")

                        _, internal_shared_sid, _ = prepare_session(current_user, "shared-session")
                        shared_file_dir = runner.get_session_files_dir(internal_shared_sid)
                        shared_file_path = os.path.join(shared_file_dir, "shared.txt")
                        os.makedirs(shared_file_dir, exist_ok=True)
                        with open(shared_file_path, "w", encoding="utf-8") as f:
                            f.write("hello from shared session")

                        shared_file_response = member_client.get(
                            "/session-files/owner:shared-session/files/shared.txt"
                        )
                        assert shared_file_response.status_code == 200
                        assert shared_file_response.text == "hello from shared session"
                        print("PASS shared viewers can read explicitly shared session artifacts.")

                        with member_client.websocket_connect("/ws/mission-control/owner:shared-session") as shared_ws:
                            shared_ws.send_text("ready")
                            shared_chat_response = client.post(
                                "/chat",
                                json={
                                    "user_input": "broadcast shared telemetry",
                                    "session_id": "shared-session",
                                },
                            )
                            assert shared_chat_response.status_code == 200
                            assert shared_chat_response.json()["response"] == "Shared session response"

                            shared_ws_payload = shared_ws.receive_json()
                            assert shared_ws_payload["type"] == "THOUGHT"
                            assert shared_ws_payload["data"]["content"] == "shared session event"
                            print("PASS shared viewers can subscribe to session telemetry.")

                        shared_chat_denied = member_client.post(
                            "/chat",
                            json={
                                "user_input": "should be denied",
                                "session_id": "owner:shared-session",
                            },
                        )
                        assert shared_chat_denied.status_code == 403

                        viewer_stop_denied = member_client.post(
                            "/sessions/owner:shared-session/emergency-stop"
                        )
                        assert viewer_stop_denied.status_code == 403
                        print("PASS viewers cannot execute or emergency-stop a shared session.")

                        upgrade_shared_operator = client.post(
                            "/sessions/shared-session/permissions",
                            json={"username": "alice", "permission": "operator"},
                        )
                        assert upgrade_shared_operator.status_code == 200
                        assert upgrade_shared_operator.json()["share"]["permission"] == "operator"

                        operator_stop = member_client.post("/sessions/owner:shared-session/emergency-stop")
                        assert operator_stop.status_code == 200
                        assert operator_stop.json()["session_id"] == "owner:shared-session"
                        mock_shutdown_session.assert_any_call(internal_shared_sid)
                        print("PASS operators can emergency-stop a shared session.")

                        revoke_shared_permission = client.delete(
                            "/sessions/shared-session/permissions/alice"
                        )
                        assert revoke_shared_permission.status_code == 200

                        revoked_shared_file = member_client.get(
                            "/session-files/owner:shared-session/files/shared.txt"
                        )
                        assert revoked_shared_file.status_code == 403
                        print("PASS revoked permissions immediately remove shared session access.")
                    finally:
                        member_client.close()

                    anonymous_client = TestClient(app)
                    try:
                        unauthorized_chat = anonymous_client.post(
                            "/chat",
                            json={"user_input": "should fail", "session_id": "anon"},
                        )
                        assert unauthorized_chat.status_code == 401
                        print("PASS /chat rejects unauthenticated access once bootstrap is complete.")
                    finally:
                        anonymous_client.close()

                    previous_chat_count = mock_chat.await_count
                    chat_response = client.post(
                        "/chat",
                        json={
                            "user_input": "hello from smoke test",
                            "session_id": "smoke-session",
                        },
                    )
                    assert chat_response.status_code == 200

                    payload = chat_response.json()
                    assert payload["session_id"] == "smoke-session"
                    assert payload["response"] == "Smoke test response"
                    print("PASS /chat returns a stable public session response.")

                    _, internal_sid, prepared_session = prepare_session(current_user, "smoke-session")
                    assert internal_sid in sessions
                    assert sessions[internal_sid].session_id == internal_sid
                    assert prepared_session.metadata["public_session_id"] == "smoke-session"
                    assert mock_chat.await_count == previous_chat_count + 1
                    print("PASS /chat scopes the in-memory session registry per authenticated user.")

                    health_after_chat = client.get("/health")
                    assert health_after_chat.status_code == 200
                    assert health_after_chat.json()["active_sessions"] == 1
                    print("PASS /health reflects active scoped sessions.")

                    smoke_file_dir = runner.get_session_files_dir(internal_sid)
                    smoke_file_path = os.path.join(smoke_file_dir, "hello.txt")
                    os.makedirs(smoke_file_dir, exist_ok=True)
                    with open(smoke_file_path, "w", encoding="utf-8") as f:
                        f.write("hello from session files")

                    file_response = client.get("/session-files/smoke-session/files/hello.txt")
                    assert file_response.status_code == 200
                    assert file_response.text == "hello from session files"
                    print("PASS /session-files serves files from the authenticated session workspace.")

                    blocked_response = client.get("/session-files/smoke-session/..%2Fpyproject.toml")
                    assert blocked_response.status_code == 403
                    print("PASS /session-files blocks path traversal outside the session root.")

                    _, internal_stop_sid, _ = prepare_session(current_user, "stop-smoke")
                    with client.websocket_connect("/ws/mission-control/stop-smoke") as websocket:
                        websocket.send_text("ready")

                        stop_response = client.post("/sessions/stop-smoke/emergency-stop")
                        assert stop_response.status_code == 200
                        assert stop_response.json()["session_id"] == "stop-smoke"
                        assert stop_response.json()["stopped"] is True
                        assert stop_response.json()["had_active_session"] is True
                        assert internal_stop_sid not in sessions
                        mock_shutdown_session.assert_any_call(internal_stop_sid)

                        stop_payload = websocket.receive_json()
                        assert stop_payload["type"] == "EMERGENCY_STOP"
                        assert stop_payload["data"]["stopped"] is True

                        runtime_payload = websocket.receive_json()
                        assert runtime_payload["type"] == "RUNTIME_STATUS"
                        assert runtime_payload["data"]["stopped"] is True
                        print("PASS emergency stop broadcasts stop state and requests runtime shutdown.")

                    with client.websocket_connect("/ws/mission-control/websocket-smoke") as websocket:
                        websocket.send_text("ready")

                        websocket_chat_response = client.post(
                            "/chat",
                            json={
                                "user_input": "trigger websocket event",
                                "session_id": "websocket-smoke",
                            },
                        )
                        assert websocket_chat_response.status_code == 200
                        assert websocket_chat_response.json()["response"] == "Websocket smoke response"

                        ws_payload = websocket.receive_json()
                        assert ws_payload["type"] == "THOUGHT"
                        assert ws_payload["data"]["content"] == "websocket smoke event"
                        print("PASS WebSocket receives broadcast events for the authenticated session.")

                    logout_response = client.post("/auth/logout")
                    assert logout_response.status_code == 200

                    post_logout_chat = client.post(
                        "/chat",
                        json={"user_input": "should fail after logout", "session_id": "smoke-session"},
                    )
                    assert post_logout_chat.status_code == 401
                    print("PASS /auth/logout revokes the cookie session.")
    finally:
        auth_manager.db_path = original_auth_db_path


if __name__ == "__main__":
    try:
        run_cors_config_tests()
        run_smoke_tests()
        print("\nAPI SMOKE TESTS PASSED!")
    except AssertionError:
        print("\nAPI SMOKE TEST FAILED!")
        raise
    finally:
        sessions.clear()
        event_bus.connections.clear()
