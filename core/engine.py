import asyncio
import json
import litellm
import os
import re
import traceback
from datetime import datetime
from typing import List, Optional

from core.bus import event_bus
from core.context import ContextManager
from core.memory import LongTermMemory, SessionLogger
from core.models import AgentSession, Message, Role, ToolCall
from core.prompter import ProfileManager
from core.resilience import CognitiveResilience, ErrorInterpreter
from core.runner import runner
from core.runtime_context import tool_runtime_context
from core.skills import skill_manager
from core.superego import RiskLevel, superego
from core.tools import registry


class ZeroHitlEngine:
    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.context_manager = ContextManager(model=model)
        self.profile_manager = ProfileManager()
        self.ltm = LongTermMemory()
        self.resilience = CognitiveResilience(self.ltm, self)
        self.error_interpreter = ErrorInterpreter()
        self._last_error: Optional[str] = None

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

    async def call_llm(self, messages: List[Message], use_tools: bool = True):
        """
        Appelle le LLM avec gestion appropriee des roles et tool calls.
        """
        formatted = []
        for m in messages:
            d = m.model_dump(exclude_none=True)

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
            "model": self.model,
            "messages": formatted,
            "stream": True,
        }

        if use_tools and registry.schemas:
            kwargs["tools"] = registry.schemas
            kwargs["tool_choice"] = "auto"

        return await litellm.acompletion(**kwargs)

    def _extract_delta(self, chunk):
        if not chunk or not getattr(chunk, "choices", None):
            return None

        choice = chunk.choices[0]
        return getattr(choice, "delta", None) or getattr(choice, "message", None)

    async def iter_completion_deltas(self, response):
        if hasattr(response, "__aiter__"):
            async for chunk in response:
                delta = self._extract_delta(chunk)
                if delta is not None:
                    yield delta
            return

        delta = self._extract_delta(response)
        if delta is not None:
            yield delta

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
                return f"PARSE ERROR: Invalid arguments JSON - {str(e)}", "TOOL_ERROR"

            verdict = superego.analyze_command(tc.function, args)

            if verdict.level == RiskLevel.BLOCKED:
                result = f"SECURITY BLOCKED: {verdict.reason}"
                if verdict.suggestion:
                    result += f" | Suggestion: {verdict.suggestion}"
                return result, "SECURITY_ALERT"

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
                return f"ERROR: Tool '{tc.function}' not found in registry", "TOOL_ERROR"

            with tool_runtime_context(session_id=session_id, tool_name=tc.function):
                result = await asyncio.wait_for(func(**args), timeout=120.0)

            result_str = str(result)
            if any(err in result_str.lower() for err in ["error", "exception", "failed", "timeout"]):
                return result_str, "TOOL_ERROR"

            return result_str, "TOOL_SUCCESS"

        except asyncio.TimeoutError:
            return f"TIMEOUT: Tool '{tc.function}' exceeded 120s limit", "TOOL_ERROR"
        except Exception as e:
            error_trace = traceback.format_exc()
            self._last_error = str(e)
            return f"EXECUTION ERROR: {str(e)}\n{error_trace[:500]}", "TOOL_ERROR"

    async def chat(self, session: AgentSession, user_input: str, profile_name: str = "orchestrateur") -> str:
        """
        Boucle de conversation principale avec gestion complete du contexte,
        de la securite, et de la resilience cognitive.
        """
        logger = SessionLogger(session.session_id)

        try:
            await self.ltm.init_db()
        except Exception as e:
            print(f"[Engine] L3 Memory init failed: {e}")

        if not session.history:
            sys_prompt = self.profile_manager.get_profile(
                profile_name,
                {"date": datetime.utcnow().isoformat(), "session_id": session.session_id},
            )
            session.history.append(Message(role=Role.SYSTEM, content=sys_prompt))
            session.history.append(Message(role=Role.SYSTEM, content=skill_manager.get_catalog()))

        user_msg = Message(role=Role.USER, content=user_input)
        session.history.append(user_msg)
        logger.log(user_msg.model_dump(exclude_none=True))

        try:
            related_memories = await self.ltm.search_related(user_input, limit=3)
            if related_memories:
                await event_bus.broadcast(
                    session.session_id,
                    "MEMORY_HIT",
                    {
                        "count": len(related_memories),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                context_msg = Message(
                    role=Role.SYSTEM,
                    content="Context from past sessions:\n" + "\n".join(f"- {m}" for m in related_memories),
                )
                session.history.insert(-1, context_msg)
        except Exception as e:
            print(f"[Engine] L3 search failed: {e}")

        final_response = None
        consecutive_errors = 0

        for attempt in range(10):
            raw_tc = {}
            full_content = ""
            tool_calls = []

            try:
                session.history = await self.context_manager.compact_if_needed(session.history, self)

                await event_bus.broadcast(
                    session.session_id,
                    "THOUGHT_START",
                    {
                        "attempt": attempt + 1,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )

                stream = await self.call_llm(session.history)

                async for delta in self._stream_with_buffer(session.session_id, stream):
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
                        )
                        await self.ltm.archive_message(
                            content=f"Q: {user_input}\nA: {full_content[:1000]}",
                            metadata={
                                "type": "successful_interaction",
                                "session_id": session.session_id,
                                "tools_used": [],
                            },
                        )
                    except Exception as e:
                        print(f"[Engine] L3 archive failed: {e}")

                    break

                error_occurred = False
                fatal_tool_failure = False
                diagnoses = []
                last_tool_error = None

                for tc in tool_calls:
                    result, event_type = await self._execute_single_tool(
                        session.session_id, tc, attempt
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
                        )
                        diagnoses.append(diagnosis)

                        if consecutive_errors >= 3 or "CRITICAL ALERT" in diagnosis:
                            fatal_tool_failure = True

                if error_occurred:
                    if fatal_tool_failure:
                        final_response = (
                            f"Aborted after {consecutive_errors} consecutive errors. "
                            f"Last: {(last_tool_error or '')[:200]}"
                        )
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
                    continue

                if consecutive_errors >= 3:
                    break

                if not error_occurred:
                    consecutive_errors = 0

            except Exception as e:
                error_trace = traceback.format_exc()
                error_text = str(e)
                await event_bus.broadcast(
                    session.session_id,
                    "ENGINE_CRITICAL",
                    {
                        "error": error_text,
                        "trace": error_trace[:1000],
                        "attempt": attempt + 1,
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
                    break

                if attempt == 9:
                    final_response = f"Critical system error after 10 attempts: {error_text}"
                    break

                session.history.append(
                    Message(
                        role=Role.SYSTEM,
                        content=f"System encountered an error: {error_text[:200]}. Retrying...",
                    )
                )
                continue

        if final_response is None:
            final_response = "Unable to complete the mission after maximum attempts."

        return final_response
