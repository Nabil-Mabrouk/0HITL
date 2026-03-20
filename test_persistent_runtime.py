import asyncio
import uuid

from core.runner import runner
from core.runtime_context import tool_runtime_context
from skills.system.tools import execute_bash, write_file


async def test_persistent_runtime():
    session_id = f"persistent-{uuid.uuid4().hex[:8]}"

    try:
        with tool_runtime_context(session_id):
            print(f"Testing persistent runtime for session {session_id}...")

            install_output = await execute_bash("python -m pip install matplotlib", network=True)
            print(install_output[:500])

            script = "\n".join(
                [
                    "import matplotlib",
                    "print('MATPLOTLIB_OK', matplotlib.__version__)",
                ]
            )
            write_output = await write_file("check_matplotlib.py", script)
            print(write_output)

            run_output = await execute_bash("python check_matplotlib.py")
            print(run_output)

            assert "MATPLOTLIB_OK" in run_output
            print("PASS Persistent runtime keeps installed packages across commands.")
    finally:
        runner.shutdown_session(session_id)


if __name__ == "__main__":
    asyncio.run(test_persistent_runtime())
