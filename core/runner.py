import atexit
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import docker

from core.bus import event_bus
from core.runtime_context import get_current_session_id


@dataclass
class SessionRuntime:
    session_id: str
    mode: str
    container_name: str
    exec_count: int = 0
    created_at: float = field(default_factory=time.time)


class SecureRunner:
    def __init__(self):
        self.client = None
        self.client_error = None
        self.host_workspace = os.path.abspath(os.getenv("HOST_WORKSPACE_PATH", "./workspace"))
        self.host_skills = os.path.abspath(os.getenv("HOST_SKILLS_PATH", "./skills"))
        self.runtimes: Dict[Tuple[str, str], SessionRuntime] = {}
        atexit.register(self.shutdown_all)

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
        return os.path.join(self.host_workspace, "sessions", safe_session_id)

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
            if container.status != "running":
                container.start()
            self.runtimes[online_runtime_key] = existing_runtime
            return container, existing_runtime, False

        if network and offline_runtime_key in self.runtimes:
            self._shutdown_runtime(safe_session_id, False)

        runtime = self.runtimes.get(requested_runtime_key)

        container_name = self._container_name(safe_session_id, network)
        created = False

        try:
            container = client.containers.get(container_name)
            container.reload()
            if container.status != "running":
                container.start()
        except docker.errors.NotFound:
            volumes = {
                self.get_session_root(safe_session_id): {"bind": "/session", "mode": "rw"},
                self.get_session_files_dir(safe_session_id): {"bind": "/app", "mode": "rw"},
            }
            if os.path.isdir(self.host_skills):
                volumes[self.host_skills] = {"bind": "/skills", "mode": "ro"}

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
            created = True

        if runtime is None:
            runtime = SessionRuntime(
                session_id=safe_session_id,
                mode="online" if network else "offline",
                container_name=container_name,
            )
            self.runtimes[requested_runtime_key] = runtime

        return container, runtime, created

    def _build_exec_command(self, session_id: str, command: str, timeout: int):
        safe_session_id = self._resolve_session_id(session_id)
        inner_command = "\n".join(
            [
                "set -e",
                "mkdir -p /session/files /session/artifacts /session/.cache/pip /session/.cache/matplotlib",
                'if [ ! -x /session/.venv/bin/python ]; then python -m venv /session/.venv; fi',
                ". /session/.venv/bin/activate",
                "export PYTHONUNBUFFERED=1",
                "export PIP_DISABLE_PIP_VERSION_CHECK=1",
                "export PIP_CACHE_DIR=/session/.cache/pip",
                "export MPLCONFIGDIR=/session/.cache/matplotlib",
                f"export HITL_SESSION_ID={shlex.quote(safe_session_id)}",
                "export HITL_SESSION_ROOT=/session",
                "export HITL_WORKSPACE=/app",
                "export HITL_ARTIFACTS=/session/artifacts",
                "cd /app",
                command,
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

        lowered_command = command.lower()
        if any(x in lowered_command for x in ["pip ", "curl ", "wget ", "apt ", "python -m pip"]):
            network = True
            timeout = max(timeout, 600)

        safe_session_id = self._resolve_session_id(session_id)

        try:
            container, runtime, created = self._ensure_runtime(safe_session_id, network)
        except RuntimeError as e:
            return f"Sandbox Error: {e}"
        except Exception as e:
            return f"Sandbox Error: {e}"

        runtime.exec_count += 1
        await self._broadcast_runtime_status(safe_session_id, runtime, created)

        try:
            exec_result = container.exec_run(
                cmd=self._build_exec_command(safe_session_id, command, timeout),
                workdir="/app",
                demux=False,
            )
            output = exec_result.output or b""
            if isinstance(output, bytes):
                logs = output.decode("utf-8", errors="replace")
            else:
                logs = str(output)

            exit_code = exec_result.exit_code
            if exit_code != 0:
                return f"Exit code {exit_code}: {logs}"
            return logs
        except Exception as e:
            return f"Sandbox Error: {str(e)}"

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
