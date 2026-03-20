import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from core.engine import ZeroHitlEngine
from core.memory import SessionLogger
from core.models import AgentSession
from core.runner import SandboxRunResult, SessionRuntime, runner
from core.tools import tool


class FakeExecResult:
    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    def __init__(self, exec_result):
        self._exec_result = exec_result

    def exec_run(self, cmd, workdir, demux):
        del cmd, workdir, demux
        return self._exec_result


class MockFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, tool_id, name, arguments, index=0):
        self.id = tool_id
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
async def telemetry_shell():
    """Returns a fake sandbox result with runner telemetry."""
    return SandboxRunResult(
        output="runner ok",
        exit_code=0,
        telemetry={
            "cold_start": True,
            "runtime_reused": False,
            "container_start_ms": 520.0,
            "docker_exec_ms": 810.0,
            "command_wall_ms": 640.0,
            "venv_bootstrap_ms": 120.0,
        },
    )


async def test_runner_collects_sandbox_metrics():
    print("Testing runner sandbox telemetry extraction...")
    session_id = f"runner-metrics-{uuid.uuid4().hex[:8]}"
    fake_runtime = SessionRuntime(session_id=session_id, mode="offline", container_name="fake-container")
    fake_output = (
        "hello from sandbox\n"
        "__0HITL_METRIC__:venv_bootstrap_ms=24\n"
        "__0HITL_METRIC__:venv_created=false\n"
        "__0HITL_METRIC__:command_wall_ms=78\n"
        "__0HITL_METRIC__:command_exit_code=0\n"
    ).encode("utf-8")

    with patch.object(
        runner,
        "_ensure_runtime",
        return_value=(
            FakeContainer(FakeExecResult(0, fake_output)),
            fake_runtime,
            True,
            {
                "cold_start": True,
                "runtime_reused": False,
                "startup_kind": "cold_start",
                "container_start_ms": 410.0,
            },
        ),
    ), patch.object(runner, "_broadcast_runtime_status", new=AsyncMock()):
        result = await runner.run_in_sandbox("echo hello from sandbox", session_id=session_id)

    assert isinstance(result, SandboxRunResult)
    assert "hello from sandbox" in result.output
    assert "__0HITL_METRIC__" not in result.output
    assert result.telemetry["cold_start"] is True
    assert result.telemetry["runtime_reused"] is False
    assert result.telemetry["container_start_ms"] == 410.0
    assert result.telemetry["venv_bootstrap_ms"] == 24
    assert result.telemetry["command_wall_ms"] == 78
    assert result.telemetry["command_exit_code"] == 0

    records = SessionLogger(session_id).get_full_history()
    sandbox_events = [record for record in records if record.get("event_type") == "sandbox_command_completed"]
    assert sandbox_events
    assert sandbox_events[-1]["cold_start"] is True
    assert sandbox_events[-1]["docker_exec_ms"] >= 0
    print("PASS runner emits clean sandbox telemetry with cold start and command timings.")


async def test_engine_logs_runner_metrics_on_tool_completion():
    print("Testing engine-level propagation of runner telemetry...")
    session = AgentSession(session_id=f"runner-tool-{uuid.uuid4().hex[:8]}")
    engine = ZeroHitlEngine(model="gpt-4o")

    first_response = MockResponse(
        MockMessage(
            "Running sandbox telemetry tool.",
            tool_calls=[MockToolCall("call_telemetry", "telemetry_shell", "{}")],
        )
    )
    second_response = MockResponse(MockMessage("runner ok"))

    with patch("litellm.acompletion", side_effect=[first_response, second_response]):
        response = await engine.chat(session, "Run the telemetry shell helper.")

    assert response == "runner ok"
    records = SessionLogger(session.session_id).get_full_history()
    tool_events = [record for record in records if record.get("event_type") == "tool_call_completed"]
    telemetry_event = next(record for record in tool_events if record.get("tool_name") == "telemetry_shell")
    assert telemetry_event["cold_start"] is True
    assert telemetry_event["container_start_ms"] == 520.0
    assert telemetry_event["docker_exec_ms"] == 810.0
    assert telemetry_event["command_wall_ms"] == 640.0
    assert telemetry_event["venv_bootstrap_ms"] == 120.0
    print("PASS tool_call_completed includes propagated runner telemetry.")


if __name__ == "__main__":
    asyncio.run(test_runner_collects_sandbox_metrics())
    asyncio.run(test_engine_logs_runner_metrics_on_tool_completion())
