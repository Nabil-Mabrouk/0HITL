import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.engine import ZeroHitlEngine
from core.models import AgentSession, Message, Role


class MockMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class MockChoice:
    def __init__(self, message):
        self.message = message


class MockResponse:
    def __init__(self, message):
        self.choices = [MockChoice(message)]


async def run_memory_post_session_tests():
    print("Testing post-session structured memory...")

    with tempfile.TemporaryDirectory() as temp_dir:
        engine = ZeroHitlEngine(model="gpt-4o")
        engine.ltm.db_path = os.path.join(temp_dir, "memory.db")
        engine.resilience.memory = engine.ltm

        user_metadata = {
            "auth_user_id": "user-1",
            "auth_username": "owner",
            "public_session_id": f"memory-a-{uuid.uuid4().hex[:6]}",
        }
        session = AgentSession(session_id=f"memory-internal-a-{uuid.uuid4().hex[:6]}", metadata=dict(user_metadata))

        consolidation_payload = (
            '{"summary":{"type":"summary","content":"The user worked on 0-HITL as a local-first assistant project.",'
            '"confidence":0.92,"sensitivity":"medium","expires_days":30},'
            '"items":[{"type":"preference","content":"The user prefers local-first deployments over SaaS hosting.",'
            '"confidence":0.97,"sensitivity":"medium"},'
            '{"type":"procedure","content":"Validate 0-HITL changes with Docker-based smoke tests before calling the task complete.",'
            '"confidence":0.9,"sensitivity":"medium"}]}'
        )

        with patch(
            "litellm.acompletion",
            side_effect=[
                MockResponse(MockMessage("I will remember your local-first workflow.")),
                MockResponse(MockMessage(consolidation_payload)),
            ],
        ):
            first_response = await engine.chat(
                session,
                "Please remember that I prefer local-first deployments and validate changes with Docker smoke tests.",
            )
            await engine.drain_post_session_tasks()

        assert "remember" in first_response.lower()

        memory_items = await engine.ltm.list_memory_items(user_id="user-1", limit=10)
        memory_types = {item["type"] for item in memory_items}
        assert "summary" in memory_types
        assert "preference" in memory_types
        assert "procedure" in memory_types
        print("PASS post-session consolidation stores summary, preference and procedure items.")

        next_session = AgentSession(
            session_id=f"memory-internal-b-{uuid.uuid4().hex[:6]}",
            metadata={
                "auth_user_id": "user-1",
                "auth_username": "owner",
                "public_session_id": f"memory-b-{uuid.uuid4().hex[:6]}",
            },
        )

        second_consolidation_payload = (
            '{"summary":{"type":"summary","content":"A follow-up session reused local-first memory successfully.",'
            '"confidence":0.88,"sensitivity":"medium","expires_days":30},"items":[]}'
        )

        with patch(
            "litellm.acompletion",
            side_effect=[
                MockResponse(MockMessage("We should keep using the local Docker smoke workflow.")),
                MockResponse(MockMessage(second_consolidation_payload)),
            ],
        ):
            second_response = await engine.chat(
                next_session,
                "How should we validate local changes on 0-HITL?",
            )
            await engine.drain_post_session_tasks()

        assert "docker" in second_response.lower()

        structured_memory_messages = [
            message
            for message in next_session.history
            if isinstance(message, Message)
            and message.role == Role.SYSTEM
            and message.content
            and "Relevant structured memory:" in message.content
        ]
        assert structured_memory_messages
        memory_context = structured_memory_messages[-1].content
        assert "local-first deployments" in memory_context
        assert "Docker-based smoke tests" in memory_context
        print("PASS future sessions reuse structured memory as injected context.")

        revision_session = AgentSession(
            session_id=f"memory-internal-c-{uuid.uuid4().hex[:6]}",
            metadata={
                "auth_user_id": "user-1",
                "auth_username": "owner",
                "public_session_id": f"memory-c-{uuid.uuid4().hex[:6]}",
            },
        )

        revision_payload = (
            '{"summary":{"type":"summary","content":"The user updated the hosting preference for 0-HITL.",'
            '"confidence":0.9,"sensitivity":"medium","expires_days":30},'
            '"items":[{"type":"preference","content":"The user now prefers a small private cloud deployment over a purely local-only setup.",'
            '"confidence":0.95,"sensitivity":"medium","replaces":["The user prefers local-first deployments over SaaS hosting."]}]}'
        )

        with patch(
            "litellm.acompletion",
            side_effect=[
                MockResponse(MockMessage("Understood, we will adapt the hosting strategy.")),
                MockResponse(MockMessage(revision_payload)),
            ],
        ):
            revision_response = await engine.chat(
                revision_session,
                "Update my preference: I now want a small private cloud deployment rather than strictly local-only hosting.",
            )
            await engine.drain_post_session_tasks()

        assert "hosting strategy" in revision_response.lower()

        active_preferences = await engine.ltm.list_memory_items(
            user_id="user-1",
            memory_types=["preference"],
            limit=10,
        )
        active_preference_contents = [item["content"] for item in active_preferences]
        assert "The user now prefers a small private cloud deployment over a purely local-only setup." in active_preference_contents
        assert "The user prefers local-first deployments over SaaS hosting." not in active_preference_contents
        print("PASS new structured memories can explicitly replace obsolete active preferences.")

        expired_item = await engine.ltm.upsert_memory_item(
            user_id="user-1",
            item_type="incident",
            content="A transient Docker Desktop outage on March 19 should remain only temporarily visible.",
            confidence=0.82,
            sensitivity="medium",
            source_session_id="expired-session",
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        assert expired_item["is_active"] is True

        visible_incidents = await engine.ltm.list_memory_items(
            user_id="user-1",
            memory_types=["incident"],
            limit=10,
        )
        assert all(
            item["content"] != "A transient Docker Desktop outage on March 19 should remain only temporarily visible."
            for item in visible_incidents
        )

        incident_rows = await engine.ltm.search_memory_items(
            "Docker Desktop outage transient",
            user_id="user-1",
            memory_types=["incident"],
            limit=10,
        )
        assert all(
            item["content"] != "A transient Docker Desktop outage on March 19 should remain only temporarily visible."
            for item in incident_rows
        )

        expired_item_rows = await engine.ltm.deactivate_memory_items(
            user_id="user-1",
            contents=["A transient Docker Desktop outage on March 19 should remain only temporarily visible."],
            memory_types=["incident"],
        )
        assert expired_item_rows == []
        print("PASS expired structured memories are retired automatically and excluded from retrieval.")


if __name__ == "__main__":
    asyncio.run(run_memory_post_session_tests())
