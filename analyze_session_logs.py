import argparse
import json
import os

from core.log_analysis import analyze_workspace_logs, render_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze 0-HITL session JSONL logs and surface timing bottlenecks."
    )
    parser.add_argument(
        "--workspace",
        default="./workspace",
        help="Workspace root containing sessions/<session_id>/logs/session.jsonl",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Optional session ID filter.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many top slow sessions/tools to display.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Render the analysis as JSON instead of plain text.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional JSON output path. If a directory is given, the report is written "
            "to perf-latest.json inside it."
        ),
    )
    return parser


def write_report_output(output_path: str, report: dict) -> str:
    candidate = os.path.abspath(output_path)
    if output_path.endswith(("/", "\\")) or os.path.isdir(candidate):
        candidate = os.path.join(candidate, "perf-latest.json")

    os.makedirs(os.path.dirname(candidate), exist_ok=True)
    with open(candidate, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return candidate


def main():
    parser = build_parser()
    args = parser.parse_args()

    report = analyze_workspace_logs(
        workspace_root=args.workspace,
        session_id=args.session,
        top_n=max(args.top, 1),
    )

    written_path = None
    if args.output:
        written_path = write_report_output(args.output, report)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    rendered = render_report(report, top_n=max(args.top, 1))
    if written_path:
        rendered += f"\n\nSaved JSON report to: {written_path}"
    print(rendered)


if __name__ == "__main__":
    main()
