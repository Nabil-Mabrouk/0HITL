import csv
import json
import os
import shlex
import uuid
from statistics import mean

from core.runner import SandboxRunResult, runner
from core.tools import tool


MAX_CSV_FILE_SIZE = 5_000_000
MAX_CSV_ROWS_ANALYZED = 1000


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


def _parse_args_json(args_json: str):
    clean = (args_json or "").strip()
    if not clean:
        return []

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"args_json must be valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("args_json must be a JSON array.")

    return [str(item) for item in parsed]


def _build_python_command(script_relative_path: str, args: list[str]) -> str:
    quoted_parts = [shlex.quote(script_relative_path)] + [shlex.quote(arg) for arg in args]
    return "python -u " + " ".join(quoted_parts)


def _wrap_run_result(result: SandboxRunResult, script_relative_path: str, network: bool) -> SandboxRunResult:
    output = str(result).strip() or "[no output]"
    lines = [
        f"Executed: {script_relative_path}",
        f"Network: {'enabled' if network else 'disabled'}",
        "",
        output,
    ]
    return SandboxRunResult(
        output="\n".join(lines).strip(),
        exit_code=result.exit_code,
        telemetry=result.telemetry,
    )


def _coerce_numeric(value: str):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return None


@tool
async def run_python(code: str, args_json: str = "", network: bool = False, filename: str = ""):
    """Writes and executes a Python snippet in the sandboxed runtime, then returns stdout/stderr."""
    clean_code = (code or "").strip()
    if not clean_code:
        return "Error: Python code is required."

    try:
        args = _parse_args_json(args_json)
    except ValueError as exc:
        return f"Error: {exc}"

    relative_filename = (filename or "").strip().replace("\\", "/")
    if relative_filename:
        try:
            script_path = _resolve_workspace_path(relative_filename)
        except ValueError as exc:
            return f"Error: {exc}"
    else:
        script_path = _resolve_workspace_path(os.path.join(".python_runtime", f"snippet_{uuid.uuid4().hex[:8]}.py"))

    os.makedirs(os.path.dirname(script_path), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(clean_code)

    script_relative_path = _relative_path(script_path)
    command = _build_python_command(script_relative_path, args)
    result = await runner.run_in_sandbox(command, network=network)
    return _wrap_run_result(result, script_relative_path, network)


@tool
async def run_python_file(path: str, args_json: str = "", network: bool = False):
    """Executes an existing Python file from the workspace in the sandboxed runtime."""
    try:
        script_path = _resolve_workspace_path(path)
        args = _parse_args_json(args_json)
    except ValueError as exc:
        return f"Error: {exc}"

    if not os.path.exists(script_path) or not os.path.isfile(script_path):
        return f"Error: File '{path}' not found in workspace."

    script_relative_path = _relative_path(script_path)
    command = _build_python_command(script_relative_path, args)
    result = await runner.run_in_sandbox(command, network=network)
    return _wrap_run_result(result, script_relative_path, network)


@tool
async def inspect_csv(path: str, max_rows: int = 5):
    """Inspects a CSV file from the workspace and returns columns, row count, samples and simple numeric stats."""
    try:
        csv_path = _resolve_workspace_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not os.path.exists(csv_path) or not os.path.isfile(csv_path):
        return f"Error: File '{path}' not found in workspace."

    if os.path.getsize(csv_path) > MAX_CSV_FILE_SIZE:
        return f"Error: CSV file '{path}' is too large for quick inspection."

    safe_max_rows = max(1, min(int(max_rows or 5), 20))

    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample or "a,b\n1,2\n")
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(handle, dialect=dialect)
            fieldnames = reader.fieldnames or []
            if not fieldnames:
                return f"Error: CSV file '{path}' has no header row."

            rows = []
            numeric_values = {field: [] for field in fieldnames}
            non_empty_counts = {field: 0 for field in fieldnames}
            unique_values = {field: set() for field in fieldnames}

            total_rows = 0
            for row in reader:
                total_rows += 1
                if len(rows) < safe_max_rows:
                    rows.append({field: (row.get(field) or "") for field in fieldnames})

                if total_rows <= MAX_CSV_ROWS_ANALYZED:
                    for field in fieldnames:
                        value = (row.get(field) or "").strip()
                        if value:
                            non_empty_counts[field] += 1
                            if len(unique_values[field]) < 20:
                                unique_values[field].add(value)
                        numeric = _coerce_numeric(value)
                        if numeric is not None:
                            numeric_values[field].append(float(numeric))
    except Exception as exc:
        return f"Error reading CSV '{path}': {exc}"

    lines = [
        f"File: {_relative_path(csv_path)}",
        f"Rows: {total_rows:,}",
        f"Columns: {len(fieldnames)}",
        f"Delimiter: {getattr(dialect, 'delimiter', ',')}",
        "",
        "Schema:",
    ]

    analyzed_rows = min(total_rows, MAX_CSV_ROWS_ANALYZED)
    for field in fieldnames:
        numeric_series = numeric_values[field]
        if numeric_series and len(numeric_series) >= max(1, analyzed_rows // 2):
            column_type = "numeric"
            stats = (
                f"min={min(numeric_series):.2f}, "
                f"max={max(numeric_series):.2f}, "
                f"avg={mean(numeric_series):.2f}"
            )
        else:
            column_type = "text"
            stats = f"unique(sample)={len(unique_values[field])}"

        lines.append(
            f"- {field}: {column_type}, non-empty={non_empty_counts[field]}/{analyzed_rows}, {stats}"
        )

    lines.append("")
    lines.append("Sample rows:")
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {json.dumps(row, ensure_ascii=False)}")

    if total_rows > MAX_CSV_ROWS_ANALYZED:
        lines.append("")
        lines.append(f"Note: stats were computed on the first {MAX_CSV_ROWS_ANALYZED} rows.")

    return "\n".join(lines).strip()
