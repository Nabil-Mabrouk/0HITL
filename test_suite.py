import asyncio
import os

from core.context import ContextManager
from core.models import Message, Role
from core.prompter import ProfileManager
from core.runtime_context import tool_runtime_context
from core.superego import RiskLevel, superego
from core.tools import registry, tool
from skills.system.tools import get_artifact_url, ls, read_file, write_file


async def test_superego():
    print("Testing SuperEgo...")
    verdict = superego.analyze_command("execute_bash", {"command": "rm -rf /"})
    assert verdict.level == RiskLevel.BLOCKED
    print("PASS SuperEgo blocked 'rm -rf /'")

    verdict = superego.analyze_command("execute_bash", {"command": "ls -la"})
    assert verdict.level == RiskLevel.SAFE
    print("PASS SuperEgo allowed 'ls -la'")


async def test_tool_registry():
    print("\nTesting Tool Registry...")

    @tool
    async def my_test_tool(param1: str, param2: int = 10):
        """A simple test tool."""
        return f"{param1} - {param2}"

    assert "my_test_tool" in registry.tools
    schema = next(s for s in registry.schemas if s["function"]["name"] == "my_test_tool")
    assert schema["function"]["parameters"]["required"] == ["param1"]
    print("PASS Tool registration and schema generation working.")


async def test_context_manager():
    print("\nTesting Context Manager...")
    cm = ContextManager(model="gpt-4o", max_tokens=100)
    messages = [
        Message(role=Role.USER, content="Hello world"),
        Message(role=Role.ASSISTANT, content="Hi there!"),
    ]
    tokens = cm.count_tokens(messages)
    assert tokens > 0
    print(f"PASS Token counting working: {tokens} tokens found.")


async def test_profile_manager():
    print("\nTesting Profile Manager...")
    pm = ProfileManager()

    with open("profiles/test_profile.md", "w", encoding="utf-8") as f:
        f.write("You are {{name}}.")

    content = pm.get_profile("test_profile", {"name": "0-HITL-Bot"})
    assert content == "You are 0-HITL-Bot."
    os.remove("profiles/test_profile.md")
    print("PASS Profile variable injection working.")


async def test_workspace_tool_guards():
    print("\nTesting Workspace Tool Guards...")

    with tool_runtime_context("test-session-a"):
        write_result = await write_file("nested/demo.txt", "hello")
        assert "written successfully" in write_result.lower()

        binary_write_result = await write_file("artifacts/demo.png", "fake-png-content")
        assert "written successfully" in binary_write_result.lower()

        read_result = await read_file("../pyproject.toml")
        assert "path must stay inside the workspace" in read_result.lower()

        ls_result = await ls("..")
        assert "path must stay inside the workspace" in ls_result.lower()

        binary_result = await read_file("artifacts/demo.png")
        assert "/session-files/test-session-a/files/artifacts/demo.png" in binary_result

        artifact_url = await get_artifact_url("demo.png")
        assert artifact_url == "/session-files/test-session-a/files/artifacts/demo.png"

    with tool_runtime_context("test-session-b"):
        missing_result = await read_file("nested/demo.txt")
        assert "not found" in missing_result.lower()

    print("PASS Workspace tools reject path traversal and isolate session workspaces.")


async def run_all_tests():
    try:
        await test_superego()
        await test_tool_registry()
        await test_context_manager()
        await test_profile_manager()
        await test_workspace_tool_guards()
        print("\nALL FOUNDATION TESTS PASSED!")
    except AssertionError:
        print("\nTEST FAILED!")
    except Exception as e:
        print(f"\nERROR DURING TESTS: {e}")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
