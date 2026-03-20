import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, List

import aiosqlite

MEMORY_ITEM_TYPES = {"summary", "fact", "preference", "procedure", "incident"}
MEMORY_SENSITIVITY_LEVELS = {"low", "medium", "high"}


class LongTermMemory:
    def __init__(self, db_path: str | None = None):
        configured_path = db_path or os.getenv("HITL_MEMORY_DB_PATH") or "./workspace/system/memory.db"
        self.db_path = self._resolve_db_path(configured_path)

    def _resolve_db_path(self, path: str) -> str:
        absolute_path = os.path.abspath(path)
        if os.path.isdir(absolute_path):
            return os.path.join(absolute_path, "memory.sqlite3")
        return absolute_path

    async def init_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS neural_archive (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    content TEXT,
                    timestamp DATETIME,
                    metadata TEXT
                )
                """
            )
            async with db.execute("PRAGMA table_info(neural_archive)") as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            if "user_id" not in columns:
                await db.execute("ALTER TABLE neural_archive ADD COLUMN user_id TEXT")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_neural_archive_user_id ON neural_archive(user_id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    source_session_id TEXT,
                    source_mission_id TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    expires_at TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_unique
                ON memory_items(user_id, type, fingerprint)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_items_user_active
                ON memory_items(user_id, is_active, type)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_items_last_used
                ON memory_items(last_used_at, updated_at)
                """
            )
            await db.commit()

    def _normalize_memory_type(self, item_type: str) -> str:
        normalized = (item_type or "").strip().lower()
        if normalized not in MEMORY_ITEM_TYPES:
            raise ValueError(f"Unsupported memory item type: {item_type}")
        return normalized

    def _normalize_sensitivity(self, sensitivity: str | None) -> str:
        normalized = (sensitivity or "medium").strip().lower()
        if normalized not in MEMORY_SENSITIVITY_LEVELS:
            return "medium"
        return normalized

    def _clamp_confidence(self, confidence: float | int | None, default: float = 0.7) -> float:
        try:
            value = float(confidence if confidence is not None else default)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))

    def _fingerprint(self, content: str) -> str:
        normalized = re.sub(r"\s+", " ", (content or "").strip().lower())
        return normalized

    def _serialize_memory_row(self, row) -> dict:
        metadata = row["metadata"]
        try:
            parsed_metadata = json.loads(metadata) if metadata else {}
        except json.JSONDecodeError:
            parsed_metadata = {"raw_metadata": metadata}

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "type": row["type"],
            "content": row["content"],
            "fingerprint": row["fingerprint"],
            "confidence": row["confidence"],
            "sensitivity": row["sensitivity"],
            "source_session_id": row["source_session_id"],
            "source_mission_id": row["source_mission_id"],
            "metadata": parsed_metadata,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
            "expires_at": row["expires_at"],
            "is_active": bool(row["is_active"]),
        }

    async def archive_message(self, content: str, metadata: dict = None, user_id: str | None = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO neural_archive (id, user_id, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, content, datetime.now(), json.dumps(metadata or {})),
            )
            await db.commit()

    async def search_related(self, query: str, limit: int = 3, user_id: str | None = None):
        async with aiosqlite.connect(self.db_path) as db:
            keywords = self._extract_keywords(query)
            if not keywords:
                return []

            content_clause = " OR ".join("content LIKE ?" for _ in keywords)
            clauses = [f"({content_clause})"]
            params = [f"%{keyword}%" for keyword in keywords]

            if user_id:
                clauses.append("(user_id = ? OR user_id IS NULL)")
                params.append(user_id)

            params.append(limit)

            async with db.execute(
                f"SELECT content FROM neural_archive WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def cleanup_expired_memory_items(self, user_id: str | None = None) -> int:
        await self.init_db()
        timestamp = datetime.utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            if user_id is None:
                cursor = await db.execute(
                    """
                    UPDATE memory_items
                    SET is_active = 0, updated_at = ?
                    WHERE is_active = 1 AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (timestamp, timestamp),
                )
            else:
                cursor = await db.execute(
                    """
                    UPDATE memory_items
                    SET is_active = 0, updated_at = ?
                    WHERE user_id = ? AND is_active = 1 AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (timestamp, user_id, timestamp),
                )
            await db.commit()
            return cursor.rowcount or 0

    async def deactivate_memory_items(
        self,
        *,
        user_id: str | None,
        contents: list[str] | None = None,
        memory_types: list[str] | None = None,
    ) -> list[dict]:
        await self.init_db()

        fingerprints = [self._fingerprint(content) for content in (contents or []) if (content or "").strip()]
        if not fingerprints:
            return []

        normalized_types = [self._normalize_memory_type(item_type) for item_type in (memory_types or [])]
        placeholders = ", ".join("?" for _ in fingerprints)
        clauses = [f"fingerprint IN ({placeholders})", "is_active = 1"]
        params: list[Any] = [*fingerprints]

        if user_id is None:
            clauses.append("user_id IS NULL")
        else:
            clauses.append("user_id = ?")
            params.append(user_id)

        if normalized_types:
            type_placeholders = ", ".join("?" for _ in normalized_types)
            clauses.append(f"type IN ({type_placeholders})")
            params.extend(normalized_types)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT *
                FROM memory_items
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                return []

            ids = [row["id"] for row in rows]
            id_placeholders = ", ".join("?" for _ in ids)
            timestamp = datetime.utcnow().isoformat()
            await db.execute(
                f"""
                UPDATE memory_items
                SET is_active = 0, updated_at = ?
                WHERE id IN ({id_placeholders})
                """,
                [timestamp, *ids],
            )
            await db.commit()

        return [self._serialize_memory_row(row) for row in rows]

    async def upsert_memory_item(
        self,
        *,
        user_id: str | None,
        item_type: str,
        content: str,
        confidence: float | int | None = None,
        sensitivity: str | None = None,
        source_session_id: str | None = None,
        source_mission_id: str | None = None,
        metadata: dict | None = None,
        expires_at: str | None = None,
        last_used_at: str | None = None,
        is_active: bool = True,
    ) -> dict:
        await self.init_db()

        normalized_type = self._normalize_memory_type(item_type)
        clean_content = re.sub(r"\s+", " ", (content or "").strip())
        if not clean_content:
            raise ValueError("Memory content cannot be empty.")

        normalized_sensitivity = self._normalize_sensitivity(sensitivity)
        normalized_confidence = self._clamp_confidence(confidence)
        fingerprint = self._fingerprint(clean_content)
        item_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()
        effective_last_used_at = last_used_at or timestamp

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO memory_items (
                    id, user_id, type, content, fingerprint, confidence, sensitivity,
                    source_session_id, source_mission_id, metadata,
                    created_at, updated_at, last_used_at, expires_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, type, fingerprint) DO UPDATE SET
                    content = excluded.content,
                    confidence = CASE
                        WHEN excluded.confidence > memory_items.confidence THEN excluded.confidence
                        ELSE memory_items.confidence
                    END,
                    sensitivity = excluded.sensitivity,
                    source_session_id = excluded.source_session_id,
                    source_mission_id = excluded.source_mission_id,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at,
                    last_used_at = excluded.last_used_at,
                    expires_at = excluded.expires_at,
                    is_active = excluded.is_active
                """,
                (
                    item_id,
                    user_id,
                    normalized_type,
                    clean_content,
                    fingerprint,
                    normalized_confidence,
                    normalized_sensitivity,
                    source_session_id,
                    source_mission_id,
                    json.dumps(metadata or {}),
                    timestamp,
                    timestamp,
                    effective_last_used_at,
                    expires_at,
                    1 if is_active else 0,
                ),
            )
            await db.commit()

            if user_id is None:
                select_query = """
                SELECT *
                FROM memory_items
                WHERE user_id IS NULL AND type = ? AND fingerprint = ?
                LIMIT 1
                """
                select_params = (normalized_type, fingerprint)
            else:
                select_query = """
                SELECT *
                FROM memory_items
                WHERE user_id = ? AND type = ? AND fingerprint = ?
                LIMIT 1
                """
                select_params = (user_id, normalized_type, fingerprint)

            async with db.execute(select_query, select_params) as cursor:
                row = await cursor.fetchone()

        return self._serialize_memory_row(row)

    async def list_memory_items(
        self,
        *,
        user_id: str | None,
        memory_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        await self.init_db()
        await self.cleanup_expired_memory_items(user_id=user_id)
        normalized_types = [self._normalize_memory_type(item_type) for item_type in (memory_types or [])]

        clauses = ["is_active = 1", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [datetime.utcnow().isoformat()]

        if user_id is None:
            clauses.append("user_id IS NULL")
        else:
            clauses.append("user_id = ?")
            params.append(user_id)

        if normalized_types:
            placeholders = ", ".join("?" for _ in normalized_types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(normalized_types)

        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT *
                FROM memory_items
                WHERE {' AND '.join(clauses)}
                ORDER BY confidence DESC, COALESCE(last_used_at, updated_at) DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._serialize_memory_row(row) for row in rows]

    async def search_memory_items(
        self,
        query: str,
        *,
        user_id: str | None,
        memory_types: list[str] | None = None,
        limit: int = 6,
    ) -> list[dict]:
        await self.init_db()
        await self.cleanup_expired_memory_items(user_id=user_id)
        normalized_types = [self._normalize_memory_type(item_type) for item_type in (memory_types or [])]
        keywords = self._extract_keywords(query)

        clauses = ["is_active = 1", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [datetime.utcnow().isoformat()]

        if user_id is None:
            clauses.append("user_id IS NULL")
        else:
            clauses.append("user_id = ?")
            params.append(user_id)

        if normalized_types:
            placeholders = ", ".join("?" for _ in normalized_types)
            clauses.append(f"type IN ({placeholders})")
            params.extend(normalized_types)

        if keywords:
            keyword_clause = " OR ".join("content LIKE ?" for _ in keywords)
            clauses.append(f"({keyword_clause})")
            params.extend([f"%{keyword}%" for keyword in keywords])

        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT *
                FROM memory_items
                WHERE {' AND '.join(clauses)}
                ORDER BY confidence DESC, COALESCE(last_used_at, updated_at) DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._serialize_memory_row(row) for row in rows]

    async def mark_memory_items_used(self, item_ids: list[str]):
        clean_ids = [item_id for item_id in item_ids if item_id]
        if not clean_ids:
            return

        await self.init_db()
        placeholders = ", ".join("?" for _ in clean_ids)
        timestamp = datetime.utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE memory_items
                SET last_used_at = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [timestamp, timestamp, *clean_ids],
            )
            await db.commit()

    def _extract_keywords(self, query: str) -> List[str]:
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "then",
            "have",
            "from",
            "into",
            "your",
            "need",
            "use",
            "show",
            "test",
            "instl",
            "install",
            "please",
        }

        words = re.findall(r"[a-zA-Z0-9_]+", query.lower() if query else "")
        keywords = []
        for word in words:
            if len(word) < 4 or word in stopwords:
                continue
            if word not in keywords:
                keywords.append(word)
        return keywords[:3]


class SessionLogger:
    """
    Synchronous JSONL logger for session traces.
    """

    def __init__(self, session_id: str):
        safe_session_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", session_id).strip("-.") or "default"
        session_dir = os.path.abspath(os.path.join("./workspace", "sessions", safe_session_id, "logs"))
        os.makedirs(session_dir, exist_ok=True)
        self.file_path = os.path.join(session_dir, "session.jsonl")
        self._lock = threading.Lock()

    def log(self, data: dict):
        def json_serial(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        line = json.dumps(data, default=json_serial, ensure_ascii=False) + "\n"

        with self._lock:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(line)

    def log_event(self, event_type: str, **data):
        payload = {
            "record_type": "event",
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
        }
        payload.update({key: value for key, value in data.items() if value is not None})
        self.log(payload)

    def get_full_history(self) -> List[dict]:
        if not os.path.exists(self.file_path):
            return []

        with open(self.file_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
