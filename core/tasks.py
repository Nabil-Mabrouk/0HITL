import os
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite


TASK_STATUS_VALUES = {"open", "done"}
TASK_PRIORITY_VALUES = {"low", "normal", "high"}


class TaskManager:
    def __init__(self, db_path: Optional[str] = None):
        configured_path = db_path or os.getenv("HITL_TASKS_DB_PATH") or "./workspace/system/tasks.db"
        self.db_path = self._resolve_db_path(configured_path)

    def _resolve_db_path(self, path: str) -> str:
        absolute_path = os.path.abspath(path)
        if os.path.isdir(absolute_path):
            return os.path.join(absolute_path, "tasks.sqlite3")
        return absolute_path

    def _utcnow(self) -> str:
        return datetime.utcnow().isoformat()

    def _normalize_status(self, status: str | None) -> str:
        normalized = (status or "open").strip().lower()
        if normalized not in TASK_STATUS_VALUES:
            raise ValueError("Status must be one of: open, done.")
        return normalized

    def _normalize_priority(self, priority: str | None) -> str:
        normalized = (priority or "normal").strip().lower()
        if normalized not in TASK_PRIORITY_VALUES:
            raise ValueError("Priority must be one of: low, normal, high.")
        return normalized

    def _sanitize_text(self, value: str | None, *, max_length: int, default: str = "") -> str:
        text = (value or default).strip()
        if len(text) > max_length:
            text = text[:max_length].rstrip()
        return text

    def _serialize_task(self, row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "status": row["status"],
            "priority": row["priority"],
            "project": row["project"] or "",
            "due_date": row["due_date"] or "",
            "notes": row["notes"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"] or "",
        }

    async def init_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    project TEXT,
                    due_date TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_user_status
                ON tasks(user_id, status, priority, created_at)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_user_project
                ON tasks(user_id, project, status)
                """
            )
            await db.commit()

    async def create_task(
        self,
        *,
        user_id: str,
        title: str,
        priority: str = "normal",
        project: str = "",
        due_date: str = "",
        notes: str = "",
    ) -> dict:
        await self.init_db()

        clean_title = self._sanitize_text(title, max_length=200)
        if not clean_title:
            raise ValueError("Task title is required.")

        task = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": clean_title,
            "status": "open",
            "priority": self._normalize_priority(priority),
            "project": self._sanitize_text(project, max_length=80),
            "due_date": self._sanitize_text(due_date, max_length=80),
            "notes": self._sanitize_text(notes, max_length=4000),
            "created_at": self._utcnow(),
            "updated_at": self._utcnow(),
            "completed_at": "",
        }

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO tasks (
                    id, user_id, title, status, priority, project, due_date, notes, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["user_id"],
                    task["title"],
                    task["status"],
                    task["priority"],
                    task["project"],
                    task["due_date"],
                    task["notes"],
                    task["created_at"],
                    task["updated_at"],
                    task["completed_at"] or None,
                ),
            )
            await db.commit()

        return task

    async def list_tasks(
        self,
        *,
        user_id: str,
        status: str = "open",
        project: str = "",
        priority: str = "",
        limit: int = 50,
    ) -> list[dict]:
        await self.init_db()

        clauses = ["user_id = ?"]
        params = [user_id]

        normalized_status = (status or "").strip().lower()
        if normalized_status and normalized_status != "all":
            clauses.append("status = ?")
            params.append(self._normalize_status(normalized_status))

        clean_project = self._sanitize_text(project, max_length=80)
        if clean_project:
            clauses.append("project = ?")
            params.append(clean_project)

        clean_priority = (priority or "").strip().lower()
        if clean_priority and clean_priority != "all":
            clauses.append("priority = ?")
            params.append(self._normalize_priority(clean_priority))

        safe_limit = max(1, min(int(limit or 50), 200))
        params.append(safe_limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT *
                FROM tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE status WHEN 'open' THEN 0 ELSE 1 END ASC,
                    CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END ASC,
                    CASE WHEN due_date IS NULL OR due_date = '' THEN 1 ELSE 0 END ASC,
                    due_date ASC,
                    created_at DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._serialize_task(row) for row in rows]

    async def get_task(self, *, user_id: str, task_id: str) -> Optional[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM tasks
                WHERE user_id = ? AND id = ?
                """,
                (user_id, task_id),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        return self._serialize_task(row)

    async def update_task(
        self,
        *,
        user_id: str,
        task_id: str,
        title: str | None = None,
        priority: str | None = None,
        project: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> Optional[dict]:
        existing = await self.get_task(user_id=user_id, task_id=task_id)
        if existing is None:
            return None

        updated = {
            "title": self._sanitize_text(title, max_length=200, default=existing["title"]) if title is not None else existing["title"],
            "priority": self._normalize_priority(priority) if priority is not None else existing["priority"],
            "project": self._sanitize_text(project, max_length=80, default=existing["project"]) if project is not None else existing["project"],
            "due_date": self._sanitize_text(due_date, max_length=80, default=existing["due_date"]) if due_date is not None else existing["due_date"],
            "notes": self._sanitize_text(notes, max_length=4000, default=existing["notes"]) if notes is not None else existing["notes"],
            "status": self._normalize_status(status) if status is not None else existing["status"],
        }

        if not updated["title"]:
            raise ValueError("Task title is required.")

        completed_at = existing["completed_at"] or ""
        if updated["status"] == "done" and not completed_at:
            completed_at = self._utcnow()
        elif updated["status"] == "open":
            completed_at = ""

        updated_at = self._utcnow()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE tasks
                SET title = ?, priority = ?, project = ?, due_date = ?, notes = ?, status = ?, updated_at = ?, completed_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    updated["title"],
                    updated["priority"],
                    updated["project"],
                    updated["due_date"],
                    updated["notes"],
                    updated["status"],
                    updated_at,
                    completed_at or None,
                    user_id,
                    task_id,
                ),
            )
            await db.commit()

        return await self.get_task(user_id=user_id, task_id=task_id)

    async def complete_task(self, *, user_id: str, task_id: str) -> Optional[dict]:
        return await self.update_task(user_id=user_id, task_id=task_id, status="done")

    async def delete_task(self, *, user_id: str, task_id: str) -> bool:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM tasks
                WHERE user_id = ? AND id = ?
                """,
                (user_id, task_id),
            )
            await db.commit()

        return bool(cursor.rowcount)


tasks_manager = TaskManager()
