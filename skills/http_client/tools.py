import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from core.runner import runner
from core.tools import tool


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_CHARS = 12000
ALLOWED_SCHEMES = {"http", "https"}
TEXTUAL_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-www-form-urlencoded",
}


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


def _validate_url(url: str) -> str:
    clean_url = (url or "").strip()
    if not clean_url:
        raise ValueError("URL is required.")

    parsed = urlparse(clean_url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are supported.")

    return clean_url


def _truncate_text(text: str, max_chars: int) -> str:
    clean_text = (text or "").strip()
    if len(clean_text) <= max_chars:
        return clean_text
    return clean_text[: max(max_chars - 3, 0)].rstrip() + "..."


def _safe_max_chars(max_chars: int) -> int:
    return max(200, min(int(max_chars or DEFAULT_MAX_CHARS), 40000))


def _parse_json_input(raw_json: str, label: str, *, expect_object: bool = True) -> Any:
    clean = (raw_json or "").strip()
    if not clean:
        return {} if expect_object else None

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc

    if expect_object and not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object.")

    return parsed


def _is_textual_content_type(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return (
        lowered.startswith("text/")
        or any(token in lowered for token in TEXTUAL_CONTENT_TYPES)
        or "json" in lowered
        or "xml" in lowered
    )


async def _send_request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json_body: Any | None = None,
) -> dict:
    request_headers = {
        "User-Agent": "0-HITL/0.1 (+local assistant; structured http client)",
        "Accept": "application/json,text/plain,text/html,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    if headers:
        request_headers.update(headers)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers=request_headers,
    ) as client:
        response = await client.request(method.upper(), url, params=params, json=json_body)
        response.raise_for_status()
        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "headers": dict(response.headers),
            "text": response.text,
            "content": response.content,
        }


def _format_preview(response: dict, max_chars: int) -> str:
    content_type = response.get("content_type", "")
    text = response.get("text", "") or ""

    if "json" in content_type.lower():
        try:
            return _truncate_text(json.dumps(json.loads(text), indent=2, ensure_ascii=False), max_chars)
        except json.JSONDecodeError:
            return _truncate_text(text, max_chars)

    if _is_textual_content_type(content_type):
        return _truncate_text(text, max_chars)

    return f"[binary content omitted: {len(response.get('content', b'')):,} bytes]"


def _format_basic_response(response: dict, *, body_label: str, body_value: str) -> str:
    lines = [
        f"URL: {response.get('url')}",
        f"Status: {response.get('status_code')}",
        f"Content-Type: {response.get('content_type') or 'unknown'}",
        "",
        body_label,
        body_value or "[empty response body]",
    ]
    return "\n".join(lines).strip()


@tool
async def http_get(url: str, params_json: str = "", headers_json: str = "", max_chars: int = DEFAULT_MAX_CHARS):
    """Fetches an HTTP(S) resource and returns a structured preview of the response."""
    try:
        clean_url = _validate_url(url)
        params = _parse_json_input(params_json, "params_json")
        headers = _parse_json_input(headers_json, "headers_json")
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        response = await _send_request("GET", clean_url, params=params, headers=headers)
    except Exception as exc:
        return f"Error performing HTTP GET: {exc}"

    preview = _format_preview(response, _safe_max_chars(max_chars))
    return _format_basic_response(response, body_label="Preview:", body_value=preview)


@tool
async def http_post_json(url: str, json_body: str, headers_json: str = "", max_chars: int = DEFAULT_MAX_CHARS):
    """Sends a JSON body to an HTTP(S) endpoint and returns a structured preview of the response."""
    try:
        clean_url = _validate_url(url)
        headers = _parse_json_input(headers_json, "headers_json")
        parsed_body = _parse_json_input(json_body, "json_body", expect_object=False)
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        response = await _send_request("POST", clean_url, headers=headers, json_body=parsed_body)
    except Exception as exc:
        return f"Error performing HTTP POST: {exc}"

    preview = _format_preview(response, _safe_max_chars(max_chars))
    return _format_basic_response(response, body_label="Preview:", body_value=preview)


@tool
async def head_url(url: str, params_json: str = "", headers_json: str = ""):
    """Fetches HTTP response headers only and returns key metadata about a URL."""
    try:
        clean_url = _validate_url(url)
        params = _parse_json_input(params_json, "params_json")
        headers = _parse_json_input(headers_json, "headers_json")
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        response = await _send_request("HEAD", clean_url, params=params, headers=headers)
    except Exception as exc:
        return f"Error performing HTTP HEAD: {exc}"

    response_headers = response.get("headers", {})
    interesting_headers = [
        "content-type",
        "content-length",
        "etag",
        "last-modified",
        "cache-control",
    ]

    lines = [
        f"URL: {response.get('url')}",
        f"Status: {response.get('status_code')}",
    ]
    for header_name in interesting_headers:
        if header_name in response_headers:
            lines.append(f"{header_name}: {response_headers[header_name]}")

    return "\n".join(lines).strip()


@tool
async def download_file(url: str, destination: str, headers_json: str = "", overwrite: bool = False):
    """Downloads an HTTP(S) resource into the current workspace."""
    try:
        clean_url = _validate_url(url)
        headers = _parse_json_input(headers_json, "headers_json")
        destination_path = _resolve_workspace_path(destination)
    except ValueError as exc:
        return f"Error: {exc}"

    if os.path.exists(destination_path) and not overwrite:
        return f"Error: Destination '{destination}' already exists."

    try:
        response = await _send_request("GET", clean_url, headers=headers)
    except Exception as exc:
        return f"Error downloading file: {exc}"

    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    with open(destination_path, "wb") as handle:
        handle.write(response.get("content", b""))

    lines = [
        f"Downloaded: {_relative_path(destination_path)}",
        f"Source URL: {response.get('url')}",
        f"Status: {response.get('status_code')}",
        f"Content-Type: {response.get('content_type') or 'unknown'}",
        f"Bytes written: {len(response.get('content', b'')):,}",
    ]
    return "\n".join(lines).strip()
