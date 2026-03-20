import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, patch

from core.auth import LocalAuthManager
from core.models import AgentSession
from core.telegram_connector import TelegramConnector


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def chat(self, session: AgentSession, user_input: str) -> str:
        self.calls.append(
            {
                "session_id": session.session_id,
                "public_session_id": session.metadata.get("public_session_id"),
                "user_input": user_input,
            }
        )
        return f"Final response: {user_input}"


async def run_telegram_connector_tests():
    print("Testing Telegram connector...")

    with tempfile.TemporaryDirectory() as tempdir:
        auth_db = os.path.join(tempdir, "auth.db")
        auth = LocalAuthManager(db_path=auth_db)
        user = await auth.bootstrap_owner("alice", "supersecurepass", display_name="Alice")
        link_code = await auth.create_telegram_link_code(user["id"], ttl_minutes=15)

        engine = FakeEngine()
        local_sessions: dict[str, AgentSession] = {}

        def prepare_session(local_user: dict, requested_sid: str | None):
            public_sid = requested_sid or f"telegram-{len(local_sessions) + 1}"
            internal_sid = f"{local_user['id']}--{public_sid}"
            session = local_sessions.get(internal_sid)
            if session is None:
                session = AgentSession(session_id=internal_sid)
                local_sessions[internal_sid] = session
            session.metadata.update(
                {
                    "auth_user_id": local_user["id"],
                    "auth_username": local_user["username"],
                    "public_session_id": public_sid,
                }
            )
            return public_sid, internal_sid, session

        connector = TelegramConnector(engine=engine, session_preparer=prepare_session, auth=auth)
        connector.enabled = True
        connector.bot_token = "test-bot-token"

        sent_messages = []

        async def capture_send(chat_id: str, text: str):
            sent_messages.append((str(chat_id), text))

        with patch.object(connector, "_send_message", side_effect=capture_send), patch.object(
            connector,
            "_send_chat_action",
            new=AsyncMock(),
        ):
            await connector._process_message(
                {
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 999, "username": "alice_tg"},
                    "text": f"/link {link_code['code']}",
                }
            )
            assert "linked to local user 'alice'" in sent_messages[-1][1]
            stored_link = await auth.get_telegram_link_by_chat_id("12345")
            assert stored_link is not None
            assert stored_link["user"]["id"] == user["id"]
            print("PASS /link associates a Telegram chat with a local user.")

            await connector._process_message(
                {
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 999, "username": "alice_tg"},
                    "text": "/new",
                }
            )
            stored_link = await auth.get_telegram_link_by_chat_id("12345")
            assert stored_link["default_session_id"].startswith("telegram-")
            print("PASS /new creates and stores a default Telegram session.")

            await connector._process_message(
                {
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 999, "username": "alice_tg"},
                    "text": "Remember this as a final-only response.",
                }
            )
            assert engine.calls[-1]["user_input"] == "Remember this as a final-only response."
            assert sent_messages[-1][1] == "Final response: Remember this as a final-only response."
            print("PASS normal messages are routed to the engine and only the final response is sent back.")

            await connector._process_message(
                {
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 999, "username": "alice_tg"},
                    "text": "/whoami",
                }
            )
            assert "Linked user: alice" in sent_messages[-1][1]
            print("PASS /whoami reports the linked local account and session.")

            await connector._process_message(
                {
                    "chat": {"id": 67890, "type": "private"},
                    "from": {"id": 111, "username": "stranger"},
                    "text": "Hello?",
                }
            )
            assert "not linked yet" in sent_messages[-1][1]
            print("PASS unlinked chats are asked to link first.")


if __name__ == "__main__":
    asyncio.run(run_telegram_connector_tests())
