from core.runtime_context import (
    get_current_auth_user_id,
    get_current_auth_username,
    get_current_session_id,
)
from core.tasks import tasks_manager
from core.tools import tool


def _get_task_scope() -> tuple[str, str]:
    auth_user_id = get_current_auth_user_id()
    auth_username = get_current_auth_username()
    session_id = get_current_session_id("default") or "default"

    if auth_user_id:
        return auth_user_id, auth_username or auth_user_id

    return f"session:{session_id}", f"session-local:{session_id}"


def _format_task(task: dict) -> str:
    parts = [
        f"[{task['id']}] {task['title']}",
        f"status={task['status']}",
        f"priority={task['priority']}",
    ]
    if task.get("project"):
        parts.append(f"project={task['project']}")
    if task.get("due_date"):
        parts.append(f"due={task['due_date']}")
    return " | ".join(parts)


@tool
async def create_task(title: str, priority: str = "normal", project: str = "", due_date: str = "", notes: str = ""):
    """Creates a local task for the current authenticated user (or current session as fallback)."""
    user_id, scope_label = _get_task_scope()

    try:
        task = await tasks_manager.create_task(
            user_id=user_id,
            title=title,
            priority=priority,
            project=project,
            due_date=due_date,
            notes=notes,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    lines = [
        f"Task created for {scope_label}:",
        _format_task(task),
    ]
    if task.get("notes"):
        lines.append(f"notes={task['notes']}")
    return "\n".join(lines)


@tool
async def list_tasks(status: str = "open", project: str = "", priority: str = "", limit: int = 20):
    """Lists tasks for the current authenticated user, optionally filtered by status, project or priority."""
    user_id, scope_label = _get_task_scope()

    try:
        tasks = await tasks_manager.list_tasks(
            user_id=user_id,
            status=status,
            project=project,
            priority=priority,
            limit=limit,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    if not tasks:
        return f"No tasks found for {scope_label}."

    lines = [f"Tasks for {scope_label}:"]
    for index, task in enumerate(tasks, start=1):
        lines.append(f"{index}. {_format_task(task)}")
    return "\n".join(lines)


@tool
async def complete_task(task_id: str):
    """Marks a task as done for the current authenticated user."""
    user_id, scope_label = _get_task_scope()
    task = await tasks_manager.complete_task(user_id=user_id, task_id=(task_id or "").strip())
    if task is None:
        return f"Error: Task '{task_id}' not found for {scope_label}."
    return f"Task completed for {scope_label}:\n{_format_task(task)}"


@tool
async def update_task(
    task_id: str,
    title: str = "",
    priority: str = "",
    project: str = "",
    due_date: str = "",
    notes: str = "",
    status: str = "",
):
    """Updates selected fields of a task for the current authenticated user."""
    user_id, scope_label = _get_task_scope()

    try:
        task = await tasks_manager.update_task(
            user_id=user_id,
            task_id=(task_id or "").strip(),
            title=title if title else None,
            priority=priority if priority else None,
            project=project if project else None,
            due_date=due_date if due_date else None,
            notes=notes if notes else None,
            status=status if status else None,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    if task is None:
        return f"Error: Task '{task_id}' not found for {scope_label}."
    return f"Task updated for {scope_label}:\n{_format_task(task)}"


@tool
async def delete_task(task_id: str):
    """Deletes a task for the current authenticated user."""
    user_id, scope_label = _get_task_scope()
    deleted = await tasks_manager.delete_task(user_id=user_id, task_id=(task_id or "").strip())
    if not deleted:
        return f"Error: Task '{task_id}' not found for {scope_label}."
    return f"Task '{task_id}' deleted for {scope_label}."
