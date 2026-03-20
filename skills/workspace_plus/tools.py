import fnmatch
import os
import shutil

from core.runner import runner
from core.tools import tool


MAX_FILE_MATCHES = 500
MAX_GREP_MATCHES = 200
MAX_TREE_ENTRIES = 500
MAX_TEXT_FILE_SIZE = 512_000


def _get_workspace_root() -> str:
    return os.path.abspath(runner.get_session_files_dir())


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


def _relative_path(target: str) -> str:
    return os.path.relpath(target, _get_workspace_root()).replace("\\", "/")


def _ensure_not_workspace_root(target: str):
    if os.path.normcase(os.path.abspath(target)) == os.path.normcase(_get_workspace_root()):
        raise ValueError("Operation on the workspace root is not allowed.")


def _is_probably_binary(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return True

    if b"\x00" in sample:
        return True

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True

    return False


def _safe_limit(limit: int, default_limit: int, hard_cap: int) -> int:
    if limit <= 0:
        return default_limit
    return min(limit, hard_cap)


@tool
async def find_files(pattern: str = "*", path: str = ".", limit: int = 200):
    """Recursively finds files in the workspace whose relative path or basename matches a glob pattern."""
    try:
        root = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(root):
        return f"Error: Directory '{path}' not found in workspace."

    effective_limit = _safe_limit(limit, 200, MAX_FILE_MATCHES)
    matches = []

    for current_root, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            absolute_path = os.path.join(current_root, filename)
            relative_path = _relative_path(absolute_path)
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(relative_path, pattern):
                matches.append(relative_path)
                if len(matches) >= effective_limit:
                    break
        if len(matches) >= effective_limit:
            break

    if not matches:
        return f"No files matched pattern '{pattern}' under '{path}'."

    suffix = ""
    if len(matches) >= effective_limit:
        suffix = f"\n\n... [truncated at {effective_limit} match(es)]"

    return "\n".join(matches) + suffix


@tool
async def grep_files(query: str, path: str = ".", case_sensitive: bool = False, file_pattern: str = "*", limit: int = 50):
    """Searches text files in the workspace and returns matching lines with file and line number."""
    if not query:
        return "Error: Query must not be empty."

    try:
        root = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(root):
        return f"Error: Directory '{path}' not found in workspace."

    effective_limit = _safe_limit(limit, 50, MAX_GREP_MATCHES)
    needle = query if case_sensitive else query.lower()
    matches = []

    for current_root, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            if not fnmatch.fnmatch(filename, file_pattern):
                continue

            absolute_path = os.path.join(current_root, filename)
            if os.path.getsize(absolute_path) > MAX_TEXT_FILE_SIZE or _is_probably_binary(absolute_path):
                continue

            try:
                with open(absolute_path, "r", encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        haystack = line if case_sensitive else line.lower()
                        if needle in haystack:
                            excerpt = line.strip()
                            if len(excerpt) > 200:
                                excerpt = excerpt[:200] + "..."
                            matches.append(f"{_relative_path(absolute_path)}:{line_number}: {excerpt}")
                            if len(matches) >= effective_limit:
                                break
            except OSError:
                continue

            if len(matches) >= effective_limit:
                break
        if len(matches) >= effective_limit:
            break

    if not matches:
        return f"No matches for '{query}' under '{path}'."

    suffix = ""
    if len(matches) >= effective_limit:
        suffix = f"\n\n... [truncated at {effective_limit} match(es)]"

    return "\n".join(matches) + suffix


@tool
async def tree_workspace(path: str = ".", max_depth: int = 2, limit: int = 200):
    """Returns a compact tree view of the workspace rooted at the given path."""
    try:
        root = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(root):
        return f"Error: Path '{path}' not found in workspace."

    effective_depth = max(0, min(max_depth, 6))
    effective_limit = _safe_limit(limit, 200, MAX_TREE_ENTRIES)
    lines = []
    root_label = _relative_path(root)
    lines.append(root_label if root_label != "." else ".")

    def visit(current_path: str, depth: int):
        if len(lines) >= effective_limit:
            return
        if depth > effective_depth or not os.path.isdir(current_path):
            return

        entries = sorted(os.listdir(current_path))
        for entry in entries:
            absolute_entry = os.path.join(current_path, entry)
            prefix = "  " * depth + "- "
            label = entry + ("/" if os.path.isdir(absolute_entry) else "")
            lines.append(prefix + label)
            if len(lines) >= effective_limit:
                return
            if os.path.isdir(absolute_entry):
                visit(absolute_entry, depth + 1)

    visit(root, 1)

    suffix = ""
    if len(lines) >= effective_limit:
        suffix = f"\n\n... [truncated at {effective_limit} entrie(s)]"

    return "\n".join(lines) + suffix


@tool
async def make_directory(path: str):
    """Creates a directory inside the workspace."""
    try:
        target = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    os.makedirs(target, exist_ok=True)
    return f"Directory '{_relative_path(target)}' is ready."


def _remove_existing_destination(destination: str):
    if os.path.isdir(destination) and not os.path.islink(destination):
        shutil.rmtree(destination)
    else:
        os.remove(destination)


@tool
async def copy_path(source: str, destination: str, overwrite: bool = False):
    """Copies a file or directory inside the workspace."""
    try:
        source_path = _resolve_workspace_path(source)
        destination_path = _resolve_workspace_path(destination)
        _ensure_not_workspace_root(source_path)
        _ensure_not_workspace_root(destination_path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(source_path):
        return f"Error: Source '{source}' not found in workspace."

    if os.path.exists(destination_path):
        if not overwrite:
            return f"Error: Destination '{destination}' already exists."
        _remove_existing_destination(destination_path)

    os.makedirs(os.path.dirname(destination_path), exist_ok=True)

    if os.path.isdir(source_path):
        shutil.copytree(source_path, destination_path)
    else:
        shutil.copy2(source_path, destination_path)

    return f"Copied '{_relative_path(source_path)}' to '{_relative_path(destination_path)}'."


@tool
async def move_path(source: str, destination: str, overwrite: bool = False):
    """Moves or renames a file or directory inside the workspace."""
    try:
        source_path = _resolve_workspace_path(source)
        destination_path = _resolve_workspace_path(destination)
        _ensure_not_workspace_root(source_path)
        _ensure_not_workspace_root(destination_path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(source_path):
        return f"Error: Source '{source}' not found in workspace."

    if os.path.exists(destination_path):
        if not overwrite:
            return f"Error: Destination '{destination}' already exists."
        _remove_existing_destination(destination_path)

    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    shutil.move(source_path, destination_path)
    return f"Moved '{source}' to '{destination}'."


@tool
async def delete_path(path: str, recursive: bool = False):
    """Deletes a file or directory inside the workspace."""
    try:
        target = _resolve_workspace_path(path)
        _ensure_not_workspace_root(target)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(target):
        return f"Error: Path '{path}' not found in workspace."

    if os.path.isdir(target) and not os.path.islink(target):
        if recursive:
            shutil.rmtree(target)
        else:
            try:
                os.rmdir(target)
            except OSError:
                return "Error: Directory is not empty. Use recursive=true to delete it."
    else:
        os.remove(target)

    return f"Deleted '{path}'."
