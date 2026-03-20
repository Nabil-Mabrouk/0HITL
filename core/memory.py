import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import List

import aiosqlite


class LongTermMemory:
    def __init__(self, db_path: str = "./memory.db"):
        self.db_path = os.path.abspath(db_path)

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS neural_archive (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    timestamp DATETIME,
                    metadata TEXT
                )
                """
            )
            await db.commit()

    async def archive_message(self, content: str, metadata: dict = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO neural_archive VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), content, datetime.now(), json.dumps(metadata or {})),
            )
            await db.commit()

    async def search_related(self, query: str, limit: int = 3):
        async with aiosqlite.connect(self.db_path) as db:
            keywords = self._extract_keywords(query)
            if not keywords:
                return []

            where_clause = " OR ".join("content LIKE ?" for _ in keywords)
            params = [f"%{keyword}%" for keyword in keywords]
            params.append(limit)

            async with db.execute(
                f"SELECT content FROM neural_archive WHERE {where_clause} LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

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

    def get_full_history(self) -> List[dict]:
        if not os.path.exists(self.file_path):
            return []

        with open(self.file_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
