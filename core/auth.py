import base64
import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
from dotenv import load_dotenv

load_dotenv()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthError(Exception):
    pass


class BootstrapCompletedError(AuthError):
    pass


class InvalidCredentialsError(AuthError):
    pass


class UsernameTakenError(AuthError):
    pass


class LocalAuthManager:
    def __init__(self, db_path: Optional[str] = None):
        default_path = os.getenv("HITL_AUTH_DB_PATH", "./workspace/system/auth.db")
        self.db_path = os.path.abspath(db_path or default_path)
        self.cookie_name = os.getenv("HITL_AUTH_SESSION_COOKIE", "zero_hitl_session")
        self.secure_cookie = os.getenv("HITL_AUTH_SECURE_COOKIE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.session_days = int(os.getenv("HITL_AUTH_SESSION_DAYS", "30"))

    async def init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS session_permissions (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    public_session_id TEXT NOT NULL,
                    grantee_user_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, public_session_id, grantee_user_id),
                    FOREIGN KEY(owner_user_id) REFERENCES users(id),
                    FOREIGN KEY(grantee_user_id) REFERENCES users(id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_permissions_owner_session
                ON session_permissions(owner_user_id, public_session_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_permissions_grantee
                ON session_permissions(grantee_user_id)
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_links (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    telegram_chat_id TEXT NOT NULL UNIQUE,
                    telegram_user_id TEXT,
                    telegram_username TEXT,
                    chat_type TEXT,
                    default_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_links_user_id
                ON telegram_links(user_id)
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_link_codes (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_link_codes_user_id
                ON telegram_link_codes(user_id)
                """
            )
            await db.commit()

    def normalize_username(self, username: str) -> str:
        value = (username or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,32}", value):
            raise AuthError(
                "Username must be 3-32 chars and contain only letters, digits, dot, dash or underscore."
            )
        return value

    def validate_password(self, password: str):
        if len(password or "") < 10:
            raise AuthError("Password must contain at least 10 characters.")

    def _hash_password(self, password: str, salt_b64: Optional[str] = None) -> tuple[str, str]:
        salt = base64.b64decode(salt_b64) if salt_b64 else secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
        return (
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    def _verify_password(self, password: str, salt_b64: str, expected_hash: str) -> bool:
        _, candidate = self._hash_password(password, salt_b64=salt_b64)
        return hmac.compare_digest(candidate, expected_hash)

    def _hash_session_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def normalize_session_permission(self, permission: str) -> str:
        normalized = (permission or "").strip().lower()
        if normalized not in {"viewer", "operator"}:
            raise AuthError("Permission must be one of: viewer, operator.")
        return normalized

    def normalize_telegram_link_code(self, code: str) -> str:
        normalized = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
        if len(normalized) < 6 or len(normalized) > 16:
            raise AuthError("Telegram link code must contain 6-16 alphanumeric characters.")
        return normalized

    def _generate_telegram_link_code(self, length: int = 8) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(max(6, min(length, 16))))

    def _serialize_user(self, row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    async def bootstrap_required(self) -> bool:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
        return not row or row[0] == 0

    async def bootstrap_owner(
        self,
        username: str,
        password: str,
        display_name: Optional[str] = None,
    ) -> dict:
        await self.init_db()

        normalized_username = self.normalize_username(username)
        self.validate_password(password)
        safe_display_name = (display_name or normalized_username).strip() or normalized_username

        if not await self.bootstrap_required():
            raise BootstrapCompletedError("Bootstrap has already been completed.")

        user_id = str(uuid.uuid4())
        password_salt, password_hash = self._hash_password(password)
        created_at = utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    id, username, display_name, role, password_salt, password_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_username,
                    safe_display_name,
                    "owner",
                    password_salt,
                    password_hash,
                    created_at,
                ),
            )
            await db.commit()

        return {
            "id": user_id,
            "username": normalized_username,
            "display_name": safe_display_name,
            "role": "owner",
            "created_at": created_at,
        }

    async def authenticate(self, username: str, password: str) -> dict:
        await self.init_db()

        normalized_username = self.normalize_username(username)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, username, display_name, role, password_salt, password_hash, created_at
                FROM users
                WHERE username = ?
                """,
                (normalized_username,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None or not self._verify_password(password, row["password_salt"], row["password_hash"]):
            raise InvalidCredentialsError("Invalid username or password.")

        return self._serialize_user(row)

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        await self.init_db()

        normalized_username = self.normalize_username(username)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, username, display_name, role, created_at
                FROM users
                WHERE username = ?
                """,
                (normalized_username,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        return self._serialize_user(row)

    async def create_session(self, user_id: str) -> tuple[str, datetime]:
        await self.init_db()

        token = secrets.token_urlsafe(32)
        created_at = utcnow()
        expires_at = created_at + timedelta(days=self.session_days)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO auth_sessions (id, token_hash, user_id, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    self._hash_session_token(token),
                    user_id,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            await db.commit()

        return token, expires_at

    async def get_user_by_session_token(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None

        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    auth_sessions.id AS session_id,
                    auth_sessions.expires_at AS expires_at,
                    users.id AS id,
                    users.username AS username,
                    users.display_name AS display_name,
                    users.role AS role,
                    users.created_at AS created_at
                FROM auth_sessions
                JOIN users ON users.id = auth_sessions.user_id
                WHERE auth_sessions.token_hash = ?
                """,
                (self._hash_session_token(token),),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return None

            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= utcnow():
                await db.execute("DELETE FROM auth_sessions WHERE id = ?", (row["session_id"],))
                await db.commit()
                return None

            await db.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
                (utcnow().isoformat(), row["session_id"]),
            )
            await db.commit()

        return self._serialize_user(row)

    async def revoke_session(self, token: Optional[str]):
        if not token:
            return

        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (self._hash_session_token(token),),
            )
            await db.commit()

    async def list_users(self) -> list[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, username, display_name, role, created_at FROM users ORDER BY created_at ASC"
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._serialize_user(row) for row in rows]

    async def create_telegram_link_code(self, user_id: str, ttl_minutes: int = 10) -> dict:
        await self.init_db()

        safe_ttl = max(1, min(int(ttl_minutes or 10), 60))
        created_at = utcnow()
        expires_at = created_at + timedelta(minutes=safe_ttl)
        code = self._generate_telegram_link_code()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM telegram_link_codes WHERE expires_at <= ? OR consumed_at IS NOT NULL",
                (created_at.isoformat(),),
            )
            await db.execute(
                """
                INSERT INTO telegram_link_codes (id, code, user_id, created_at, expires_at, consumed_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(uuid.uuid4()),
                    code,
                    user_id,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            await db.commit()

        return {"code": code, "created_at": created_at.isoformat(), "expires_at": expires_at.isoformat()}

    async def list_telegram_links(self, user_id: str) -> list[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM telegram_links
                WHERE user_id = ?
                ORDER BY created_at ASC
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "chat_id": row["telegram_chat_id"],
                "telegram_user_id": row["telegram_user_id"],
                "telegram_username": row["telegram_username"],
                "chat_type": row["chat_type"],
                "default_session_id": row["default_session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    async def get_telegram_link_by_chat_id(self, chat_id: str) -> Optional[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    telegram_links.telegram_chat_id AS telegram_chat_id,
                    telegram_links.telegram_user_id AS telegram_user_id,
                    telegram_links.telegram_username AS telegram_username,
                    telegram_links.chat_type AS chat_type,
                    telegram_links.default_session_id AS default_session_id,
                    telegram_links.created_at AS created_at,
                    telegram_links.updated_at AS updated_at,
                    users.id AS id,
                    users.username AS username,
                    users.display_name AS display_name,
                    users.role AS role,
                    users.created_at AS user_created_at
                FROM telegram_links
                JOIN users ON users.id = telegram_links.user_id
                WHERE telegram_links.telegram_chat_id = ?
                """,
                (str(chat_id),),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        return {
            "chat_id": row["telegram_chat_id"],
            "telegram_user_id": row["telegram_user_id"],
            "telegram_username": row["telegram_username"],
            "chat_type": row["chat_type"],
            "default_session_id": row["default_session_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "user": {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "role": row["role"],
                "created_at": row["user_created_at"],
            },
        }

    async def link_telegram_chat(
        self,
        *,
        code: str,
        chat_id: str,
        telegram_user_id: str | None = None,
        telegram_username: str | None = None,
        chat_type: str | None = None,
    ) -> dict:
        await self.init_db()

        normalized_code = self.normalize_telegram_link_code(code)
        now = utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT code, user_id, expires_at, consumed_at
                FROM telegram_link_codes
                WHERE code = ?
                """,
                (normalized_code,),
            ) as cursor:
                code_row = await cursor.fetchone()

            if code_row is None:
                raise AuthError("Telegram link code not found.")

            if code_row["consumed_at"]:
                raise AuthError("Telegram link code has already been used.")

            if datetime.fromisoformat(code_row["expires_at"]) <= utcnow():
                await db.execute("DELETE FROM telegram_link_codes WHERE code = ?", (normalized_code,))
                await db.commit()
                raise AuthError("Telegram link code has expired.")

            async with db.execute(
                """
                SELECT user_id
                FROM telegram_links
                WHERE telegram_chat_id = ?
                """,
                (str(chat_id),),
            ) as cursor:
                existing_link = await cursor.fetchone()

            if existing_link is not None and existing_link["user_id"] != code_row["user_id"]:
                raise AuthError("This Telegram chat is already linked to another user.")

            if existing_link is None:
                await db.execute(
                    """
                    INSERT INTO telegram_links (
                        id, user_id, telegram_chat_id, telegram_user_id, telegram_username, chat_type, default_session_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        code_row["user_id"],
                        str(chat_id),
                        str(telegram_user_id) if telegram_user_id is not None else None,
                        (telegram_username or "").strip() or None,
                        (chat_type or "").strip() or None,
                        None,
                        now,
                        now,
                    ),
                )
            else:
                await db.execute(
                    """
                    UPDATE telegram_links
                    SET telegram_user_id = ?, telegram_username = ?, chat_type = ?, updated_at = ?
                    WHERE telegram_chat_id = ?
                    """,
                    (
                        str(telegram_user_id) if telegram_user_id is not None else None,
                        (telegram_username or "").strip() or None,
                        (chat_type or "").strip() or None,
                        now,
                        str(chat_id),
                    ),
                )

            await db.execute(
                "UPDATE telegram_link_codes SET consumed_at = ? WHERE code = ?",
                (now, normalized_code),
            )
            await db.commit()

        link = await self.get_telegram_link_by_chat_id(str(chat_id))
        if link is None:
            raise AuthError("Telegram link could not be loaded after creation.")
        return link

    async def update_telegram_default_session(self, chat_id: str, default_session_id: str | None) -> Optional[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE telegram_links
                SET default_session_id = ?, updated_at = ?
                WHERE telegram_chat_id = ?
                """,
                ((default_session_id or "").strip() or None, utcnow().isoformat(), str(chat_id)),
            )
            await db.commit()

        if not cursor.rowcount:
            return None

        return await self.get_telegram_link_by_chat_id(str(chat_id))

    async def delete_telegram_link(self, user_id: str, chat_id: str) -> bool:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM telegram_links
                WHERE user_id = ? AND telegram_chat_id = ?
                """,
                (user_id, str(chat_id)),
            )
            await db.commit()

        return bool(cursor.rowcount)

    async def create_user(
        self,
        username: str,
        password: str,
        display_name: Optional[str] = None,
        role: str = "member",
    ) -> dict:
        await self.init_db()

        normalized_username = self.normalize_username(username)
        self.validate_password(password)
        normalized_role = (role or "member").strip().lower()
        if normalized_role not in {"owner", "admin", "member"}:
            raise AuthError("Role must be one of: owner, admin, member.")

        safe_display_name = (display_name or normalized_username).strip() or normalized_username
        password_salt, password_hash = self._hash_password(password)
        created_at = utcnow().isoformat()
        user_id = str(uuid.uuid4())

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO users (
                        id, username, display_name, role, password_salt, password_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_username,
                        safe_display_name,
                        normalized_role,
                        password_salt,
                        password_hash,
                        created_at,
                    ),
                )
                await db.commit()
        except aiosqlite.IntegrityError as exc:
            raise UsernameTakenError("Username already exists.") from exc

        return {
            "id": user_id,
            "username": normalized_username,
            "display_name": safe_display_name,
            "role": normalized_role,
            "created_at": created_at,
        }

    async def get_session_permission(
        self,
        owner_user_id: str,
        public_session_id: str,
        current_user_id: str,
    ) -> Optional[str]:
        await self.init_db()

        if owner_user_id == current_user_id:
            return "owner"

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT permission
                FROM session_permissions
                WHERE owner_user_id = ? AND public_session_id = ? AND grantee_user_id = ?
                """,
                (owner_user_id, public_session_id, current_user_id),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        return row[0]

    async def list_session_permissions(
        self,
        owner_user_id: str,
        public_session_id: str,
    ) -> list[dict]:
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    session_permissions.permission AS permission,
                    session_permissions.created_at AS created_at,
                    session_permissions.updated_at AS updated_at,
                    users.id AS id,
                    users.username AS username,
                    users.display_name AS display_name,
                    users.role AS role
                FROM session_permissions
                JOIN users ON users.id = session_permissions.grantee_user_id
                WHERE session_permissions.owner_user_id = ? AND session_permissions.public_session_id = ?
                ORDER BY users.username ASC
                """,
                (owner_user_id, public_session_id),
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            {
                "user": {
                    "id": row["id"],
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "role": row["role"],
                },
                "permission": row["permission"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    async def grant_session_permission(
        self,
        owner_user_id: str,
        public_session_id: str,
        grantee_username: str,
        permission: str,
    ) -> dict:
        await self.init_db()

        normalized_permission = self.normalize_session_permission(permission)
        grantee = await self.get_user_by_username(grantee_username)
        if grantee is None:
            raise AuthError("User not found.")
        if grantee["id"] == owner_user_id:
            raise AuthError("Cannot share a session with its owner.")

        timestamp = utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO session_permissions (
                    id, owner_user_id, public_session_id, grantee_user_id, permission, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, public_session_id, grantee_user_id)
                DO UPDATE SET permission = excluded.permission, updated_at = excluded.updated_at
                """,
                (
                    str(uuid.uuid4()),
                    owner_user_id,
                    public_session_id,
                    grantee["id"],
                    normalized_permission,
                    timestamp,
                    timestamp,
                ),
            )
            await db.commit()

        permissions = await self.list_session_permissions(owner_user_id, public_session_id)
        for share in permissions:
            if share["user"]["id"] == grantee["id"]:
                return share

        raise AuthError("Unable to load the granted session permission.")

    async def revoke_session_permission(
        self,
        owner_user_id: str,
        public_session_id: str,
        grantee_username: str,
    ) -> bool:
        await self.init_db()

        grantee = await self.get_user_by_username(grantee_username)
        if grantee is None:
            return False

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM session_permissions
                WHERE owner_user_id = ? AND public_session_id = ? AND grantee_user_id = ?
                """,
                (owner_user_id, public_session_id, grantee["id"]),
            )
            await db.commit()

        return cursor.rowcount > 0


auth_manager = LocalAuthManager()
