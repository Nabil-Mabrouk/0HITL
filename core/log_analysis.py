import json
import os
from collections import Counter, defaultdict
from typing import Any


DECISION_PLAYBOOK = {
    "llm_latency": {
        "label": "LLM Latency",
        "recommendation": "Reduce repeated model turns, compact earlier, or route routine work to a faster model.",
    },
    "docker_cold_start": {
        "label": "Docker Cold Start",
        "recommendation": "Reuse runtimes longer or prewarm the sandbox before tool-heavy missions.",
    },
    "venv_bootstrap": {
        "label": "Venv Bootstrap",
        "recommendation": "Keep the session venv warm longer or reduce per-run bootstrap work.",
    },
    "command_execution": {
        "label": "Command Execution",
        "recommendation": "Optimize the shell command itself or replace it with a native tool when possible.",
    },
    "retry_pressure": {
        "label": "Retry Pressure",
        "recommendation": "Reduce retries by improving prompts, tool contracts, or error recovery logic.",
    },
    "tool_instability": {
        "label": "Tool Instability",
        "recommendation": "Harden the failing tool path first because instability is amplifying latency.",
    },
}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _session_decision_candidates(summary: dict) -> list[dict]:
    mission = summary["missions"]
    llm = summary["llm"]
    runner = summary["runner"]
    tools = summary["tools"]
    candidates = []

    def add(kind: str, score: float | None, evidence: str):
        if score is None or score <= 0:
            return
        playbook = DECISION_PLAYBOOK[kind]
        candidates.append(
            {
                "kind": kind,
                "label": playbook["label"],
                "score": round(score, 2),
                "evidence": evidence,
                "recommendation": playbook["recommendation"],
            }
        )

    avg_llm = llm.get("avg_duration_ms") or 0.0
    avg_container_start = runner.get("avg_container_start_ms") or 0.0
    avg_venv_bootstrap = runner.get("avg_venv_bootstrap_ms") or 0.0
    avg_command_wall = runner.get("avg_command_wall_ms") or 0.0
    max_attempts = mission.get("max_attempts") or 0.0
    runner_count = runner.get("count") or 0
    cold_starts = runner.get("cold_start_count") or 0

    add(
        "llm_latency",
        avg_llm / 12.0 if avg_llm >= 120 else 0.0,
        f"Average LLM call duration is {avg_llm:.2f} ms.",
    )
    add(
        "docker_cold_start",
        (avg_container_start / 8.0) + (cold_starts * 10.0) if cold_starts else 0.0,
        f"{cold_starts} cold start(s) with average container start {avg_container_start:.2f} ms.",
    )
    add(
        "venv_bootstrap",
        (avg_venv_bootstrap / 8.0) if avg_venv_bootstrap >= 80 else 0.0,
        f"Average venv bootstrap cost is {avg_venv_bootstrap:.2f} ms.",
    )
    add(
        "command_execution",
        (avg_command_wall / 12.0) if avg_command_wall >= 120 else 0.0,
        f"Average command wall time is {avg_command_wall:.2f} ms across {runner_count} runner command(s).",
    )
    add(
        "retry_pressure",
        ((max_attempts - 1) * 35.0) + (15.0 if mission.get("non_success_count") else 0.0)
        if max_attempts > 1
        else 0.0,
        f"Mission retries reached {max_attempts:.2f} attempt(s).",
    )

    unstable_tools = tools.get("most_unstable_tools") or []
    if unstable_tools:
        top_tool = unstable_tools[0]
        error_rate = top_tool.get("error_rate") or 0.0
        avg_tool_duration = top_tool.get("avg_duration_ms") or 0.0
        add(
            "tool_instability",
            (error_rate * 40.0) + (avg_tool_duration / 100.0),
            (
                f"Tool '{top_tool.get('tool_name')}' failed {top_tool.get('error_count')} time(s) "
                f"with error rate {error_rate:.2f}."
            ),
        )

    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _build_session_decision(summary: dict) -> dict:
    candidates = _session_decision_candidates(summary)
    if not candidates:
        return {
            "dominant_cause": "insufficient_data",
            "label": "Insufficient Data",
            "summary": "Not enough timing data was available to infer a dominant bottleneck.",
            "recommendation": "Run more real sessions or more tool-bearing tasks before optimizing.",
            "secondary_signals": [],
        }

    primary = candidates[0]
    secondary = candidates[1:3]
    return {
        "dominant_cause": primary["kind"],
        "label": primary["label"],
        "summary": f"Dominant bottleneck appears to be {primary['label'].lower()}. {primary['evidence']}",
        "recommendation": primary["recommendation"],
        "score": primary["score"],
        "secondary_signals": secondary,
    }


def _build_global_decision(session_summaries: list[dict]) -> dict:
    if not session_summaries:
        return {
            "dominant_cause": "insufficient_data",
            "label": "Insufficient Data",
            "summary": "No session logs were available for a global performance diagnosis.",
            "recommendation": "Collect a few representative sessions before drawing optimization conclusions.",
            "secondary_signals": [],
        }

    weighted_scores = defaultdict(float)
    evidence_by_kind = {}
    for summary in session_summaries:
        decision = summary.get("decision") or {}
        kind = decision.get("dominant_cause")
        if not kind or kind == "insufficient_data":
            continue
        weight = summary["missions"]["max_duration_ms"] or summary["missions"]["avg_duration_ms"] or 1.0
        weighted_scores[kind] += weight
        evidence_by_kind.setdefault(kind, []).append(summary["session_id"])

    if not weighted_scores:
        return {
            "dominant_cause": "insufficient_data",
            "label": "Insufficient Data",
            "summary": "Session data exists but none of the current heuristics found a dominant bottleneck.",
            "recommendation": "Inspect raw session logs and extend the heuristics if needed.",
            "secondary_signals": [],
        }

    ranked = sorted(weighted_scores.items(), key=lambda item: item[1], reverse=True)
    primary_kind, primary_score = ranked[0]
    secondary = [
        {
            "kind": kind,
            "label": DECISION_PLAYBOOK[kind]["label"],
            "score": round(score, 2),
            "sessions": evidence_by_kind.get(kind, []),
        }
        for kind, score in ranked[1:3]
    ]

    return {
        "dominant_cause": primary_kind,
        "label": DECISION_PLAYBOOK[primary_kind]["label"],
        "summary": (
            f"Across scanned sessions, the leading optimization priority is {DECISION_PLAYBOOK[primary_kind]['label'].lower()} "
            f"based on the slowest observed sessions."
        ),
        "recommendation": DECISION_PLAYBOOK[primary_kind]["recommendation"],
        "score": round(primary_score, 2),
        "sessions": evidence_by_kind.get(primary_kind, []),
        "secondary_signals": secondary,
    }


def discover_session_log_files(workspace_root: str = "./workspace", session_id: str | None = None) -> list[str]:
    sessions_root = os.path.abspath(os.path.join(workspace_root, "sessions"))
    if not os.path.isdir(sessions_root):
        return []

    discovered = []
    for candidate_session_id in sorted(os.listdir(sessions_root)):
        if session_id and candidate_session_id != session_id:
            continue

        log_path = os.path.join(sessions_root, candidate_session_id, "logs", "session.jsonl")
        if os.path.isfile(log_path):
            discovered.append(log_path)
    return discovered


def load_session_records(log_path: str) -> list[dict]:
    records = []
    with open(log_path, "r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                records.append(
                    {
                        "record_type": "event",
                        "event_type": "invalid_json_line",
                        "status": "parse_error",
                        "line_number": line_number,
                        "raw_preview": line[:240],
                    }
                )
                continue

            records.append(record)
    return records


def summarize_session_records(records: list[dict], session_id: str) -> dict:
    event_records = [record for record in records if record.get("record_type") == "event"]
    mission_events = [record for record in event_records if record.get("event_type") == "mission_completed"]
    llm_events = [record for record in event_records if record.get("event_type") == "llm_call_completed"]
    tool_events = [record for record in event_records if record.get("event_type") == "tool_call_completed"]
    subtask_events = [record for record in event_records if record.get("event_type") == "subtask_completed"]
    runner_events = [record for record in event_records if record.get("event_type") == "sandbox_command_completed"]

    mission_durations = [
        duration
        for duration in (_safe_float(record.get("duration_ms")) for record in mission_events)
        if duration is not None
    ]
    llm_durations = [
        duration
        for duration in (_safe_float(record.get("duration_ms")) for record in llm_events)
        if duration is not None
    ]
    tool_durations = [
        duration
        for duration in (_safe_float(record.get("duration_ms")) for record in tool_events)
        if duration is not None
    ]
    subtask_durations = [
        duration
        for duration in (_safe_float(record.get("duration_ms")) for record in subtask_events)
        if duration is not None
    ]
    retry_counts = [
        retry_count
        for retry_count in (_safe_float(record.get("attempts")) for record in mission_events)
        if retry_count is not None
    ]
    runner_container_starts = [
        duration
        for duration in (_safe_float(record.get("container_start_ms")) for record in runner_events)
        if duration is not None
    ]
    runner_docker_execs = [
        duration
        for duration in (_safe_float(record.get("docker_exec_ms")) for record in runner_events)
        if duration is not None
    ]
    runner_command_walls = [
        duration
        for duration in (_safe_float(record.get("command_wall_ms")) for record in runner_events)
        if duration is not None
    ]
    runner_venv_bootstraps = [
        duration
        for duration in (_safe_float(record.get("venv_bootstrap_ms")) for record in runner_events)
        if duration is not None
    ]
    cold_start_count = sum(1 for record in runner_events if record.get("cold_start") is True)
    runtime_reused_count = sum(1 for record in runner_events if record.get("runtime_reused") is True)

    mission_status_counts = Counter(
        (record.get("status") or "unknown")
        for record in mission_events
    )

    tool_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tool_name": "",
            "calls": 0,
            "error_count": 0,
            "durations_ms": [],
            "last_status": None,
            "sample_error": None,
        }
    )

    for record in tool_events:
        tool_name = record.get("tool_name") or "unknown"
        tool_entry = tool_stats[tool_name]
        tool_entry["tool_name"] = tool_name
        tool_entry["calls"] += 1
        tool_entry["last_status"] = record.get("status")

        duration_ms = _safe_float(record.get("duration_ms"))
        if duration_ms is not None:
            tool_entry["durations_ms"].append(duration_ms)

        if record.get("status") != "tool_success":
            tool_entry["error_count"] += 1
            if tool_entry["sample_error"] is None:
                tool_entry["sample_error"] = record.get("result_preview")

    tool_summary = []
    for tool_name, tool_entry in tool_stats.items():
        durations = tool_entry["durations_ms"]
        calls = tool_entry["calls"]
        error_count = tool_entry["error_count"]
        tool_summary.append(
            {
                "tool_name": tool_name,
                "calls": calls,
                "error_count": error_count,
                "error_rate": _round_metric((error_count / calls) if calls else 0.0),
                "avg_duration_ms": _round_metric(_avg(durations)),
                "max_duration_ms": _round_metric(max(durations) if durations else None),
                "sample_error": tool_entry["sample_error"],
            }
        )

    slowest_tools = sorted(
        tool_summary,
        key=lambda item: (item["avg_duration_ms"] or 0.0, item["max_duration_ms"] or 0.0, item["calls"]),
        reverse=True,
    )
    most_unstable_tools = sorted(
        [item for item in tool_summary if item["error_count"] > 0],
        key=lambda item: (item["error_rate"] or 0.0, item["error_count"], item["avg_duration_ms"] or 0.0),
        reverse=True,
    )

    bottlenecks = []
    if mission_durations:
        bottlenecks.append(
            {
                "kind": "mission_latency",
                "message": (
                    f"Session '{session_id}' reached {max(mission_durations):.2f} ms on its slowest mission "
                    f"(avg {_avg(mission_durations):.2f} ms)."
                ),
            }
        )

    if slowest_tools and slowest_tools[0].get("avg_duration_ms") is not None:
        top_tool = slowest_tools[0]
        bottlenecks.append(
            {
                "kind": "slow_tool",
                "message": (
                    f"Tool '{top_tool['tool_name']}' is the slowest in session '{session_id}' "
                    f"(avg {top_tool['avg_duration_ms']:.2f} ms across {top_tool['calls']} call(s))."
                ),
            }
        )

    if most_unstable_tools:
        unstable_tool = most_unstable_tools[0]
        bottlenecks.append(
            {
                "kind": "unstable_tool",
                "message": (
                    f"Tool '{unstable_tool['tool_name']}' failed {unstable_tool['error_count']} time(s) "
                    f"in session '{session_id}' (error rate {unstable_tool['error_rate']:.2f})."
                ),
            }
        )

    if runner_container_starts and max(runner_container_starts) > 250.0:
        bottlenecks.append(
            {
                "kind": "runner_startup_latency",
                "message": (
                    f"Runner startup reached {max(runner_container_starts):.2f} ms in session '{session_id}' "
                    f"with {cold_start_count} cold start(s)."
                ),
            }
        )

    if retry_counts and max(retry_counts) > 1:
        bottlenecks.append(
            {
                "kind": "retry_pressure",
                "message": (
                    f"Session '{session_id}' needed up to {int(max(retry_counts))} attempt(s) on a mission, "
                    "which suggests retries contribute to latency."
                ),
            }
        )

    non_success_missions = sum(
        count for status, count in mission_status_counts.items() if status != "success"
    )

    summary = {
        "session_id": session_id,
        "records": len(records),
        "event_records": len(event_records),
        "missions": {
            "count": len(mission_events),
            "success_count": mission_status_counts.get("success", 0),
            "non_success_count": non_success_missions,
            "status_counts": dict(mission_status_counts),
            "avg_duration_ms": _round_metric(_avg(mission_durations)),
            "max_duration_ms": _round_metric(max(mission_durations) if mission_durations else None),
            "avg_attempts": _round_metric(_avg(retry_counts)),
            "max_attempts": _round_metric(max(retry_counts) if retry_counts else None),
        },
        "llm": {
            "count": len(llm_events),
            "avg_duration_ms": _round_metric(_avg(llm_durations)),
            "max_duration_ms": _round_metric(max(llm_durations) if llm_durations else None),
        },
        "tools": {
            "count": len(tool_events),
            "error_count": sum(item["error_count"] for item in tool_summary),
            "slowest_tools": slowest_tools,
            "most_unstable_tools": most_unstable_tools,
        },
        "subtasks": {
            "count": len(subtask_events),
            "avg_duration_ms": _round_metric(_avg(subtask_durations)),
            "max_duration_ms": _round_metric(max(subtask_durations) if subtask_durations else None),
        },
        "runner": {
            "count": len(runner_events),
            "cold_start_count": cold_start_count,
            "runtime_reused_count": runtime_reused_count,
            "avg_container_start_ms": _round_metric(_avg(runner_container_starts)),
            "max_container_start_ms": _round_metric(max(runner_container_starts) if runner_container_starts else None),
            "avg_docker_exec_ms": _round_metric(_avg(runner_docker_execs)),
            "max_docker_exec_ms": _round_metric(max(runner_docker_execs) if runner_docker_execs else None),
            "avg_command_wall_ms": _round_metric(_avg(runner_command_walls)),
            "max_command_wall_ms": _round_metric(max(runner_command_walls) if runner_command_walls else None),
            "avg_venv_bootstrap_ms": _round_metric(_avg(runner_venv_bootstraps)),
            "max_venv_bootstrap_ms": _round_metric(max(runner_venv_bootstraps) if runner_venv_bootstraps else None),
        },
        "bottlenecks": bottlenecks,
    }
    summary["decision"] = _build_session_decision(summary)
    return summary


def analyze_workspace_logs(workspace_root: str = "./workspace", session_id: str | None = None, top_n: int = 5) -> dict:
    log_files = discover_session_log_files(workspace_root=workspace_root, session_id=session_id)
    session_summaries = []
    all_slowest_tools = []
    all_unstable_tools = []
    global_status_counts = Counter()
    session_bottlenecks = []

    for log_path in log_files:
        current_session_id = os.path.basename(os.path.dirname(os.path.dirname(log_path)))
        records = load_session_records(log_path)
        session_summary = summarize_session_records(records, current_session_id)
        session_summaries.append(session_summary)
        global_status_counts.update(session_summary["missions"]["status_counts"])

        for tool in session_summary["tools"]["slowest_tools"]:
            all_slowest_tools.append({"session_id": current_session_id, **tool})
        for tool in session_summary["tools"]["most_unstable_tools"]:
            all_unstable_tools.append({"session_id": current_session_id, **tool})
        for bottleneck in session_summary["bottlenecks"]:
            session_bottlenecks.append({"session_id": current_session_id, **bottleneck})

    mission_summaries = [summary["missions"] for summary in session_summaries]
    llm_summaries = [summary["llm"] for summary in session_summaries]
    subtask_summaries = [summary["subtasks"] for summary in session_summaries]
    runner_summaries = [summary["runner"] for summary in session_summaries]

    mission_durations = [
        summary["max_duration_ms"]
        for summary in mission_summaries
        if summary["max_duration_ms"] is not None
    ]
    llm_maxima = [
        summary["max_duration_ms"]
        for summary in llm_summaries
        if summary["max_duration_ms"] is not None
    ]
    subtask_maxima = [
        summary["max_duration_ms"]
        for summary in subtask_summaries
        if summary["max_duration_ms"] is not None
    ]
    runner_start_maxima = [
        summary["max_container_start_ms"]
        for summary in runner_summaries
        if summary["max_container_start_ms"] is not None
    ]
    runner_exec_maxima = [
        summary["max_docker_exec_ms"]
        for summary in runner_summaries
        if summary["max_docker_exec_ms"] is not None
    ]

    slowest_sessions = sorted(
        session_summaries,
        key=lambda item: (
            item["missions"]["max_duration_ms"] or 0.0,
            item["tools"]["error_count"],
            item["tools"]["count"],
        ),
        reverse=True,
    )

    all_slowest_tools = sorted(
        all_slowest_tools,
        key=lambda item: (item["avg_duration_ms"] or 0.0, item["max_duration_ms"] or 0.0, item["calls"]),
        reverse=True,
    )
    all_unstable_tools = sorted(
        all_unstable_tools,
        key=lambda item: (item["error_rate"] or 0.0, item["error_count"], item["avg_duration_ms"] or 0.0),
        reverse=True,
    )

    report = {
        "workspace_root": os.path.abspath(workspace_root),
        "sessions_scanned": len(session_summaries),
        "log_files_scanned": len(log_files),
        "missions": {
            "count": sum(summary["count"] for summary in mission_summaries),
            "success_count": sum(summary["success_count"] for summary in mission_summaries),
            "non_success_count": sum(summary["non_success_count"] for summary in mission_summaries),
            "status_counts": dict(global_status_counts),
            "slowest_mission_ms": _round_metric(max(mission_durations) if mission_durations else None),
        },
        "llm": {
            "count": sum(summary["count"] for summary in llm_summaries),
            "slowest_call_ms": _round_metric(max(llm_maxima) if llm_maxima else None),
        },
        "subtasks": {
            "count": sum(summary["count"] for summary in subtask_summaries),
            "slowest_subtask_ms": _round_metric(max(subtask_maxima) if subtask_maxima else None),
        },
        "runner": {
            "count": sum(summary["count"] for summary in runner_summaries),
            "cold_start_count": sum(summary["cold_start_count"] for summary in runner_summaries),
            "runtime_reused_count": sum(summary["runtime_reused_count"] for summary in runner_summaries),
            "slowest_container_start_ms": _round_metric(max(runner_start_maxima) if runner_start_maxima else None),
            "slowest_docker_exec_ms": _round_metric(max(runner_exec_maxima) if runner_exec_maxima else None),
        },
        "slowest_sessions": [
            {
                "session_id": summary["session_id"],
                "mission_max_duration_ms": summary["missions"]["max_duration_ms"],
                "mission_avg_duration_ms": summary["missions"]["avg_duration_ms"],
                "non_success_count": summary["missions"]["non_success_count"],
                "tool_error_count": summary["tools"]["error_count"],
                "mission_max_attempts": summary["missions"]["max_attempts"],
                "cold_start_count": summary["runner"]["cold_start_count"],
            }
            for summary in slowest_sessions[:top_n]
        ],
        "slowest_tools": all_slowest_tools[:top_n],
        "most_unstable_tools": all_unstable_tools[:top_n],
        "bottlenecks": session_bottlenecks[:top_n],
        "sessions": session_summaries,
    }
    report["decision_summary"] = _build_global_decision(session_summaries)
    return report


def render_report(report: dict, top_n: int = 5) -> str:
    lines = [
        "0-HITL Session Log Analysis",
        f"Workspace: {report['workspace_root']}",
        f"Sessions scanned: {report['sessions_scanned']}",
        f"Log files scanned: {report['log_files_scanned']}",
        "",
        "Decision Summary",
        f"- Dominant bottleneck: {report['decision_summary']['label']}",
        f"- Diagnosis: {report['decision_summary']['summary']}",
        f"- Recommended action: {report['decision_summary']['recommendation']}",
        "",
        "Mission Summary",
        (
            f"- Missions: {report['missions']['count']} total | "
            f"{report['missions']['success_count']} success | "
            f"{report['missions']['non_success_count']} non-success"
        ),
        f"- Slowest mission: {report['missions']['slowest_mission_ms'] or 0:.2f} ms",
        (
            f"- LLM calls: {report['llm']['count']} total | "
            f"slowest call {report['llm']['slowest_call_ms'] or 0:.2f} ms"
        ),
        (
            f"- Subtasks: {report['subtasks']['count']} total | "
            f"slowest subtask {report['subtasks']['slowest_subtask_ms'] or 0:.2f} ms"
        ),
        (
            f"- Runner commands: {report['runner']['count']} total | "
            f"cold starts {report['runner']['cold_start_count']} | "
            f"runtime reuses {report['runner']['runtime_reused_count']} | "
            f"slowest container start {report['runner']['slowest_container_start_ms'] or 0:.2f} ms | "
            f"slowest docker exec {report['runner']['slowest_docker_exec_ms'] or 0:.2f} ms"
        ),
        "",
        f"Top {top_n} Slowest Sessions",
    ]

    if report["slowest_sessions"]:
        for session in report["slowest_sessions"]:
            lines.append(
                (
                    f"- {session['session_id']}: max mission {session['mission_max_duration_ms'] or 0:.2f} ms | "
                    f"avg {session['mission_avg_duration_ms'] or 0:.2f} ms | "
                    f"non-success {session['non_success_count']} | tool errors {session['tool_error_count']} | "
                    f"max attempts {session['mission_max_attempts'] or 0:.2f} | cold starts {session['cold_start_count']}"
                )
            )
            matching_summary = next(
                (item for item in report["sessions"] if item["session_id"] == session["session_id"]),
                None,
            )
            if matching_summary is not None:
                lines.append(
                    f"  Decision: {matching_summary['decision']['label']} | {matching_summary['decision']['recommendation']}"
                )
    else:
        lines.append("- No session timing data found.")

    lines.extend(["", f"Top {top_n} Slowest Tools"])
    if report["slowest_tools"]:
        for tool in report["slowest_tools"]:
            lines.append(
                (
                    f"- {tool['tool_name']} (session {tool['session_id']}): "
                    f"avg {tool['avg_duration_ms'] or 0:.2f} ms | "
                    f"max {tool['max_duration_ms'] or 0:.2f} ms | "
                    f"calls {tool['calls']} | errors {tool['error_count']}"
                )
            )
    else:
        lines.append("- No tool timing data found.")

    lines.extend(["", f"Top {top_n} Most Unstable Tools"])
    if report["most_unstable_tools"]:
        for tool in report["most_unstable_tools"]:
            lines.append(
                (
                    f"- {tool['tool_name']} (session {tool['session_id']}): "
                    f"errors {tool['error_count']} / {tool['calls']} | "
                    f"error rate {tool['error_rate'] or 0:.2f}"
                )
            )
    else:
        lines.append("- No recurring tool failures found.")

    lines.extend(["", "Bottleneck Notes"])
    if report["bottlenecks"]:
        for bottleneck in report["bottlenecks"]:
            lines.append(f"- {bottleneck['message']}")
    else:
        lines.append("- No obvious bottleneck found in the scanned logs.")

    return "\n".join(lines)
