import asyncio
import os
import uuid
from unittest.mock import patch

from core.engine import ZeroHitlEngine
from core.memory import SessionLogger
from core.models import AgentSession
from core.runner import runner
from core.tools import tool


class MockFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, id, name, arguments, index=0):
        self.id = id
        self.index = index
        self.function = MockFunction(name, arguments)


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


@tool
async def get_weather(city: str):
    """Gets the current weather."""
    return f"It is sunny in {city}."


async def test_engine_loop_mock():
    print("Testing Engine Reasoning Loop (Mocked LLM)...")
    engine = ZeroHitlEngine(model="gpt-4o")
    session = AgentSession(session_id="test-session")

    tc = MockToolCall("call_123", "get_weather", '{"city": "Paris"}')
    msg1 = MockMessage("I'll check the weather.", tool_calls=[tc])
    resp1 = MockResponse(msg1)

    msg2 = MockMessage("The weather in Paris is sunny.")
    resp2 = MockResponse(msg2)

    with patch("litellm.acompletion", side_effect=[resp1, resp2]):
        response = await engine.chat(session, "What's the weather in Paris?")

        print(f"Mocked Response: {response}")
        assert "sunny" in response
        assert len(session.history) >= 6
        print("PASS Reasoning loop correctly executed tool call and integrated result.")


async def test_engine_appends_artifact_url():
    print("Testing Engine Artifact URL Delivery...")
    engine = ZeroHitlEngine(model="gpt-4o")
    session = AgentSession(session_id="artifact-test-session")

    artifact_dir = os.path.join(runner.get_session_files_dir(session.session_id), "artifacts")
    os.makedirs(artifact_dir, exist_ok=True)
    artifact_path = os.path.join(artifact_dir, "bitcoin_price_24h.png")
    with open(artifact_path, "wb") as f:
        f.write(b"fake-image")

    response_text = (
        "The Bitcoin price chart has been successfully generated and saved as "
        "`bitcoin_price_24h.png` in the `artifacts` directory. The task is now complete."
    )

    with patch("litellm.acompletion", return_value=MockResponse(MockMessage(response_text))):
        response = await engine.chat(
            session,
            "provide me with the link to the bitcoin_price_24h.png file that you created",
        )

    assert "/session-files/artifact-test-session/files/artifacts/bitcoin_price_24h.png" in response
    print("PASS Engine appended the artifact URL when the model forgot it.")


async def test_session_jsonl_includes_timing_metrics():
    print("Testing Session JSONL timing metrics...")
    engine = ZeroHitlEngine(model="gpt-4o")
    session = AgentSession(session_id=f"timing-test-{uuid.uuid4().hex[:8]}")

    tc = MockToolCall("call_timing_1", "get_weather", '{"city": "Paris"}')
    first_response = MockResponse(MockMessage("Checking the weather.", tool_calls=[tc]))
    second_response = MockResponse(MockMessage("The weather in Paris is sunny."))

    with patch("litellm.acompletion", side_effect=[first_response, second_response]):
        response = await engine.chat(session, "What's the weather in Paris?")

    assert "sunny" in response.lower()

    records = SessionLogger(session.session_id).get_full_history()
    event_records = [record for record in records if record.get("record_type") == "event"]
    event_types = {record.get("event_type") for record in event_records}

    assert "mission_started" in event_types
    assert "mission_completed" in event_types
    assert "subtask_completed" in event_types
    assert "tool_call_completed" in event_types
    assert "llm_call_completed" in event_types

    mission_completed = next(record for record in event_records if record.get("event_type") == "mission_completed")
    assert mission_completed["status"] == "success"
    assert mission_completed["duration_ms"] >= 0
    assert mission_completed["llm_calls"] >= 2
    assert mission_completed["tool_calls"] >= 1

    llm_events = [record for record in event_records if record.get("event_type") == "llm_call_completed"]
    assert len(llm_events) >= 2
    assert all(record["duration_ms"] >= 0 for record in llm_events)

    tool_event = next(record for record in event_records if record.get("event_type") == "tool_call_completed")
    assert tool_event["tool_name"] == "get_weather"
    assert tool_event["duration_ms"] >= 0

    subtask_events = [record for record in event_records if record.get("event_type") == "subtask_completed"]
    assert subtask_events
    assert all(record["duration_ms"] >= 0 for record in subtask_events)
    print("PASS Session JSONL now includes mission, LLM, tool and subtask timing metrics.")


if __name__ == "__main__":
    asyncio.run(test_engine_loop_mock())
    asyncio.run(test_engine_appends_artifact_url())
    asyncio.run(test_session_jsonl_includes_timing_metrics())
