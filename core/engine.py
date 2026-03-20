import asyncio
import json
import litellm
import os
import re
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import List, Optional

from core.bus import event_bus
from core.context import ContextManager
from core.memory import LongTermMemory, SessionLogger
from core.model_registry import resolve_runtime_model_roles
from core.models import AgentSession, Message, Role, ToolCall
from core.prompter import ProfileManager
from core.resilience import CognitiveResilience, ErrorInterpreter
from core.runner import runner
from core.runtime_context import tool_runtime_context
from core.skills import skill_manager
from core.superego import RiskLevel, superego
from core.tools import registry


_active_trace_context: ContextVar[dict | None] = ContextVar("active_trace_context", default=None)


class TracedCompletion:
    def __init__(self, response, trace_info: dict):
        self.response = response
        self.trace_info = trace_info

    def __getattr__(self, name):
        return getattr(self.response, name)


class ZeroHitlEngine:
    def __init__(self, model: str | None = None, memory_model: str | None = None):
        self.model_roles = resolve_runtime_model_roles(
            agent_model=model,
            memory_model=memory_model,
        )
        self.model = self.model_roles["agent"]
        self.memory_model = self.model_roles["memory"]
        self.context_manager = ContextManager(model=self.model)
        self.profile_manager = ProfileManager()
        self.ltm = LongTermMemory()
        self.resilience = CognitiveResilience(self.ltm, self)
        self.error_interpreter = ErrorInterpreter()
        self._last_error: Optional[str] = None
        self._post_session_tasks: set[asyncio.Task] = set()

    async def drain_post_session_tasks(self):
        if not self._post_session_tasks:
            return
        await asyncio.gather(*list(self._post_session_tasks), return_exceptions=True)

    def _get_trace_context(self) -> dict | None:
        return _active_trace_context.get()

    def _trace_event(self, event_type: str, **data):
        trace_context = self._get_trace_context()
        if not trace_context:
            return

        logger = trace_context.get("logger")
        if logger is None:
            return

        payload = {
            "mission_id": trace_context.get("mission_id"),
            "public_session_id": trace_context.get("public_session_id"),
            "internal_session_id": trace_context.get("internal_session_id"),
            "auth_user_id": trace_context.get("auth_user_id"),
            "auth_username": trace_context.get("auth_username"),
        }
        payload.update(data)
        logger.log_event(event_type, **payload)

    def _extract_json(self, text: str) -> dict:
        """
        Extrait robustement un objet JSON d'une chaine de texte.
        Gere les cas ou le JSON est entoure de markdown ou contient des artefacts.
        """
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        markdown_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if markdown_match:
            try:
                return json.loads(markdown_match.group(1))
            except json.JSONDecodeError:
                pass

        depth = 0
        start = -1
        for i, char in enumerate(text):
            if char == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        continue

        raise ValueError(f"Impossible d'extraire un JSON valide de: {text[:200]}...")

    async def call_llm(
        self,
        messages: List[Message],
        use_tools: bool = True,
        model: str | None = None,
        llm_role: str = "agent",
    ):
        """
        Appelle le LLM avec gestion appropriee des roles et tool calls.
        """
        effective_model = model or self.model
        formatted = []
        for m in messages:
            d = m.model_dump(exclude_none=True)

            if "id" in d:
                del d["id"]
            if "timestamp" in d:
                del d["timestamp"]
            if "parent_id" in d:
                del d["parent_id"]

            if m.role == Role.TOOL:
                d["role"] = "tool"
                d["content"] = str(d.get("content", ""))
                if not d.get("tool_call_id"):
                    d["tool_call_id"] = "unknown"

            if "tool_calls" in d and d["tool_calls"]:
                d["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in d["tool_calls"]
                ]

            formatted.append(d)

        kwargs = {
            "model": effective_model,
            "messages": formatted,
            "stream": True,
        }

        if use_tools and registry.schemas:
            kwargs["tools"] = registry.schemas
            kwargs["tool_choice"] = "auto"

        trace_context = self._get_trace_context()
        trace_info = None
        if trace_context is not None:
            trace_context["llm_call_index"] = trace_context.get("llm_call_index", 0) + 1
            trace_context["llm_calls"] = trace_context.get("llm_calls", 0) + 1
            trace_context["attempt_llm_calls"] = trace_context.get("attempt_llm_calls", 0) + 1
            trace_info = {
                "started_at": time.perf_counter(),
                "llm_call_index": trace_context["llm_call_index"],
                "attempt": trace_context.get("current_attempt"),
                "use_tools": use_tools,
                "message_count": len(messages),
                "model": effective_model,
                "llm_role": llm_role,
            }
            self._trace_event(
                "llm_call_started",
                llm_call_index=trace_info["llm_call_index"],
                attempt=trace_info["attempt"],
                use_tools=use_tools,
                message_count=len(messages),
                model=effective_model,
                llm_role=llm_role,
            )

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            if trace_info is not None:
                self._trace_event(
                    "llm_call_completed",
                    llm_call_index=trace_info["llm_call_index"],
                    attempt=trace_info["attempt"],
                    use_tools=trace_info["use_tools"],
                    message_count=trace_info["message_count"],
                    model=trace_info["model"],
                    llm_role=trace_info["llm_role"],
                    status="dispatch_error",
                    duration_ms=round((time.perf_counter() - trace_info["started_at"]) * 1000, 2),
                    error=str(exc)[:500],
                )
            raise

        if trace_info is not None:
            return TracedCompletion(response, trace_info)

        return response

    def _extract_delta(self, chunk):
        if not chunk or not getattr(chunk, "choices", None):
            return None

        choice = chunk.choices[0]
        return getattr(choice, "delta", None) or getattr(choice, "message", None)

    async def iter_completion_deltas(self, response):
        traced_response = response if isinstance(response, TracedCompletion) else None
        response_source = traced_response.response if traced_response else response
        trace_info = traced_response.trace_info if traced_response else None

        delta_count = 0
        content_chars = 0
        tool_call_chunks = 0
        first_delta_ms = None
        status = "completed"
        completed_stream = False
        error_text = None

        try:
            if hasattr(response_source, "__aiter__"):
                async for chunk in response_source:
                    delta = self._extract_delta(chunk)
                    if delta is None:
                        continue

                    delta_count += 1
                    if trace_info is not None and first_delta_ms is None:
                        first_delta_ms = round((time.perf_counter() - trace_info["started_at"]) * 1000, 2)

                    content = getattr(delta, "content", None)
                    if content:
                        content_chars += len(content)

                    delta_tool_calls = getattr(delta, "tool_calls", None)
                    if delta_tool_calls:
                        tool_call_chunks += len(delta_tool_calls)

                    yield delta

                completed_stream = True
                return

            delta = self._extract_delta(response_source)
            if delta is not None:
                delta_count += 1
                if trace_info is not None and first_delta_ms is None:
                    first_delta_ms = round((time.perf_counter() - trace_info["started_at"]) * 1000, 2)

                content = getattr(delta, "content", None)
                if content:
                    content_chars += len(content)

                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    tool_call_chunks += len(delta_tool_calls)

                yield delta
            completed_stream = True
        except Exception as exc:
            status = "error"
            error_text = str(exc)[:500]
            raise
        finally:
            if trace_info is not None:
                if status == "completed" and not completed_stream:
                    status = "interrupted"

                self._trace_event(
                    "llm_call_completed",
                    llm_call_index=trace_info["llm_call_index"],
                    attempt=trace_info["attempt"],
                    use_tools=trace_info["use_tools"],
                    message_count=trace_info["message_count"],
                    model=trace_info["model"],
                    llm_role=trace_info["llm_role"],
                    status=status,
                    duration_ms=round((time.perf_counter() - trace_info["started_at"]) * 1000, 2),
                    first_delta_ms=first_delta_ms,
                    delta_count=delta_count,
                    content_chars=content_chars,
                    tool_call_chunks=tool_call_chunks,
                    error=error_text,
                )

    async def collect_completion_text(self, response) -> str:
        full_content = ""
        async for delta in self.iter_completion_deltas(response):
            content = getattr(delta, "content", None)
            if content:
                full_content += content
        return full_content

    def _user_requires_artifact_url(self, user_input: str) -> bool:
        lowered = (user_input or "").lower()
        url_markers = [" url", "url ", "link", "lien", "download", "télécharger", "telecharger"]
        artifact_markers = [
            "chart",
            "graph",
            "plot",
            "image",
            "png",
            "pdf",
            "csv",
            "file",
            "fichier",
        ]
        return any(marker in lowered for marker in url_markers) and any(
            marker in lowered for marker in artifact_markers
        )

    def _response_contains_url(self, text: Optional[str]) -> bool:
        if not text:
            return False
        return bool(re.search(r"(https?://\S+|/session-files/\S+)", text))

    def _extract_urls(self, text: Optional[str]) -> List[str]:
        if not text:
            return []
        matches = re.findall(r"(https?://[^\s\])]+|/session-files/[^\s\])]+)", text)
        return matches

    def _candidate_artifact_paths(self, session_id: str, filename: str) -> List[str]:
        candidates = [
            os.path.join(runner.get_session_files_dir(session_id), filename),
            os.path.join(runner.get_session_files_dir(session_id), "artifacts", filename),
        ]
        unique = []
        for candidate in candidates:
            normalized = os.path.abspath(candidate)
            if normalized not in unique:
                unique.append(normalized)
        return unique

    def _find_referenced_artifact_url(self, session_id: str, text: Optional[str]) -> Optional[str]:
        if not text:
            return None

        filename_matches = re.findall(
            r"([A-Za-z0-9_./\\-]+\.(?:png|jpg|jpeg|svg|pdf|csv|html|json))",
            text,
            flags=re.IGNORECASE,
        )
        for match in filename_matches:
            normalized = match.strip().strip("`'\"")
            for candidate in self._candidate_artifact_paths(session_id, normalized):
                if os.path.exists(candidate) and os.path.isfile(candidate):
                    relative = os.path.relpath(candidate, runner.get_session_files_dir(session_id))
                    return runner.build_session_file_url(
                        session_id, f"files/{relative.replace(os.sep, '/')}"
                    )
        return None

    def _find_recent_tool_url(self, session: AgentSession) -> Optional[str]:
        for message in reversed(session.history[-12:]):
            if message.role != Role.TOOL:
                continue
            urls = self._extract_urls(message.content)
            if urls:
                return urls[0]
        return None

    def _find_latest_artifact_url(self, session_id: str) -> Optional[str]:
        artifacts_dir = runner.get_session_workspace_artifacts_dir(session_id)
        if not os.path.isdir(artifacts_dir):
            return None

        latest_path = None
        latest_mtime = -1.0
        for root, _, files in os.walk(artifacts_dir):
            for name in files:
                candidate = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(candidate)
                except OSError:
                    continue
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_path = candidate

        if latest_path is None:
            return None

        relative = os.path.relpath(latest_path, runner.get_session_files_dir(session_id))
        return runner.build_session_file_url(session_id, f"files/{relative.replace(os.sep, '/')}")

    def _resolve_artifact_url_for_response(
        self, session: AgentSession, user_input: str, response_text: Optional[str]
    ) -> Optional[str]:
        if not self._user_requires_artifact_url(user_input):
            return None

        url = self._find_referenced_artifact_url(session.session_id, response_text)
        if url:
            return url

        url = self._find_recent_tool_url(session)
        if url:
            return url

        return self._find_latest_artifact_url(session.session_id)

    def _emergency_stop_requested(self, session: AgentSession) -> bool:
        return bool(session.metadata.get("emergency_stop_requested"))

    def _emergency_stop_response(self, session: AgentSession) -> str:
        public_session_id = session.metadata.get("public_session_id", session.session_id)
        return (
            f"EMERGENCY STOP: Session '{public_session_id}' was stopped by the user. "
            "The Docker runtime was shut down."
        )

    def _truncate_text(self, text: Optional[str], limit: int = 280) -> str:
        value = (text or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(limit - 3, 0)].rstrip() + "..."

    def _contains_sensitive_memory_content(self, content: str) -> bool:
        patterns = [
            r"\bpassword\b",
            r"\bpasswd\b",
            r"\bapi[_ -]?key\b",
            r"\bsecret\b",
            r"\btoken\b",
            r"\bbearer\b",
            r"\bsession cookie\b",
            r"sk-[A-Za-z0-9_-]{12,}",
            r"[A-Za-z0-9+/]{32,}={0,2}",
        ]
        lowered = (content or "").lower()
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)

    def _memory_expiry_days(self, item_type: str, requested_days: Optional[int]) -> Optional[int]:
        if requested_days is not None:
            return max(1, min(int(requested_days), 365))

        defaults = {
            "summary": 30,
            "incident": 60,
            "fact": None,
            "preference": None,
            "procedure": None,
        }
        return defaults.get(item_type)

    def _memory_expires_at(self, item_type: str, requested_days: Optional[int]) -> Optional[str]:
        expiry_days = self._memory_expiry_days(item_type, requested_days)
        if expiry_days is None:
            return None
        return datetime.utcfromtimestamp(time.time() + expiry_days * 86400).isoformat()

    def _normalize_memory_candidate(
        self,
        candidate: dict | str | None,
        *,
        default_type: Optional[str] = None,
        default_confidence: float = 0.75,
    ) -> Optional[dict]:
        if candidate is None:
            return None

        if isinstance(candidate, str):
            candidate = {"content": candidate}
        if not isinstance(candidate, dict):
            return None

        item_type = (candidate.get("type") or default_type or "").strip().lower()
        if item_type not in {"summary", "fact", "preference", "procedure", "incident"}:
            return None

        content = re.sub(r"\s+", " ", str(candidate.get("content") or "").strip())
        if len(content) < 12:
            return None
        if len(content) > 500:
            content = self._truncate_text(content, 500)

        if self._contains_sensitive_memory_content(content):
            return None

        try:
            confidence = float(candidate.get("confidence", default_confidence))
        except (TypeError, ValueError):
            confidence = default_confidence

        confidence = max(0.0, min(1.0, confidence))
        minimum_confidence = 0.5 if item_type == "summary" else 0.6
        if confidence < minimum_confidence:
            return None

        sensitivity = (candidate.get("sensitivity") or "medium").strip().lower()
        if sensitivity not in {"low", "medium", "high"}:
            sensitivity = "medium"

        expires_days = candidate.get("expires_days")
        try:
            expires_days = int(expires_days) if expires_days is not None else None
        except (TypeError, ValueError):
            expires_days = None

        return {
            "type": item_type,
            "content": content,
            "confidence": confidence,
            "sensitivity": sensitivity,
            "expires_at": self._memory_expires_at(item_type, expires_days),
            "replaces": [
                re.sub(r"\s+", " ", str(value).strip())
                for value in (candidate.get("replaces") or [])
                if str(value or "").strip()
            ][:5],
            "metadata": candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {},
        }

    def _recent_history_excerpt(self, messages: List[Message], limit: int = 10) -> str:
        excerpt = []
        for message in messages[-limit:]:
            if message.role == Role.SYSTEM:
                continue

            line = f"{message.role.value.upper()}: {self._truncate_text(message.content, 240)}"
            if message.role == Role.ASSISTANT and message.tool_calls:
                tool_names = ", ".join(tc.function for tc in message.tool_calls if tc.function)
                if tool_names:
                    line += f" | tool_calls={tool_names}"
            excerpt.append(line)
        return "\n".join(excerpt) if excerpt else "No recent non-system messages."

    def _build_memory_consolidation_prompt(self, snapshot: dict) -> str:
        tools_used = snapshot.get("tools_used") or []
        tools_block = "\n".join(
            f"- {tool.get('name', 'unknown')} ({tool.get('status', 'unknown')}, {tool.get('duration_ms', 0)} ms)"
            for tool in tools_used[:8]
        ) or "- No tools were used."
        existing_memory = snapshot.get("existing_memory") or []
        existing_memory_block = "\n".join(
            f"- [{item.get('type', 'unknown')}] {item.get('content', '')}"
            for item in existing_memory[:10]
        ) or "- No active structured memory yet."

        return (
            "You are consolidating post-session memory for a private local assistant.\n"
            "Return strict JSON only. No markdown.\n"
            "This is a low-latency, low-cost post-session consolidation task.\n"
            "Schema:\n"
            "{\n"
            '  "summary": {"type": "summary", "content": "...", "confidence": 0.0, "sensitivity": "low|medium|high", "expires_days": 30},\n'
            '  "items": [\n'
            '    {"type": "fact|preference|procedure|incident", "content": "...", "confidence": 0.0, "sensitivity": "low|medium|high", "expires_days": 30, "replaces": ["exact old content if obsolete"]}\n'
            "  ]\n"
            "}\n"
            "Rules:\n"
            "- Keep at most 1 summary and 5 items.\n"
            "- Keep only stable, reusable, user-relevant information.\n"
            "- Never store secrets, passwords, API keys, cookies, private tokens, raw credentials or one-off noisy details.\n"
            "- Facts are durable truths. Preferences are recurring user choices. Procedures are reusable successful workflows. Incidents are recurring failures or constraints.\n"
            "- If a new item supersedes an existing active memory, populate `replaces` with the exact old content string to retire.\n"
            "- Be concise and future-useful.\n"
            f"Session ID: {snapshot.get('public_session_id')}\n"
            f"Mission status: {snapshot.get('mission_status')}\n"
            f"User input: {self._truncate_text(snapshot.get('user_input'), 320)}\n"
            f"Final response: {self._truncate_text(snapshot.get('final_response'), 420)}\n"
            f"Tools used:\n{tools_block}\n"
            f"Existing active structured memory:\n{existing_memory_block}\n"
            f"Recent exchange:\n{snapshot.get('history_excerpt')}\n"
        )

    def _fallback_memory_consolidation_payload(self, snapshot: dict) -> dict:
        summary = {
            "type": "summary",
            "content": (
                f"Session '{snapshot.get('public_session_id')}' ended with status "
                f"'{snapshot.get('mission_status')}'. User asked: "
                f"{self._truncate_text(snapshot.get('user_input'), 120)}. "
                f"Outcome: {self._truncate_text(snapshot.get('final_response'), 180)}"
            ),
            "confidence": 0.78,
            "sensitivity": "medium",
            "expires_days": 30,
        }

        items = []
        if snapshot.get("mission_status") != "success":
            items.append(
                {
                    "type": "incident",
                    "content": (
                        f"A recent mission for session '{snapshot.get('public_session_id')}' ended with "
                        f"status '{snapshot.get('mission_status')}'. Review the last response before retrying."
                    ),
                    "confidence": 0.72,
                    "sensitivity": "medium",
                    "expires_days": 60,
                }
            )

        return {"summary": summary, "items": items}

    async def _generate_memory_consolidation_payload(self, snapshot: dict) -> dict:
        prompt = self._build_memory_consolidation_prompt(snapshot)

        try:
            response = await self.call_llm(
                [Message(role=Role.SYSTEM, content=prompt)],
                use_tools=False,
                model=self.memory_model,
                llm_role="memory",
            )
            payload_text = await self.collect_completion_text(response)
            payload = self._extract_json(payload_text)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

        return self._fallback_memory_consolidation_payload(snapshot)

    async def _consolidate_post_session_memory(self, snapshot: dict):
        logger = SessionLogger(snapshot["internal_session_id"])
        started_at = time.perf_counter()
        trace_token = _active_trace_context.set(None)

        logger.log_event(
            "memory_consolidation_started",
            mission_id=snapshot.get("mission_id"),
            public_session_id=snapshot.get("public_session_id"),
            internal_session_id=snapshot.get("internal_session_id"),
            auth_user_id=snapshot.get("auth_user_id"),
            auth_username=snapshot.get("auth_username"),
            mission_status=snapshot.get("mission_status"),
            memory_model=self.memory_model,
        )

        stored_items = []
        revised_items = []
        status = "completed"
        error_text = None

        try:
            await self.ltm.init_db()
            await self.ltm.cleanup_expired_memory_items(user_id=snapshot.get("auth_user_id"))
            payload = await self._generate_memory_consolidation_payload(snapshot)

            summary_candidate = self._normalize_memory_candidate(
                payload.get("summary"),
                default_type="summary",
                default_confidence=0.8,
            )
            if summary_candidate is not None:
                summary_candidate["metadata"].update(
                    {
                        "kind": "session_summary",
                        "mission_status": snapshot.get("mission_status"),
                        "tools_used": [tool["name"] for tool in snapshot.get("tools_used", [])],
                    }
                )
                stored_items.append(
                    await self.ltm.upsert_memory_item(
                        user_id=snapshot.get("auth_user_id"),
                        item_type=summary_candidate["type"],
                        content=summary_candidate["content"],
                        confidence=summary_candidate["confidence"],
                        sensitivity=summary_candidate["sensitivity"],
                        source_session_id=snapshot.get("public_session_id"),
                        source_mission_id=snapshot.get("mission_id"),
                        metadata=summary_candidate["metadata"],
                        expires_at=summary_candidate["expires_at"],
                    )
                )

            for raw_item in (payload.get("items") or [])[:5]:
                item = self._normalize_memory_candidate(raw_item)
                if item is None:
                    continue

                retired_items = []
                if item["replaces"]:
                    retired_items = await self.ltm.deactivate_memory_items(
                        user_id=snapshot.get("auth_user_id"),
                        contents=item["replaces"],
                        memory_types=[item["type"]],
                    )
                    revised_items.extend(retired_items)

                item["metadata"].update(
                    {
                        "kind": "structured_memory",
                        "mission_status": snapshot.get("mission_status"),
                        "supersedes": [retired["content"] for retired in retired_items],
                    }
                )
                stored_items.append(
                    await self.ltm.upsert_memory_item(
                        user_id=snapshot.get("auth_user_id"),
                        item_type=item["type"],
                        content=item["content"],
                        confidence=item["confidence"],
                        sensitivity=item["sensitivity"],
                        source_session_id=snapshot.get("public_session_id"),
                        source_mission_id=snapshot.get("mission_id"),
                        metadata=item["metadata"],
                        expires_at=item["expires_at"],
                    )
                )
        except Exception as exc:
            status = "error"
            error_text = str(exc)[:500]
        finally:
            logger.log_event(
                "memory_consolidation_completed",
                mission_id=snapshot.get("mission_id"),
                public_session_id=snapshot.get("public_session_id"),
                internal_session_id=snapshot.get("internal_session_id"),
                auth_user_id=snapshot.get("auth_user_id"),
                auth_username=snapshot.get("auth_username"),
                mission_status=snapshot.get("mission_status"),
                memory_model=self.memory_model,
                status=status,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                stored_items=len(stored_items),
                stored_types=[item["type"] for item in stored_items],
                revised_items=len(revised_items),
                revised_types=[item["type"] for item in revised_items],
                error=error_text,
            )
            _active_trace_context.reset(trace_token)

    def _schedule_post_session_consolidation(self, snapshot: dict):
        task = asyncio.create_task(self._consolidate_post_session_memory(snapshot))
        self._post_session_tasks.add(task)
        task.add_done_callback(self._post_session_tasks.discard)

    async def _build_structured_memory_context(self, user_input: str, auth_user_id: str | None) -> tuple[Optional[Message], int]:
        memory_items = await self.ltm.search_memory_items(
            user_input,
            user_id=auth_user_id,
            memory_types=["fact", "preference", "procedure", "incident"],
            limit=6,
        )
        summary_items = await self.ltm.list_memory_items(
            user_id=auth_user_id,
            memory_types=["summary"],
            limit=2,
        )

        if not memory_items and not summary_items:
            return None, 0

        sections = []
        if memory_items:
            grouped = {"fact": [], "preference": [], "procedure": [], "incident": []}
            for item in memory_items:
                grouped[item["type"]].append(f"- {item['content']}")

            section_titles = {
                "fact": "Known facts",
                "preference": "User preferences",
                "procedure": "Reusable procedures",
                "incident": "Known incidents or constraints",
            }
            for item_type in ["fact", "preference", "procedure", "incident"]:
                if grouped[item_type]:
                    sections.append(section_titles[item_type] + ":\n" + "\n".join(grouped[item_type]))

        if summary_items:
            summary_lines = [f"- {item['content']}" for item in summary_items]
            sections.append("Recent session summaries:\n" + "\n".join(summary_lines))

        item_ids = [item["id"] for item in [*memory_items, *summary_items] if item.get("id")]
        if item_ids:
            await self.ltm.mark_memory_items_used(item_ids)

        return (
            Message(
                role=Role.SYSTEM,
                content="Relevant structured memory:\n" + "\n\n".join(sections),
            ),
            len(item_ids),
        )

    async def _list_active_structured_memory_for_revision(self, auth_user_id: str | None, limit: int = 10) -> list[dict]:
        memory_items = await self.ltm.list_memory_items(
            user_id=auth_user_id,
            memory_types=["fact", "preference", "procedure", "incident"],
            limit=limit,
        )
        return [
            {
                "id": item["id"],
                "type": item["type"],
                "content": item["content"],
                "confidence": item["confidence"],
            }
            for item in memory_items
        ]

    async def _stream_with_buffer(self, session_id: str, stream, buffer_size: int = 10):
        """
        Stream avec buffering pour reduire la charge reseau sur le WebSocket.
        """
        buffer = ""
        chunk_count = 0

        async for delta in self.iter_completion_deltas(stream):
            content = getattr(delta, "content", None) or ""
            if content:
                buffer += content
                chunk_count += 1

            if buffer and (chunk_count >= buffer_size or any(c in content for c in ".!?\n")):
                await event_bus.broadcast(
                    session_id,
                    "THOUGHT",
                    {
                        "content": buffer,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                buffer = ""
                chunk_count = 0

            yield delta

        if buffer:
            await event_bus.broadcast(
                session_id,
                "THOUGHT",
                {
                    "content": buffer,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

    async def _execute_single_tool(self, session_id: str, tc: ToolCall, attempt: int) -> tuple[str, str]:
        """
        Execute un outil unique avec gestion complete des erreurs et securite.
        Retourne: (result, event_type)
        """
        started_at = time.perf_counter()
        trace_context = self._get_trace_context()
        tool_call_index = None
        if trace_context is not None:
            trace_context["tool_call_index"] = trace_context.get("tool_call_index", 0) + 1
            trace_context["tool_calls"] = trace_context.get("tool_calls", 0) + 1
            trace_context["attempt_tool_calls"] = trace_context.get("attempt_tool_calls", 0) + 1
            tool_call_index = trace_context["tool_call_index"]

        event_type = "TOOL_ERROR"
        result = ""
        result_telemetry = {}

        await event_bus.broadcast(
            session_id,
            "TOOL_START",
            {
                "name": tc.function,
                "attempt": attempt + 1,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        try:
            try:
                args = self._extract_json(tc.arguments)
            except ValueError as e:
                result = f"PARSE ERROR: Invalid arguments JSON - {str(e)}"
                return result, event_type

            verdict = superego.analyze_command(tc.function, args)

            if verdict.level == RiskLevel.BLOCKED:
                result = f"SECURITY BLOCKED: {verdict.reason}"
                if verdict.suggestion:
                    result += f" | Suggestion: {verdict.suggestion}"
                event_type = "SECURITY_ALERT"
                return result, event_type

            if verdict.level == RiskLevel.SUSPICIOUS:
                await event_bus.broadcast(
                    session_id,
                    "SECURITY_WARNING",
                    {
                        "msg": f"Suspicious activity: {verdict.reason}",
                        "function": tc.function,
                    },
                )

            func = registry.tools.get(tc.function)
            if not func:
                result = f"ERROR: Tool '{tc.function}' not found in registry"
                return result, event_type

            with tool_runtime_context(
                session_id=session_id,
                tool_name=tc.function,
                auth_user_id=trace_context.get("auth_user_id") if trace_context else None,
                auth_username=trace_context.get("auth_username") if trace_context else None,
            ):
                result = await asyncio.wait_for(func(**args), timeout=120.0)

            if hasattr(result, "telemetry") and isinstance(getattr(result, "telemetry"), dict):
                result_telemetry = dict(getattr(result, "telemetry"))

            result_str = str(result)
            if any(err in result_str.lower() for err in ["error", "exception", "failed", "timeout"]):
                result = result_str
                return result, event_type

            result = result_str
            event_type = "TOOL_SUCCESS"
            return result, event_type

        except asyncio.TimeoutError:
            result = f"TIMEOUT: Tool '{tc.function}' exceeded 120s limit"
            return result, event_type
        except Exception as e:
            error_trace = traceback.format_exc()
            self._last_error = str(e)
            result = f"EXECUTION ERROR: {str(e)}\n{error_trace[:500]}"
            return result, event_type
        finally:
            if trace_context is not None and event_type in {"TOOL_ERROR", "SECURITY_ALERT"}:
                trace_context["attempt_error_count"] = trace_context.get("attempt_error_count", 0) + 1
                trace_context["tool_error_count"] = trace_context.get("tool_error_count", 0) + 1

            self._trace_event(
                "tool_call_completed",
                tool_call_index=tool_call_index,
                attempt=attempt + 1,
                tool_name=tc.function,
                tool_call_id=tc.id,
                status=event_type.lower(),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                result_preview=result[:240] if result else None,
                **result_telemetry,
            )

    async def chat(self, session: AgentSession, user_input: str, profile_name: str = "orchestrateur") -> str:
        """
        Boucle de conversation principale avec gestion complete du contexte,
        de la securite, et de la resilience cognitive.
        """
        logger = SessionLogger(session.session_id)
        auth_user_id = session.metadata.get("auth_user_id")
        public_session_id = session.metadata.get("public_session_id", session.session_id)
        trace_context = {
            "logger": logger,
            "mission_id": str(uuid.uuid4()),
            "public_session_id": public_session_id,
            "internal_session_id": session.session_id,
            "auth_user_id": auth_user_id,
            "auth_username": session.metadata.get("auth_username"),
            "llm_call_index": 0,
            "tool_call_index": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_error_count": 0,
            "attempt_count": 0,
            "current_attempt": None,
        }
        mission_started_at = time.perf_counter()
        mission_status = "running"
        final_response = None
        trace_token = _active_trace_context.set(trace_context)

        self._trace_event(
            "mission_started",
            profile_name=profile_name,
            max_attempts=10,
            user_input_preview=user_input[:240],
        )

        try:
            if self._emergency_stop_requested(session):
                mission_status = "emergency_stopped"
                final_response = self._emergency_stop_response(session)
                return final_response

            try:
                await self.ltm.init_db()
            except Exception as e:
                print(f"[Engine] L3 Memory init failed: {e}")

            if not session.history:
                sys_prompt = self.profile_manager.get_profile(
                    profile_name,
                    {"date": datetime.utcnow().isoformat(), "session_id": public_session_id},
                )
                session.history.append(Message(role=Role.SYSTEM, content=sys_prompt))
                session.history.append(Message(role=Role.SYSTEM, content=skill_manager.get_catalog()))

            user_msg = Message(role=Role.USER, content=user_input)
            session.history.append(user_msg)
            logger.log(user_msg.model_dump(exclude_none=True))
            memory_hit_count = 0

            try:
                structured_context_msg, structured_count = await self._build_structured_memory_context(
                    user_input,
                    auth_user_id,
                )
                if structured_context_msg is not None:
                    session.history.insert(-1, structured_context_msg)
                    memory_hit_count += structured_count
            except Exception as e:
                print(f"[Engine] Structured memory lookup failed: {e}")

            try:
                related_memories = await self.ltm.search_related(
                    user_input,
                    limit=3,
                    user_id=auth_user_id,
                )
                if related_memories:
                    memory_hit_count += len(related_memories)
                    context_msg = Message(
                        role=Role.SYSTEM,
                        content="Context from past sessions:\n" + "\n".join(f"- {m}" for m in related_memories),
                    )
                    session.history.insert(-1, context_msg)
            except Exception as e:
                print(f"[Engine] L3 search failed: {e}")

            if memory_hit_count:
                await event_bus.broadcast(
                    session.session_id,
                    "MEMORY_HIT",
                    {
                        "count": memory_hit_count,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )

            consecutive_errors = 0
            mission_tools_used = []

            for attempt in range(10):
                attempt_number = attempt + 1
                trace_context["current_attempt"] = attempt_number
                trace_context["attempt_count"] = attempt_number
                trace_context["attempt_llm_calls"] = 0
                trace_context["attempt_tool_calls"] = 0
                trace_context["attempt_error_count"] = 0
                trace_context["attempt_status"] = "running"

                raw_tc = {}
                full_content = ""
                tool_calls = []
                attempt_started_at = time.perf_counter()

                self._trace_event(
                    "subtask_started",
                    subtask_type="agent_attempt",
                    attempt=attempt_number,
                )

                try:
                    if self._emergency_stop_requested(session):
                        final_response = self._emergency_stop_response(session)
                        mission_status = "emergency_stopped"
                        trace_context["attempt_status"] = "emergency_stopped"
                        break

                    session.history = await self.context_manager.compact_if_needed(session.history, self)

                    await event_bus.broadcast(
                        session.session_id,
                        "THOUGHT_START",
                        {
                            "attempt": attempt_number,
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    )

                    stream = await self.call_llm(session.history)
                    stream_interrupted = False

                    async for delta in self._stream_with_buffer(session.session_id, stream):
                        if self._emergency_stop_requested(session):
                            final_response = self._emergency_stop_response(session)
                            mission_status = "emergency_stopped"
                            trace_context["attempt_status"] = "stream_interrupted"
                            stream_interrupted = True
                            break

                        content = getattr(delta, "content", None)
                        if content:
                            full_content += content

                        delta_tool_calls = getattr(delta, "tool_calls", None)
                        if delta_tool_calls:
                            for fallback_idx, tcc in enumerate(delta_tool_calls):
                                idx = getattr(tcc, "index", fallback_idx)
                                if idx not in raw_tc:
                                    raw_tc[idx] = {"id": "", "name": "", "args": ""}

                                tc_id = getattr(tcc, "id", None)
                                if tc_id:
                                    raw_tc[idx]["id"] = tc_id

                                function = getattr(tcc, "function", None)
                                function_name = getattr(function, "name", None)
                                function_args = getattr(function, "arguments", None)

                                if function_name:
                                    raw_tc[idx]["name"] += function_name
                                if function_args:
                                    raw_tc[idx]["args"] += function_args

                    if stream_interrupted:
                        break

                    tool_calls = [
                        ToolCall(id=v["id"], function=v["name"], arguments=v["args"])
                        for v in raw_tc.values()
                        if v["name"]
                    ]

                    assistant_msg = Message(
                        role=Role.ASSISTANT,
                        content=full_content if full_content else None,
                        tool_calls=tool_calls if tool_calls else None,
                    )
                    session.history.append(assistant_msg)
                    logger.log(assistant_msg.model_dump(exclude_none=True))

                    if not tool_calls:
                        final_response = full_content
                        trace_context["attempt_status"] = "completed_without_tools"
                        mission_status = "success"

                        if self._user_requires_artifact_url(user_input) and not self._response_contains_url(final_response):
                            artifact_url = self._resolve_artifact_url_for_response(
                                session, user_input, final_response
                            )
                            if artifact_url:
                                final_response = (
                                    (final_response or "").rstrip() +
                                    ("\n\n" if final_response else "") +
                                    f"Artifact URL: {artifact_url}"
                                )
                            else:
                                trace_context["attempt_status"] = "retry_missing_artifact_url"
                                mission_status = "running"
                                final_response = None
                                session.history.append(
                                    Message(
                                        role=Role.SYSTEM,
                                        content=(
                                            "The user explicitly requested a direct artifact URL. "
                                            "Do not conclude without providing a /session-files/... link. "
                                            "Use get_artifact_url(path) if an artifact exists."
                                        ),
                                    )
                                )
                                continue

                        try:
                            await self.resilience.register_success(
                                user_input,
                                f"Success: {full_content[:500]}",
                                user_id=auth_user_id,
                            )
                            await self.ltm.archive_message(
                                content=f"Q: {user_input}\nA: {full_content[:1000]}",
                                metadata={
                                    "type": "successful_interaction",
                                    "session_id": public_session_id,
                                    "auth_user_id": auth_user_id,
                                    "tools_used": [],
                                },
                                user_id=auth_user_id,
                            )
                        except Exception as e:
                            print(f"[Engine] L3 archive failed: {e}")

                        break

                    error_occurred = False
                    fatal_tool_failure = False
                    diagnoses = []
                    last_tool_error = None
                    trace_context["attempt_status"] = "executing_tools"

                    for tc in tool_calls:
                        if self._emergency_stop_requested(session):
                            final_response = self._emergency_stop_response(session)
                            mission_status = "emergency_stopped"
                            trace_context["attempt_status"] = "emergency_stopped"
                            break

                        tool_started_at = time.perf_counter()
                        result, event_type = await self._execute_single_tool(
                            session.session_id, tc, attempt
                        )
                        tool_duration_ms = round((time.perf_counter() - tool_started_at) * 1000, 2)
                        mission_tools_used.append(
                            {
                                "name": tc.function,
                                "status": event_type.lower(),
                                "duration_ms": tool_duration_ms,
                            }
                        )

                        event_payload = {
                            "name": tc.function,
                            "result": result[:500] if len(result) > 500 else result,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                        if event_type == "TOOL_ERROR":
                            event_payload["error"] = event_payload["result"]
                        elif event_type == "SECURITY_ALERT":
                            event_payload["msg"] = event_payload["result"]

                        await event_bus.broadcast(session.session_id, event_type, event_payload)

                        tool_msg = Message(
                            role=Role.TOOL,
                            tool_call_id=tc.id,
                            content=result,
                        )
                        session.history.append(tool_msg)
                        logger.log(tool_msg.model_dump(exclude_none=True))

                        if event_type in ["TOOL_ERROR", "SECURITY_ALERT"]:
                            error_occurred = True
                            consecutive_errors += 1
                            self._last_error = result
                            last_tool_error = result

                            diagnosis = await self.resilience.analyze_and_learn(
                                result,
                                f"{tc.function} with args {tc.arguments[:100]}",
                                user_id=auth_user_id,
                            )
                            diagnoses.append(diagnosis)

                            if consecutive_errors >= 3 or "CRITICAL ALERT" in diagnosis:
                                fatal_tool_failure = True

                        if self._emergency_stop_requested(session):
                            final_response = self._emergency_stop_response(session)
                            mission_status = "emergency_stopped"
                            trace_context["attempt_status"] = "emergency_stopped"
                            break

                    if final_response is not None:
                        break

                    if error_occurred:
                        if fatal_tool_failure:
                            final_response = (
                                f"Aborted after {consecutive_errors} consecutive errors. "
                                f"Last: {(last_tool_error or '')[:200]}"
                            )
                            mission_status = "fatal_tool_failure"
                            trace_context["attempt_status"] = "fatal_tool_failure"
                            break

                        unique_diagnoses = []
                        for diagnosis in diagnoses:
                            if diagnosis not in unique_diagnoses:
                                unique_diagnoses.append(diagnosis)

                        repair_lines = "\n".join(f"- {d}" for d in unique_diagnoses[:3])
                        repair_msg = Message(
                            role=Role.SYSTEM,
                            content=(
                                "Previous actions failed.\n"
                                f"{repair_lines}\n"
                                "Try an alternative approach and do not repeat the same failing tool pattern."
                            ),
                        )
                        session.history.append(repair_msg)
                        trace_context["attempt_status"] = "retry_after_tool_error"
                        continue

                    if consecutive_errors >= 3:
                        mission_status = "fatal_tool_failure"
                        trace_context["attempt_status"] = "aborted_after_consecutive_errors"
                        break

                    if not error_occurred:
                        consecutive_errors = 0
                        trace_context["attempt_status"] = "tool_phase_completed"

                except Exception as e:
                    if self._emergency_stop_requested(session):
                        final_response = self._emergency_stop_response(session)
                        mission_status = "emergency_stopped"
                        trace_context["attempt_status"] = "emergency_stopped"
                        break

                    error_trace = traceback.format_exc()
                    error_text = str(e)
                    await event_bus.broadcast(
                        session.session_id,
                        "ENGINE_CRITICAL",
                        {
                            "error": error_text,
                            "trace": error_trace[:1000],
                            "attempt": attempt_number,
                        },
                    )

                    if (
                        "tool_calls" in error_text
                        and "tool_call_id" in error_text
                    ):
                        final_response = (
                            "Critical protocol error while handling tool calls: "
                            f"{error_text}"
                        )
                        mission_status = "critical_protocol_error"
                        trace_context["attempt_status"] = "critical_protocol_error"
                        break

                    if attempt == 9:
                        final_response = f"Critical system error after 10 attempts: {error_text}"
                        mission_status = "critical_system_error"
                        trace_context["attempt_status"] = "critical_system_error"
                        break

                    session.history.append(
                        Message(
                            role=Role.SYSTEM,
                            content=f"System encountered an error: {error_text[:200]}. Retrying...",
                        )
                    )
                    trace_context["attempt_status"] = "retry_after_engine_exception"
                    continue
                finally:
                    self._trace_event(
                        "subtask_completed",
                        subtask_type="agent_attempt",
                        attempt=attempt_number,
                        status=trace_context.get("attempt_status", "unknown"),
                        duration_ms=round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        llm_calls=trace_context.get("attempt_llm_calls", 0),
                        tool_calls=trace_context.get("attempt_tool_calls", 0),
                        error_count=trace_context.get("attempt_error_count", 0),
                        assistant_chars=len(full_content),
                        tool_calls_requested=len(tool_calls),
                    )

            if final_response is None:
                mission_status = "max_attempts_reached" if mission_status == "running" else mission_status
                final_response = "Unable to complete the mission after maximum attempts."

            if mission_status != "emergency_stopped":
                existing_memory = []
                try:
                    existing_memory = await self._list_active_structured_memory_for_revision(auth_user_id)
                except Exception as e:
                    print(f"[Engine] Active structured memory listing failed: {e}")

                snapshot = {
                    "mission_id": trace_context.get("mission_id"),
                    "public_session_id": public_session_id,
                    "internal_session_id": session.session_id,
                    "auth_user_id": auth_user_id,
                    "auth_username": session.metadata.get("auth_username"),
                    "mission_status": mission_status,
                    "user_input": user_input,
                    "final_response": final_response,
                    "tools_used": mission_tools_used,
                    "existing_memory": existing_memory,
                    "history_excerpt": self._recent_history_excerpt(session.history),
                }
                self._schedule_post_session_consolidation(snapshot)

            return final_response
        finally:
            self._trace_event(
                "mission_completed",
                profile_name=profile_name,
                status=mission_status,
                duration_ms=round((time.perf_counter() - mission_started_at) * 1000, 2),
                attempts=trace_context.get("attempt_count", 0),
                llm_calls=trace_context.get("llm_calls", 0),
                tool_calls=trace_context.get("tool_calls", 0),
                tool_error_count=trace_context.get("tool_error_count", 0),
                emergency_stop_requested=self._emergency_stop_requested(session),
                final_response_preview=(final_response or "")[:240],
            )
            _active_trace_context.reset(trace_token)
