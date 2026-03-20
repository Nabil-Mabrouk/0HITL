import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch

from core.runtime_context import tool_runtime_context
from core.tasks import TaskManager


def _load_tasks_tools_module():
    module_name = "test_tasks_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "tasks", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_tasks_skill_tests():
    print("Testing tasks skill...")
    tasks_tools = _load_tasks_tools_module()

    with tempfile.TemporaryDirectory() as tempdir:
        manager = TaskManager(db_path=os.path.join(tempdir, "tasks.db"))

        with patch.object(tasks_tools, "tasks_manager", manager):
            with tool_runtime_context(session_id="session-a", auth_user_id="user-a", auth_username="alice"):
                created = await tasks_tools.create_task(
                    "Tester Telegram v1",
                    priority="high",
                    project="0-hitl",
                    due_date="2026-03-21",
                    notes="Verifier le mapping compte local.",
                )
                assert "Task created for alice" in created
                listed = await tasks_tools.list_tasks(project="0-hitl")
                assert "Tester Telegram v1" in listed
                assert "priority=high" in listed
                print("PASS create_task and list_tasks are scoped to the current user.")

                tasks = await manager.list_tasks(user_id="user-a", status="all")
                task_id = tasks[0]["id"]

                updated = await tasks_tools.update_task(task_id, priority="normal", status="done")
                assert "status=done" in updated
                assert "priority=normal" in updated
                print("PASS update_task edits task fields.")

                completed = await tasks_tools.complete_task(task_id)
                assert "status=done" in completed
                print("PASS complete_task keeps the task completed.")

            with tool_runtime_context(session_id="session-b", auth_user_id="user-b", auth_username="bob"):
                isolated = await tasks_tools.list_tasks(status="all")
                assert "No tasks found for bob." in isolated
                print("PASS tasks are isolated between authenticated users.")

            with tool_runtime_context(session_id="session-a"):
                session_task = await tasks_tools.create_task("Tache locale de session")
                assert "session-local:session-a" in session_task
                fallback_list = await tasks_tools.list_tasks(status="all")
                assert "Tache locale de session" in fallback_list
                print("PASS tasks fallback to a session-local scope when no auth user exists.")

            with tool_runtime_context(session_id="session-a", auth_user_id="user-a", auth_username="alice"):
                tasks = await manager.list_tasks(user_id="user-a", status="all")
                task_id = tasks[0]["id"]
                deleted = await tasks_tools.delete_task(task_id)
                assert f"Task '{task_id}' deleted for alice." == deleted
                print("PASS delete_task removes a task from the current user scope.")


if __name__ == "__main__":
    asyncio.run(run_tasks_skill_tests())
