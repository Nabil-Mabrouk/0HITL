import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch


def _load_http_client_tools_module():
    module_name = "test_http_client_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "http_client", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_http_client_skill_tests():
    print("Testing http_client skill...")
    http_tools = _load_http_client_tools_module()

    async def fake_send_request(method, url, *, params=None, headers=None, json_body=None):
        if method == "GET" and url == "https://api.example.com/items":
            assert params == {"page": 1}
            assert headers == {"Authorization": "Bearer test"}
            return {
                "url": "https://api.example.com/items?page=1",
                "status_code": 200,
                "content_type": "application/json; charset=utf-8",
                "headers": {"content-type": "application/json; charset=utf-8"},
                "text": '{"items":[{"id":1,"name":"alpha"}],"count":1}',
                "content": b'{"items":[{"id":1,"name":"alpha"}],"count":1}',
            }

        if method == "POST" and url == "https://api.example.com/tasks":
            assert json_body == {"title": "ship skill", "priority": "high"}
            return {
                "url": "https://api.example.com/tasks",
                "status_code": 201,
                "content_type": "application/json",
                "headers": {"content-type": "application/json"},
                "text": '{"ok":true,"task_id":"tsk_123"}',
                "content": b'{"ok":true,"task_id":"tsk_123"}',
            }

        if method == "HEAD" and url == "https://downloads.example.com/archive.zip":
            return {
                "url": url,
                "status_code": 200,
                "content_type": "application/zip",
                "headers": {
                    "content-type": "application/zip",
                    "content-length": "4096",
                    "etag": '"archive-v1"',
                },
                "text": "",
                "content": b"",
            }

        if method == "GET" and url == "https://downloads.example.com/archive.zip":
            return {
                "url": url,
                "status_code": 200,
                "content_type": "application/zip",
                "headers": {"content-type": "application/zip"},
                "text": "",
                "content": b"PK\x03\x04archive-bytes",
            }

        raise AssertionError(f"Unexpected request: {method} {url}")

    with tempfile.TemporaryDirectory() as tempdir:
        with patch.object(http_tools, "_send_request", side_effect=fake_send_request):
            get_result = await http_tools.http_get(
                "https://api.example.com/items",
                params_json='{"page": 1}',
                headers_json='{"Authorization": "Bearer test"}',
            )
            assert "Status: 200" in get_result
            assert '"count": 1' in get_result
            assert "https://api.example.com/items?page=1" in get_result
            print("PASS http_get returns a structured JSON preview.")

            post_result = await http_tools.http_post_json(
                "https://api.example.com/tasks",
                json_body='{"title": "ship skill", "priority": "high"}',
            )
            assert "Status: 201" in post_result
            assert '"task_id": "tsk_123"' in post_result
            print("PASS http_post_json sends JSON and formats the response.")

            head_result = await http_tools.head_url("https://downloads.example.com/archive.zip")
            assert "content-length: 4096" in head_result
            assert 'etag: "archive-v1"' in head_result
            print("PASS head_url returns response metadata.")

        with patch.object(http_tools, "_send_request", side_effect=fake_send_request), patch.object(
            http_tools, "_get_workspace_root", return_value=tempdir
        ):
            download_result = await http_tools.download_file(
                "https://downloads.example.com/archive.zip",
                "artifacts/archive.zip",
            )
            assert "Downloaded: artifacts/archive.zip" in download_result
            target_path = os.path.join(tempdir, "artifacts", "archive.zip")
            assert os.path.isfile(target_path)
            with open(target_path, "rb") as handle:
                assert handle.read().startswith(b"PK\x03\x04")
            print("PASS download_file writes binary content into the workspace.")

            invalid_json = await http_tools.http_post_json(
                "https://api.example.com/tasks",
                json_body="{invalid json}",
            )
            assert "valid JSON" in invalid_json
            print("PASS http_post_json rejects invalid JSON payloads.")


if __name__ == "__main__":
    asyncio.run(run_http_client_skill_tests())
