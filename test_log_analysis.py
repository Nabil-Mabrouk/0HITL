import json
import os
import tempfile

from analyze_session_logs import write_report_output
from core.log_analysis import analyze_workspace_logs, render_report


def _write_jsonl(path: str, records: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def run_log_analysis_tests():
    print("Testing session log analysis...")

    with tempfile.TemporaryDirectory() as temp_dir:
        session_a_log = os.path.join(temp_dir, "sessions", "session-a", "logs", "session.jsonl")
        session_b_log = os.path.join(temp_dir, "sessions", "session-b", "logs", "session.jsonl")

        _write_jsonl(
            session_a_log,
            [
                {"record_type": "event", "event_type": "mission_started", "mission_id": "m1"},
                {
                    "record_type": "event",
                    "event_type": "llm_call_completed",
                    "duration_ms": 450.0,
                    "status": "completed",
                },
                {
                    "record_type": "event",
                    "event_type": "tool_call_completed",
                    "tool_name": "execute_bash",
                    "duration_ms": 2400.0,
                    "status": "tool_success",
                    "cold_start": True,
                    "container_start_ms": 640.0,
                    "docker_exec_ms": 1700.0,
                    "command_wall_ms": 1200.0,
                    "venv_bootstrap_ms": 380.0,
                },
                {
                    "record_type": "event",
                    "event_type": "tool_call_completed",
                    "tool_name": "execute_bash",
                    "duration_ms": 1800.0,
                    "status": "tool_error",
                    "result_preview": "Exit code 1",
                    "runtime_reused": True,
                    "container_start_ms": 0.0,
                    "docker_exec_ms": 1600.0,
                    "command_wall_ms": 1100.0,
                    "venv_bootstrap_ms": 15.0,
                },
                {
                    "record_type": "event",
                    "event_type": "sandbox_command_completed",
                    "tool_name": "execute_bash",
                    "cold_start": True,
                    "runtime_reused": False,
                    "container_start_ms": 640.0,
                    "docker_exec_ms": 1700.0,
                    "command_wall_ms": 1200.0,
                    "venv_bootstrap_ms": 380.0,
                },
                {
                    "record_type": "event",
                    "event_type": "sandbox_command_completed",
                    "tool_name": "execute_bash",
                    "cold_start": False,
                    "runtime_reused": True,
                    "container_start_ms": 0.0,
                    "docker_exec_ms": 1600.0,
                    "command_wall_ms": 1100.0,
                    "venv_bootstrap_ms": 15.0,
                },
                {
                    "record_type": "event",
                    "event_type": "subtask_completed",
                    "duration_ms": 3200.0,
                    "status": "retry_after_tool_error",
                    "error_count": 1,
                },
                {
                    "record_type": "event",
                    "event_type": "mission_completed",
                    "duration_ms": 4200.0,
                    "status": "fatal_tool_failure",
                    "attempts": 3,
                },
            ],
        )

        _write_jsonl(
            session_b_log,
            [
                {"record_type": "event", "event_type": "mission_started", "mission_id": "m2"},
                {
                    "record_type": "event",
                    "event_type": "llm_call_completed",
                    "duration_ms": 180.0,
                    "status": "completed",
                },
                {
                    "record_type": "event",
                    "event_type": "tool_call_completed",
                    "tool_name": "get_weather",
                    "duration_ms": 40.0,
                    "status": "tool_success",
                },
                {
                    "record_type": "event",
                    "event_type": "subtask_completed",
                    "duration_ms": 260.0,
                    "status": "completed_without_tools",
                    "error_count": 0,
                },
                {
                    "record_type": "event",
                    "event_type": "mission_completed",
                    "duration_ms": 700.0,
                    "status": "success",
                    "attempts": 1,
                },
            ],
        )

        report = analyze_workspace_logs(workspace_root=temp_dir, top_n=3)

        assert report["sessions_scanned"] == 2
        assert report["missions"]["count"] == 2
        assert report["missions"]["success_count"] == 1
        assert report["missions"]["non_success_count"] == 1
        assert report["slowest_sessions"][0]["session_id"] == "session-a"
        assert report["slowest_tools"][0]["tool_name"] == "execute_bash"
        assert report["most_unstable_tools"][0]["tool_name"] == "execute_bash"
        assert report["most_unstable_tools"][0]["error_count"] == 1
        assert report["runner"]["cold_start_count"] == 1
        assert report["runner"]["slowest_container_start_ms"] == 640.0
        assert report["sessions"][0]["runner"]["avg_command_wall_ms"] is not None
        assert report["decision_summary"]["dominant_cause"] == "command_execution"
        assert report["sessions"][0]["decision"]["dominant_cause"] == "command_execution"
        assert any("execute_bash" in item["message"] for item in report["bottlenecks"])
        assert any("cold start" in item["message"] for item in report["sessions"][0]["bottlenecks"])
        assert any("attempt" in item["message"] for item in report["sessions"][0]["bottlenecks"])

        rendered = render_report(report, top_n=3)
        assert "0-HITL Session Log Analysis" in rendered
        assert "Decision Summary" in rendered
        assert "Dominant bottleneck" in rendered
        assert "Top 3 Slowest Tools" in rendered
        assert "execute_bash" in rendered
        assert "session-a" in rendered
        assert "cold starts" in rendered

        output_dir = os.path.join(temp_dir, "system", "reports")
        written_path = write_report_output(output_dir, report)
        assert os.path.isfile(written_path)
        with open(written_path, "r", encoding="utf-8") as handle:
            saved_report = json.load(handle)
        assert saved_report["decision_summary"]["dominant_cause"] == "command_execution"
        print("PASS Log analysis surfaces slow sessions, unstable tools and bottleneck notes.")


if __name__ == "__main__":
    run_log_analysis_tests()
