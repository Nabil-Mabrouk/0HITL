import asyncio
import os
import tempfile
import uuid
from unittest.mock import patch

from core.engine import ZeroHitlEngine
from core.model_registry import get_groq_model_catalog, resolve_runtime_model_roles
from core.models import AgentSession


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


async def test_groq_model_registry_defaults():
    print("Testing Groq model registry defaults...")

    with patch.dict(os.environ, {"GROQ_API_KEY": "test-groq-key"}, clear=False):
        resolved = resolve_runtime_model_roles()
        assert resolved["agent"] == "groq/openai/gpt-oss-20b"
        assert resolved["memory"] == "groq/openai/gpt-oss-20b"
        assert resolved["deep_reasoning"] == "groq/openai/gpt-oss-120b"
        assert resolved["coding"] == "groq/moonshotai/kimi-k2-instruct-0905"
        assert resolved["multilingual"] == "groq/qwen/qwen3-32b"
        assert resolved["vision"] == "groq/meta-llama/llama-4-scout-17b-16e-instruct"
        assert resolved["safety"] == "groq/openai/gpt-oss-safeguard-20b"

        override = resolve_runtime_model_roles(memory_model="openai/gpt-oss-120b")
        assert override["memory"] == "groq/openai/gpt-oss-120b"

        explicit_agent = resolve_runtime_model_roles(agent_model="gpt-4o")
        assert explicit_agent["agent"] == "gpt-4o"
        assert explicit_agent["memory"] == "gpt-4o"

    catalog = get_groq_model_catalog()
    assert catalog["agent"]["provider"] == "groq"
    assert "tool_use" in catalog["agent"]["capabilities"]
    assert "vision" in catalog["vision"]["capabilities"]
    print("PASS Groq model registry exposes the expected role-based defaults.")


async def test_memory_model_is_used_for_consolidation():
    print("Testing dedicated memory model usage...")

    with tempfile.TemporaryDirectory() as temp_dir:
        engine = ZeroHitlEngine(
            model="groq/openai/gpt-oss-20b",
            memory_model="groq/openai/gpt-oss-120b",
        )
        engine.ltm.db_path = os.path.join(temp_dir, "memory.db")
        engine.resilience.memory = engine.ltm

        session = AgentSession(
            session_id=f"model-registry-{uuid.uuid4().hex[:8]}",
            metadata={
                "auth_user_id": "user-1",
                "auth_username": "owner",
                "public_session_id": f"model-public-{uuid.uuid4().hex[:6]}",
            },
        )

        consolidation_payload = (
            '{"summary":{"type":"summary","content":"The user asked about the current 0-HITL model strategy.",'
            '"confidence":0.9,"sensitivity":"medium","expires_days":30},"items":[]}'
        )

        with patch(
            "litellm.acompletion",
            side_effect=[
                MockResponse(MockMessage("We now prefer Groq-backed role-based model selection.")),
                MockResponse(MockMessage(consolidation_payload)),
            ],
        ) as mocked_completion:
            response = await engine.chat(session, "Remember that Groq should be the preferred provider for 0-HITL.")
            await engine.drain_post_session_tasks()

        assert "groq" in response.lower()
        assert mocked_completion.call_args_list[0].kwargs["model"] == "groq/openai/gpt-oss-20b"
        assert mocked_completion.call_args_list[1].kwargs["model"] == "groq/openai/gpt-oss-120b"
        print("PASS post-session consolidation uses the dedicated memory model.")


if __name__ == "__main__":
    asyncio.run(test_groq_model_registry_defaults())
    asyncio.run(test_memory_model_is_used_for_consolidation())
