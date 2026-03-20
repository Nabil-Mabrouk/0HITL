import mimetypes
import os

from core.runner import runner
from core.runtime_context import get_current_session_id
from core.tools import tool


def _get_workspace_root() -> str:
    return os.path.abspath(runner.get_session_files_dir())


def _build_workspace_file_url(filepath: str) -> str:
    session_id = get_current_session_id("default")
    relative_path = os.path.relpath(filepath, _get_workspace_root()).replace("\\", "/")
    return runner.build_session_file_url(session_id, f"files/{relative_path}")


def _resolve_artifact_path(path: str) -> str:
    normalized = (path or "").strip()
    if not normalized:
        raise ValueError("Artifact path is required.")

    candidates = []
    if normalized.startswith("artifacts/") or normalized.startswith("artifacts\\"):
        candidates.append(_resolve_workspace_path(normalized))
    else:
        candidates.append(_resolve_workspace_path(normalized))
        candidates.append(_resolve_workspace_path(os.path.join("artifacts", normalized)))

    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return candidate

    return candidates[-1]


def _resolve_workspace_path(path: str = ".") -> str:
    workspace_root = _get_workspace_root()
    target = os.path.abspath(os.path.join(workspace_root, path or "."))

    try:
        inside_workspace = os.path.commonpath([workspace_root, target]) == workspace_root
    except ValueError:
        inside_workspace = False

    if not inside_workspace:
        raise ValueError("Path must stay inside the workspace.")

    return target


@tool
async def write_file(filename: str, content: str):
    """Writes content to a file inside the workspace."""
    try:
        filepath = _resolve_workspace_path(filename)
    except ValueError as e:
        return f"Error: {e}"

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(str(content))

    relative_path = os.path.relpath(filepath, _get_workspace_root())
    return f"File '{relative_path}' written successfully in workspace."


@tool
async def read_file(filename: str):
    """Reads content from a file in the workspace. Handles binary files gracefully."""
    try:
        filepath = _resolve_workspace_path(filename)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return f"Error: File '{filename}' not found in workspace."

    mime, _ = mimetypes.guess_type(filepath)
    is_binary = mime and not mime.startswith(("text/", "application/json", "application/xml"))

    if is_binary or mime in ["image/png", "image/jpeg", "application/pdf"]:
        size = os.path.getsize(filepath)
        return (
            f"[Binary file: {filename} | Type: {mime} | Size: {size:,} bytes | "
            f"URL: {_build_workspace_file_url(filepath)}]"
        )

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            if len(content) > 10000:
                return content[:10000] + f"\n\n... [truncated, total: {len(content):,} chars]"
            return content
    except UnicodeDecodeError:
        return f"Error: '{filename}' is binary. Cannot display as text."


@tool
async def execute_bash(command: str, network: bool = False):
    """Executes a bash command in the isolated Docker sandbox."""
    return await runner.run_in_sandbox(command, network=network)


@tool
async def get_artifact_url(path: str):
    """Returns a direct session URL for an artifact file located in the workspace or artifacts directory."""
    try:
        filepath = _resolve_artifact_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return f"Error: Artifact '{path}' not found in workspace."

    return _build_workspace_file_url(filepath)


@tool
async def ls(path: str = "."):
    """Lists files in the current workspace."""
    try:
        directory = _resolve_workspace_path(path)
        if not os.path.isdir(directory):
            return f"Error: Directory '{path}' not found in workspace."
        files = os.listdir(directory)
        return "\n".join(files) if files else "Directory is empty."
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing directory: {str(e)}"
