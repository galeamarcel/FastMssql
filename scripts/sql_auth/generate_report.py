from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


CASE_PATTERN = re.compile(r"^- `([A-Z]+-\d{3})`: (.+)$", re.MULTILINE)
STATUS_MAP = {
    "passed": "PASS",
    "failed": "FAIL",
    "error": "ERROR",
    "skipped": "SKIPPED",
}
STATUS_PRIORITY = {
    "NOT RUN": 0,
    "PASS": 1,
    "SKIPPED": 2,
    "FAIL": 3,
    "ERROR": 4,
}
LANE_DISPLAY_NAMES = {
    "upstream": "original-local-regression",
}
SECRET_NAME = re.compile(r"(?:PASSWORD|TOKEN|SECRET)", re.IGNORECASE)
INLINE_SECRET = re.compile(
    r"(?i)\b(password|pwd|token|secret)\s*=\s*([^;\s|]+)"
)


def _secret_values() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for name, value in os.environ.items()
                if value and SECRET_NAME.search(name)
            },
            key=len,
            reverse=True,
        )
    )


def redact(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        value = value.replace(secret, "<redacted>")
    return INLINE_SECRET.sub(r"\1=<redacted>", value)


def markdown_cell(value: object, secrets: tuple[str, ...]) -> str:
    text = redact(str(value or ""), secrets)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def parse_spec(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    cases = CASE_PATTERN.findall(text)
    if not cases:
        raise ValueError(f"no matrix cases found in {path}")
    ids = [case_id for case_id, _ in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate matrix IDs in {path}")
    category_order: dict[str, int] = {}
    for case_id in ids:
        category_order.setdefault(case_id.split("-", 1)[0], len(category_order))
    return sorted(
        cases,
        key=lambda item: (
            category_order[item[0].split("-", 1)[0]],
            int(item[0].split("-", 1)[1]),
        ),
    )


def _result_files(primary: Path, artifact_dir: Path) -> list[Path]:
    candidates = {primary}
    candidates.update(artifact_dir.glob("*-results.json"))
    return sorted(path for path in candidates if path.is_file())


def load_results(
    primary: Path,
    artifact_dir: Path,
    secrets: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for path in _result_files(primary, artifact_dir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case_id, raw in payload.get("cases", {}).items():
            outcome = STATUS_MAP.get(
                str(raw.get("outcome", "")).lower(),
                "ERROR",
            )
            candidate = {
                "status": outcome,
                "nodeid": redact(str(raw.get("nodeid", "")), secrets),
                "duration_seconds": float(raw.get("duration_seconds", 0.0)),
                "message": redact(str(raw.get("message", "")), secrets),
                "result_file": path.name,
            }
            existing = merged.get(case_id)
            if existing is None or STATUS_PRIORITY[outcome] >= STATUS_PRIORITY[
                str(existing["status"])
            ]:
                merged[case_id] = candidate
    return merged


def _junit_summary(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = list(root.findall(".//testsuite"))
    keys = ("tests", "failures", "errors", "skipped")
    return {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in keys
    }


def load_lanes(artifact_dir: Path, secrets: tuple[str, ...]) -> list[dict]:
    names = sorted(
        {
            path.name.removesuffix(".exitcode")
            for path in artifact_dir.glob("*.exitcode")
        }
        | {
            path.name.removesuffix(".xml")
            for path in artifact_dir.glob("*.xml")
        }
    )
    lanes: list[dict] = []
    for name in names:
        exit_path = artifact_dir / f"{name}.exitcode"
        command_path = artifact_dir / f"{name}.command"
        exit_code = (
            exit_path.read_text(encoding="utf-8").strip()
            if exit_path.is_file()
            else "NOT RUN"
        )
        command = (
            redact(command_path.read_text(encoding="utf-8").strip(), secrets)
            if command_path.is_file()
            else ""
        )
        lanes.append(
            {
                "name": LANE_DISPLAY_NAMES.get(name, name),
                "exit_code": exit_code,
                "command": command,
                "junit": _junit_summary(artifact_dir / f"{name}.xml"),
            }
        )
    return lanes


def load_case_metrics(artifact_dir: Path, filename: str) -> dict[str, dict]:
    path = artifact_dir / filename
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported framework metrics schema")
    return dict(payload.get("cases", {}))


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def render_matrix(
    cases: list[tuple[str, str]],
    results: dict[str, dict[str, object]],
    secrets: tuple[str, ...],
) -> str:
    lines = [
        "# FastMssql strict SQL-auth test matrix",
        "",
        "Missing evidence is reported as `NOT RUN`; it is never promoted to a pass.",
        "",
        "| Case | Status | Test node | Seconds | Evidence | Requirement |",
        "|---|---|---|---:|---|---|",
    ]
    for case_id, description in cases:
        result = results.get(case_id)
        if result is None:
            status = "NOT RUN"
            nodeid = ""
            duration = ""
            evidence = ""
        else:
            status = str(result["status"])
            nodeid = result["nodeid"]
            duration = f"{float(result['duration_seconds']):.6f}"
            evidence = result["message"] or result["result_file"]
        lines.append(
            "| "
            f"`{case_id}` | {status} | "
            f"{markdown_cell(nodeid, secrets)} | {duration} | "
            f"{markdown_cell(evidence, secrets)} | "
            f"{markdown_cell(description, secrets)} |"
        )
    return "\n".join(lines) + "\n"


def render_report(
    root: Path,
    cases: list[tuple[str, str]],
    results: dict[str, dict[str, object]],
    lanes: list[dict],
    framework_metrics: dict[str, dict],
    load_metrics: dict[str, dict],
    secrets: tuple[str, ...],
) -> str:
    counts = Counter(
        str(results.get(case_id, {}).get("status", "NOT RUN"))
        for case_id, _ in cases
    )
    lines = [
        "# FastMssql SQL-auth validation report",
        "",
        "## Scope",
        "",
        "- Authentication under test: SQL Server username/password only.",
        "- Azure authentication and Windows authentication are explicitly excluded.",
        "- SQL Server target: isolated Developer Edition container.",
        "- ARM64 hosts may execute the `linux/amd64` image under emulation; "
        "timings are diagnostic rather than marketing benchmarks.",
        "",
        "## Environment",
        "",
        f"- Python: `{platform.python_version()}`",
        f"- Platform: `{markdown_cell(platform.platform(), secrets)}`",
        f"- Machine: `{markdown_cell(platform.machine(), secrets)}`",
        f"- Git commit: `{_git_commit(root)}`",
        "",
        "## Matrix outcomes",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status in ("PASS", "FAIL", "ERROR", "SKIPPED", "NOT RUN"):
        lines.append(f"| {status} | {counts[status]} |")

    lines.extend(
        [
            "",
            "## Execution lanes",
            "",
            "| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    if not lanes:
        lines.append("| none | NOT RUN | 0 | 0 | 0 | 0 | |")
    for lane in lanes:
        junit = lane["junit"]
        lines.append(
            f"| {markdown_cell(lane['name'], secrets)} | "
            f"{markdown_cell(lane['exit_code'], secrets)} | "
            f"{junit.get('tests', 0)} | {junit.get('failures', 0)} | "
            f"{junit.get('errors', 0)} | {junit.get('skipped', 0)} | "
            f"{markdown_cell(lane['command'], secrets)} |"
        )

    failures = [
        (case_id, results[case_id])
        for case_id, _ in cases
        if case_id in results
        and results[case_id]["status"] in {"FAIL", "ERROR"}
    ]
    lines.extend(["", "## Failures and errors", ""])
    if not failures:
        lines.append("None recorded.")
    else:
        for case_id, result in failures:
            lines.extend(
                [
                    f"### {case_id} — {result['status']}",
                    "",
                    f"- Node: `{markdown_cell(result['nodeid'], secrets)}`",
                    f"- Evidence: {markdown_cell(result['message'], secrets)}",
                    "",
                ]
            )

    lines.extend(
        [
            "",
            "## Framework execution models",
            "",
            "- **FastAPI/native ASGI:** true-async end-to-end only when "
            "`FRAME-005` through `FRAME-013` pass.",
            "- **Flask/WSGI:** functional async-view compatibility; each "
            "request remains worker-bound.",
            "- **Flask via WsgiToAsgi:** persistent event-loop compatibility; "
            "the application remains adapted WSGI, not native ASGI.",
            "",
            "### Fixed exclusions",
            "",
            "- Production deployment tuning for Gunicorn, uWSGI, Hypercorn, "
            "or Uvicorn.",
            "- WebSockets.",
            "- Framework authentication, authorization, serialization, or "
            "ORM behavior.",
            "- Quart, gevent, eventlet, or non-`asyncio` event loops.",
            "- Multi-process pool sharing; each process must own its own pool.",
            "- Windows or Azure SQL authentication.",
            "",
            "### Framework metrics",
            "",
            "| Case | Metrics |",
            "|---|---|",
        ]
    )
    if not framework_metrics:
        lines.append("| none | NOT RUN |")
    for case_id, metrics in sorted(framework_metrics.items()):
        lines.append(
            f"| `{case_id}` | "
            f"{markdown_cell(json.dumps(metrics, sort_keys=True), secrets)} |"
        )
    lines.extend(
        [
            "",
            "## Load metrics",
            "",
            "| Case | Metrics |",
            "|---|---|",
        ]
    )
    if not load_metrics:
        lines.append("| none | NOT RUN |")
    for case_id, metrics in sorted(load_metrics.items()):
        lines.append(
            f"| `{case_id}` | "
            f"{markdown_cell(json.dumps(metrics, sort_keys=True), secrets)} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--strict-results", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--matrix-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail after writing reports when any matrix case lacks evidence",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    secrets = _secret_values()
    cases = parse_spec(args.spec)
    results = load_results(
        args.strict_results,
        args.artifact_dir,
        secrets,
    )
    lanes = load_lanes(args.artifact_dir, secrets)
    framework_metrics = load_case_metrics(
        args.artifact_dir, "framework-metrics.json"
    )
    load_metrics = load_case_metrics(args.artifact_dir, "load-metrics.json")
    root = Path(__file__).resolve().parents[2]
    matrix = render_matrix(cases, results, secrets)
    report = render_report(
        root,
        cases,
        results,
        lanes,
        framework_metrics,
        load_metrics,
        secrets,
    )
    args.matrix_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.matrix_output.write_text(matrix, encoding="utf-8")
    args.report_output.write_text(report, encoding="utf-8")
    missing = [case_id for case_id, _ in cases if case_id not in results]
    if args.require_complete and missing:
        print(
            f"missing evidence for {len(missing)} case(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
