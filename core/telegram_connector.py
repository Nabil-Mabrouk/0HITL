import asyncio
import os
import re
from typing import Awaitable, Callable, Optional

import httpx

from core.auth import AuthError, auth_manager


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


class TelegramConnector:
    def __init__(
        self,
        *,
        engine,
        session_preparer: Callable[[dict, str | None], tuple[str, str, object]],
        auth=auth_manager,
    ):
        self.engine = engine
        self.session_preparer = session_preparer
        self.auth = auth
        self.enabled = _parse_bool_env("HITL_TELEGRAM_ENABLED", default=False)
        self.bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        self.api_base = (os.getenv("HITL_TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
        self.poll_timeout_seconds = max(5, min(int(os.getenv("HITL_TELEGRAM_POLL_TIMEOUT", "30")), 60))
        self.link_code_ttl_minutes = max(
            1,
            min(int(os.getenv("HITL_TELEGRAM_LINK_CODE_TTL_MINUTES", "10")), 60),
        )
        self.max_message_chars = max(500, min(int(os.getenv("HITL_TELEGRAM_MAX_MESSAGE_CHARS", "3500")), 4000))
        self._poll_task: asyncio.Task | None = None
        self._pending_tasks: set[asyncio.Task] = set()
        self._next_update_offset: Optional[int] = None

    def is_configured(self) -> bool:
        return bool(self.enabled and self.bot_token)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": bool(self.bot_token),
            "running": self._poll_task is not None and not self._poll_task.done(),
            "mode": "long_polling" if self.enabled else "disabled",
        }

    async def start(self):
        if not self.enabled:
            print("[0-HITL] Telegram connector disabled.")
            return

        if not self.bot_token:
            print("[0-HITL] Telegram connector enabled but TELEGRAM_BOT_TOKEN is missing.")
            return

        if self._poll_task is not None and not self._poll_task.done():
            return

        await self.auth.init_db()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="zero-hitl-telegram-poll")
        print("[0-HITL] Telegram connector started in long polling mode.")

    async def stop(self):
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._pending_tasks:
            pending = list(self._pending_tasks)
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._pending_tasks.clear()

    async def _call_api(self, method: str, payload: dict) -> dict | list:
        url = f"{self.api_base}/bot{self.bot_token}/{method}"
        timeout = max(self.poll_timeout_seconds + 5, 20)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {data}")

        return data.get("result")

    async def _get_updates(self) -> list[dict]:
        payload = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if self._next_update_offset is not None:
            payload["offset"] = self._next_update_offset

        result = await self._call_api("getUpdates", payload)
        return result if isinstance(result, list) else []

    async def _send_message(self, chat_id: str, text: str):
        for chunk in self._split_message(text):
            await self._call_api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                },
            )

    async def _send_chat_action(self, chat_id: str, action: str = "typing"):
        await self._call_api(
            "sendChatAction",
            {
                "chat_id": chat_id,
                "action": action,
            },
        )

    def _split_message(self, text: str) -> list[str]:
        clean = (text or "").strip() or "[empty response]"
        if len(clean) <= self.max_message_chars:
            return [clean]

        chunks = []
        remaining = clean
        while remaining:
            if len(remaining) <= self.max_message_chars:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, self.max_message_chars)
            if split_at < self.max_message_chars // 2:
                split_at = remaining.rfind(" ", 0, self.max_message_chars)
            if split_at < self.max_message_chars // 2:
                split_at = self.max_message_chars
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].lstrip()
        return [chunk for chunk in chunks if chunk]

    def _schedule_message_processing(self, message: dict):
        task = asyncio.create_task(self._process_message(message))
        self._pending_tasks.add(task)

        def _cleanup(completed_task: asyncio.Task):
            self._pending_tasks.discard(completed_task)
            try:
                completed_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"[0-HITL] Telegram message processing failed: {exc}")

        task.add_done_callback(_cleanup)

    def _parse_command(self, text: str) -> tuple[str, str]:
        raw = (text or "").strip()
        if not raw.startswith("/"):
            return "", raw

        first_token, _, rest = raw.partition(" ")
        command = first_token.split("@", 1)[0].lower()
        return command, rest.strip()

    async def _poll_loop(self):
        retry_delay = 5
        while True:
            try:
                updates = await self._get_updates()
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._next_update_offset = update_id + 1

                    message = update.get("message")
                    if isinstance(message, dict):
                        self._schedule_message_processing(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[0-HITL] Telegram polling error: {exc}")
                await asyncio.sleep(retry_delay)

    async def _process_message(self, message: dict):
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = str(chat.get("id") or "")
        chat_type = (chat.get("type") or "").strip().lower()
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        if chat_type and chat_type != "private":
            await self._send_message(chat_id, "Telegram v1 supports private chats only.")
            return

        command, args = self._parse_command(text)

        if command in {"/start", "/help"}:
            if command == "/start" and args:
                await self._handle_link_command(chat_id, chat_type, from_user, args)
                return
            await self._send_message(
                chat_id,
                (
                    "0-HITL Telegram v1 is ready.\n"
                    "Use /link CODE to associate this chat with your local 0-HITL account.\n"
                    "Then send a normal message to continue your current session, or /new to start a fresh one."
                ),
            )
            return

        if command == "/link":
            await self._handle_link_command(chat_id, chat_type, from_user, args)
            return

        if command == "/whoami":
            await self._handle_whoami(chat_id)
            return

        if command == "/new":
            await self._handle_new_session(chat_id)
            return

        await self._handle_user_message(chat_id, text)

    async def _handle_link_command(self, chat_id: str, chat_type: str, from_user: dict, code: str):
        clean_code = re.sub(r"\s+", "", code or "")
        if not clean_code:
            await self._send_message(chat_id, "Usage: /link CODE")
            return

        try:
            link = await self.auth.link_telegram_chat(
                code=clean_code,
                chat_id=chat_id,
                telegram_user_id=str(from_user.get("id")) if from_user.get("id") is not None else None,
                telegram_username=from_user.get("username"),
                chat_type=chat_type,
            )
        except AuthError as exc:
            await self._send_message(chat_id, f"Link failed: {exc}")
            return

        await self._send_message(
            chat_id,
            (
                f"This chat is now linked to local user '{link['user']['username']}'.\n"
                "Your next message will continue your Telegram session. Use /new to start a fresh one."
            ),
        )

    async def _handle_whoami(self, chat_id: str):
        link = await self.auth.get_telegram_link_by_chat_id(chat_id)
        if link is None:
            await self._send_message(chat_id, "This chat is not linked yet. Use /link CODE from a private chat.")
            return

        default_session = link.get("default_session_id") or "[none yet]"
        await self._send_message(
            chat_id,
            (
                f"Linked user: {link['user']['username']}\n"
                f"Default session: {default_session}"
            ),
        )

    async def _handle_new_session(self, chat_id: str):
        link = await self.auth.get_telegram_link_by_chat_id(chat_id)
        if link is None:
            await self._send_message(chat_id, "This chat is not linked yet. Use /link CODE first.")
            return

        public_sid, _, _ = self.session_preparer(link["user"], None)
        await self.auth.update_telegram_default_session(chat_id, public_sid)
        await self._send_message(
            chat_id,
            f"Started a new Telegram session: {public_sid}\nYour next message will continue there.",
        )

    async def _handle_user_message(self, chat_id: str, text: str):
        link = await self.auth.get_telegram_link_by_chat_id(chat_id)
        if link is None:
            await self._send_message(chat_id, "This chat is not linked yet. Use /link CODE first.")
            return

        public_sid = link.get("default_session_id") or None
        public_sid, _, session = self.session_preparer(link["user"], public_sid)
        if link.get("default_session_id") != public_sid:
            await self.auth.update_telegram_default_session(chat_id, public_sid)

        try:
            await self._send_chat_action(chat_id, "typing")
        except Exception:
            pass

        try:
            response = await self.engine.chat(session, text)
        except Exception as exc:
            await self._send_message(chat_id, f"0-HITL failed to process this message: {exc}")
            return

        await self._send_message(chat_id, response)
