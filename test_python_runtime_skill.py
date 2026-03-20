import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

from core.runner import SandboxRunResult


def _load_python_runtime_tools_module():
    module_name = "test_python_runtime_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "python_runtime", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_python_runtime_skill_tests():
    print("Testing python_runtime skill...")
    python_tools = _load_python_runtime_tools_module()

    with tempfile.TemporaryDirectory() as tempdir:
        script_path = os.path.join(tempdir, "scripts", "hello.py")
        csv_path = os.path.join(tempdir, "data.csv")
        os.makedirs(os.path.dirname(script_path), exist_ok=True)

        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write("print('hello from file')\n")

        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            handle.write("name,score,city\nAlice,10,Paris\nBob,14,Lyon\nCharlie,9,Paris\n")

        fake_runner_result = SandboxRunResult(
            output="script ok",
            exit_code=0,
            telemetry={"cold_start": False, "runtime_reused": True, "docker_exec_ms": 42.0},
        )

        with patch.object(python_tools, "_get_workspace_root", return_value=tempdir), patch.object(
            python_tools.runner,
            "run_in_sandbox",
            new=AsyncMock(return_value=fake_runner_result),
        ) as mocked_run:
            run_result = await python_tools.run_python(
                "print('hello from snippet')",
                args_json='["--city", "Paris"]',
            )
            assert "Executed: .python_runtime/" in run_result.output
            assert "script ok" in run_result.output
            assert run_result.telemetry["docker_exec_ms"] == 42.0
            command = mocked_run.await_args_list[0].args[0]
            assert command.startswith("python -u .python_runtime/")
            assert "--city" in command
            print("PASS run_python writes a snippet and executes it in the sandbox.")

            file_result = await python_tools.run_python_file("scripts/hello.py", args_json='["--verbose"]')
            assert "Executed: scripts/hello.py" in file_result.output
            file_command = mocked_run.await_args_list[1].args[0]
            assert file_command == "python -u scripts/hello.py --verbose"
            print("PASS run_python_file executes an existing workspace script.")

            csv_result = await python_tools.inspect_csv("data.csv", max_rows=2)
            assert "Rows: 3" in csv_result
            assert "- score: numeric" in csv_result
            assert '"name": "Alice"' in csv_result
            assert '"name": "Bob"' in csv_result
            print("PASS inspect_csv reports schema, stats and sample rows.")

            invalid_args = await python_tools.run_python("print('bad')", args_json='{"oops": true}')
            assert "args_json must be a JSON array" in invalid_args
            print("PASS run_python rejects invalid args_json payloads.")


if __name__ == "__main__":
    asyncio.run(run_python_runtime_skill_tests())
