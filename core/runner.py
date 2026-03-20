import atexit
import ntpath
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import docker
from dotenv import load_dotenv

from core.bus import event_bus
from core.memory import SessionLogger
from core.runtime_context import get_current_runtime_context, get_current_session_id

load_dotenv()


@dataclass
class SessionRuntime:
    session_id: str
    mode: str
    container_name: str
    exec_count: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class SandboxRunResult:
    output: str
    exit_code: Optional[int] = None
    telemetry: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.output


class SecureRunner:
    METRIC_PREFIX = "__0HITL_METRIC__:"

    def __init__(self):
        self.client = None
        self.client_error = None
        self.local_workspace = os.path.abspath("./workspace")
        self.local_skills = os.path.abspath("./skills")
        self.configured_host_workspace = os.getenv("HOST_WORKSPACE_PATH")
        self.configured_host_skills = os.getenv("HOST_SKILLS_PATH")
        self._detected_host_mounts: Dict[str, str] = {}
        self.runtimes: Dict[Tuple[str, str], SessionRuntime] = {}
        atexit.register(self.shutdown_all)

    def _is_windows_path(self, path: Optional[str]) -> bool:
        if not path:
            return False
        return bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")

    def _normalize_host_path(self, path: Optional[str], fallback: str) -> str:
        value = (path or "").strip()
        if not value:
            return fallback
        if os.path.isabs(value) or self._is_windows_path(value):
            return value
        return os.path.abspath(value)

    def _join_host_path(self, base: str, *parts: str) -> str:
        if self._is_windows_path(base):
            return ntpath.join(base, *parts)
        return os.path.join(base, *parts)

    def _detect_self_mount_source(self, container_target: str) -> Optional[str]:
        cached = self._detected_host_mounts.get(container_target)
        if cached:
            return cached

        if not os.path.exists("/.dockerenv"):
            return None

        container_id = os.getenv("HOSTNAME")
        if not container_id:
            return None

        try:
            client = self._get_client()
            container = client.containers.get(container_id)
            for mount in container.attrs.get("Mounts", []):
                destination = str(mount.get("Destination", "")).rstrip("/")
                source = mount.get("Source")
                if destination == container_target.rstrip("/") and source:
                    self._detected_host_mounts[container_target] = str(source)
                    return self._detected_host_mounts[container_target]
        except Exception:
            return None

        return None

    def _get_host_workspace_root(self) -> str:
        detected = self._detect_self_mount_source("/app/workspace")
        if detected:
            return detected
        return self._normalize_host_path(self.configured_host_workspace, self.local_workspace)

    def _get_host_skills_root(self) -> str:
        detected = self._detect_self_mount_source("/app/skills")
        if detected:
            return detected
        return self._normalize_host_path(self.configured_host_skills, self.local_skills)

    def _get_host_session_root(self, session_id: Optional[str] = None) -> str:
        safe_session_id = self._resolve_session_id(session_id)
        return self._join_host_path(self._get_host_workspace_root(), "sessions", safe_session_id)

    def _get_host_session_files_dir(self, session_id: Optional[str] = None) -> str:
        return self._join_host_path(self._get_host_session_root(session_id), "files")

    def _get_client(self):
        if self.client is not None:
            return self.client

        if self.client_error is not None:
            raise RuntimeError(self.client_error)

        try:
            self.client = docker.from_env()
            self.client.ping()
            return self.client
        except Exception as e:
            self.client_error = f"Docker unavailable: {e}"
            raise RuntimeError(self.client_error) from e

    def _sanitize_session_id(self, session_id: Optional[str]) -> str:
        raw = session_id or "default"
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-.")
        return safe or "default"

    def _resolve_session_id(self, session_id: Optional[str] = None) -> str:
        return self._sanitize_session_id(session_id or get_current_session_id("default"))

    def get_session_root(self, session_id: Optional[str] = None) -> str:
        safe_session_id = self._resolve_session_id(session_id)
        return os.path.join(self.local_workspace, "sessions", safe_session_id)

    def get_session_files_dir(self, session_id: Optional[str] = None) -> str:
        return os.path.join(self.get_session_root(session_id), "files")

    def get_session_artifacts_dir(self, session_id: Optional[str] = None) -> str:
        return os.path.join(self.get_session_root(session_id), "artifacts")

    def get_session_workspace_artifacts_dir(self, session_id: Optional[str] = None) -> str:
        return os.path.join(self.get_session_files_dir(session_id), "artifacts")

    def get_session_venv_dir(self, session_id: Optional[str] = None) -> str:
        return os.path.join(self.get_session_root(session_id), ".venv")

    def get_session_cache_dir(self, session_id: Optional[str] = None) -> str:
        return os.path.join(self.get_session_root(session_id), ".cache")

    def ensure_session_dirs(self, session_id: Optional[str] = None) -> str:
        safe_session_id = self._resolve_session_id(session_id)
        os.makedirs(self.get_session_files_dir(safe_session_id), exist_ok=True)
        os.makedirs(self.get_session_artifacts_dir(safe_session_id), exist_ok=True)
        os.makedirs(self.get_session_workspace_artifacts_dir(safe_session_id), exist_ok=True)
        os.makedirs(self.get_session_cache_dir(safe_session_id), exist_ok=True)
        return safe_session_id

    def build_session_file_url(self, session_id: str, relative_path: str) -> str:
        safe_session_id = self._resolve_session_id(session_id)
        normalized = relative_path.replace("\\", "/").lstrip("/")
        return f"/session-files/{safe_session_id}/{normalized}"

    def _runtime_key(self, session_id: str, network: bool) -> Tuple[str, str]:
        return (session_id, "online" if network else "offline")

    def _container_name(self, session_id: str, network: bool) -> str:
        mode = "online" if network else "offline"
        return f"zero-hitl-{session_id}-{mode}"

    def _runtime_metrics(self, session_id: str, runtime: SessionRuntime) -> dict:
        active_runtimes = len(self.runtimes)
        total_execs = sum(item.exec_count for item in self.runtimes.values())
        session_execs = sum(
            item.exec_count for item in self.runtimes.values() if item.session_id == session_id
        )
        return {
            "session_id": session_id,
            "mode": runtime.mode,
            "active_runtimes": active_runtimes,
            "session_execs": session_execs,
            "total_execs": total_execs,
        }

    def _coerce_metric_value(self, value: str):
        normalized = (value or "").strip()
        lowered = normalized.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            if "." in normalized:
                return round(float(normalized), 2)
            return int(normalized)
        except (TypeError, ValueError):
            return normalized

    def _extract_embedded_metrics(self, logs: str) -> tuple[str, dict]:
        clean_lines = []
        metrics = {}

        for raw_line in (logs or "").splitlines():
            line = raw_line.rstrip("\r")
            if line.startswith(self.METRIC_PREFIX):
                payload = line[len(self.METRIC_PREFIX) :].strip()
                if "=" in payload:
                    key, value = payload.split("=", 1)
                    metrics[key.strip()] = self._coerce_metric_value(value)
                continue
            clean_lines.append(raw_line)

        cleaned_output = "\n".join(clean_lines)
        if logs.endswith("\n") and clean_lines:
            cleaned_output += "\n"
        return cleaned_output, metrics

    def runtime_status_snapshot(self, session_id: str, mode: str = "stopped") -> dict:
        safe_session_id = self._resolve_session_id(session_id)
        active_runtimes = len(self.runtimes)
        total_execs = sum(item.exec_count for item in self.runtimes.values())
        session_execs = sum(
            item.exec_count for item in self.runtimes.values() if item.session_id == safe_session_id
        )
        return {
            "session_id": safe_session_id,
            "mode": mode,
            "active_runtimes": active_runtimes,
            "session_execs": session_execs,
            "total_execs": total_execs,
        }

    def _ensure_runtime(self, session_id: str, network: bool):
        client = self._get_client()
        safe_session_id = self.ensure_session_dirs(session_id)
        requested_runtime_key = self._runtime_key(safe_session_id, network)
        online_runtime_key = self._runtime_key(safe_session_id, True)
        offline_runtime_key = self._runtime_key(safe_session_id, False)

        if not network:
            existing_runtime = self.runtimes.get(online_runtime_key) or SessionRuntime(
                session_id=safe_session_id,
                mode="online",
                container_name=self._container_name(safe_session_id, True),
            )
            try:
                container = client.containers.get(existing_runtime.container_name)
            except docker.errors.NotFound:
                container = None

        else:
            existing_runtime = None
            container = None

        if container is not None:
            container.reload()
            container_start_ms = 0.0
            startup_kind = "reused"
            if container.status != "running":
                start_started_at = time.perf_counter()
                container.start()
                container_start_ms = round((time.perf_counter() - start_started_at) * 1000, 2)
                startup_kind = "restarted"
            self.runtimes[online_runtime_key] = existing_runtime
            return container, existing_runtime, False, {
                "cold_start": False,
                "runtime_reused": True,
                "startup_kind": startup_kind,
                "container_start_ms": container_start_ms,
            }

        if network and offline_runtime_key in self.runtimes:
            self._shutdown_runtime(safe_session_id, False)

        runtime = self.runtimes.get(requested_runtime_key)

        container_name = self._container_name(safe_session_id, network)
        created = False

        try:
            container = client.containers.get(container_name)
            container.reload()
            container_start_ms = 0.0
            startup_kind = "reused"
            if container.status != "running":
                start_started_at = time.perf_counter()
                container.start()
                container_start_ms = round((time.perf_counter() - start_started_at) * 1000, 2)
                startup_kind = "restarted"
        except docker.errors.NotFound:
            start_started_at = time.perf_counter()
            volumes = {
                self._get_host_session_root(safe_session_id): {"bind": "/session", "mode": "rw"},
                self._get_host_session_files_dir(safe_session_id): {"bind": "/app", "mode": "rw"},
            }
            if os.path.isdir(self.local_skills):
                volumes[self._get_host_skills_root()] = {"bind": "/skills", "mode": "ro"}

            container = client.containers.run(
                image="python:3.12-slim",
                name=container_name,
                command=[
                    "bash",
                    "-lc",
                    "mkdir -p /session/files /session/artifacts /session/.cache && while true; do sleep 3600; done",
                ],
                volumes=volumes,
                working_dir="/app",
                mem_limit="512m",
                network_disabled=not network,
                auto_remove=False,
                detach=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges=true"],
                pids_limit=256,
                labels={
                    "zero_hitl.session_id": safe_session_id,
                    "zero_hitl.mode": "online" if network else "offline",
                },
            )
            container_start_ms = round((time.perf_counter() - start_started_at) * 1000, 2)
            startup_kind = "cold_start"
            created = True

        if runtime is None:
            runtime = SessionRuntime(
                session_id=safe_session_id,
                mode="online" if network else "offline",
                container_name=container_name,
            )
            self.runtimes[requested_runtime_key] = runtime

        return container, runtime, created, {
            "cold_start": created,
            "runtime_reused": not created,
            "startup_kind": startup_kind,
            "container_start_ms": container_start_ms,
        }

    def _build_exec_command(self, session_id: str, command: str, timeout: int):
        safe_session_id = self._resolve_session_id(session_id)
        inner_command = "\n".join(
            [
                "set -e",
                "now_ms() { date +%s%3N; }",
                f'emit_metric() {{ printf "{self.METRIC_PREFIX}%s=%s\\n" "$1" "$2"; }}',
                "mkdir -p /session/files /session/artifacts /session/.cache/pip /session/.cache/matplotlib",
                "HITL_VENV_START=$(now_ms)",
                'if [ ! -x /session/.venv/bin/python ]; then python -m venv /session/.venv; HITL_VENV_CREATED=true; else HITL_VENV_CREATED=false; fi',
                ". /session/.venv/bin/activate",
                "HITL_VENV_END=$(now_ms)",
                'emit_metric "venv_bootstrap_ms" "$((HITL_VENV_END-HITL_VENV_START))"',
                'emit_metric "venv_created" "$HITL_VENV_CREATED"',
                "export PYTHONUNBUFFERED=1",
                "export PIP_DISABLE_PIP_VERSION_CHECK=1",
                "export PIP_CACHE_DIR=/session/.cache/pip",
                "export MPLCONFIGDIR=/session/.cache/matplotlib",
                f"export HITL_SESSION_ID={shlex.quote(safe_session_id)}",
                "export HITL_SESSION_ROOT=/session",
                "export HITL_WORKSPACE=/app",
                "export HITL_ARTIFACTS=/session/artifacts",
                "cd /app",
                "HITL_COMMAND_START=$(now_ms)",
                "set +e",
                f"eval {shlex.quote(command)}",
                "HITL_COMMAND_EXIT=$?",
                "set -e",
                "HITL_COMMAND_END=$(now_ms)",
                'emit_metric "command_wall_ms" "$((HITL_COMMAND_END-HITL_COMMAND_START))"',
                'emit_metric "command_exit_code" "$HITL_COMMAND_EXIT"',
                "exit $HITL_COMMAND_EXIT",
            ]
        )
        wrapped_command = (
            f"timeout --signal=KILL {int(timeout)}s bash -lc {shlex.quote(inner_command)}"
        )
        return ["bash", "-lc", wrapped_command]

    async def _broadcast_runtime_status(self, session_id: str, runtime: SessionRuntime, created: bool):
        payload = self._runtime_metrics(session_id, runtime)
        payload["created"] = created
        await event_bus.broadcast(session_id, "RUNTIME_STATUS", payload)

    async def run_in_sandbox(
        self,
        command: str,
        skill_path: Optional[str] = None,
        network: bool = False,
        timeout: int = 120,
        session_id: Optional[str] = None,
    ):
        del skill_path
        started_at = time.perf_counter()
        requested_network = bool(network)

        lowered_command = command.lower()
        if any(x in lowered_command for x in ["pip ", "curl ", "wget ", "apt ", "python -m pip"]):
            network = True
            timeout = max(timeout, 600)

        safe_session_id = self._resolve_session_id(session_id)

        try:
            container, runtime, created, runtime_telemetry = self._ensure_runtime(safe_session_id, network)
        except RuntimeError as e:
            return SandboxRunResult(
                output=f"Sandbox Error: {e}",
                exit_code=None,
                telemetry={
                    "requested_network": requested_network,
                    "effective_network": network,
                    "startup_kind": "error",
                    "sandbox_roundtrip_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )
        except Exception as e:
            return SandboxRunResult(
                output=f"Sandbox Error: {e}",
                exit_code=None,
                telemetry={
                    "requested_network": requested_network,
                    "effective_network": network,
                    "startup_kind": "error",
                    "sandbox_roundtrip_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            )

        runtime.exec_count += 1
        await self._broadcast_runtime_status(safe_session_id, runtime, created)

        runtime_context = get_current_runtime_context()
        tool_name = runtime_context.tool_name if runtime_context is not None else None
        exec_started_at = time.perf_counter()

        try:
            exec_result = container.exec_run(
                cmd=self._build_exec_command(safe_session_id, command, timeout),
                workdir="/app",
                demux=False,
            )
            docker_exec_ms = round((time.perf_counter() - exec_started_at) * 1000, 2)
            output = exec_result.output or b""
            if isinstance(output, bytes):
                logs = output.decode("utf-8", errors="replace")
            else:
                logs = str(output)

            exit_code = exec_result.exit_code
            clean_logs, embedded_metrics = self._extract_embedded_metrics(logs)
            telemetry = {
                **runtime_telemetry,
                **self._runtime_metrics(safe_session_id, runtime),
                **embedded_metrics,
                "requested_network": requested_network,
                "effective_network": network,
                "runtime_mode": runtime.mode,
                "docker_exec_ms": docker_exec_ms,
                "sandbox_roundtrip_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "command_length": len(command),
            }

            SessionLogger(safe_session_id).log_event(
                "sandbox_command_completed",
                tool_name=tool_name,
                status="success" if exit_code == 0 else "non_zero_exit",
                exit_code=exit_code,
                command_preview=command[:240],
                **telemetry,
            )

            if exit_code != 0:
                return SandboxRunResult(
                    output=f"Exit code {exit_code}: {clean_logs}",
                    exit_code=exit_code,
                    telemetry=telemetry,
                )
            return SandboxRunResult(
                output=clean_logs,
                exit_code=exit_code,
                telemetry=telemetry,
            )
        except Exception as e:
            docker_exec_ms = round((time.perf_counter() - exec_started_at) * 1000, 2)
            telemetry = {
                **runtime_telemetry,
                **self._runtime_metrics(safe_session_id, runtime),
                "requested_network": requested_network,
                "effective_network": network,
                "runtime_mode": runtime.mode,
                "docker_exec_ms": docker_exec_ms,
                "sandbox_roundtrip_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "command_length": len(command),
            }
            SessionLogger(safe_session_id).log_event(
                "sandbox_command_completed",
                tool_name=tool_name,
                status="sandbox_error",
                exit_code=None,
                command_preview=command[:240],
                error=str(e)[:500],
                **telemetry,
            )
            return SandboxRunResult(
                output=f"Sandbox Error: {str(e)}",
                exit_code=None,
                telemetry=telemetry,
            )

    def shutdown_session(self, session_id: str):
        safe_session_id = self._resolve_session_id(session_id)

        for network in [False, True]:
            self._shutdown_runtime(safe_session_id, network)

    def _shutdown_runtime(self, session_id: str, network: bool):
        safe_session_id = self._resolve_session_id(session_id)
        runtime_key = self._runtime_key(safe_session_id, network)
        container_name = self._container_name(safe_session_id, network)

        try:
            client = self._get_client()
        except RuntimeError:
            self.runtimes.pop(runtime_key, None)
            return

        try:
            container = client.containers.get(container_name)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
        except Exception:
            pass

        self.runtimes.pop(runtime_key, None)

    def shutdown_all(self):
        try:
            client = self._get_client()
        except RuntimeError:
            return

        for runtime in list(self.runtimes.values()):
            try:
                container = client.containers.get(runtime.container_name)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
            except Exception:
                pass
        self.runtimes.clear()


runner = SecureRunner()
