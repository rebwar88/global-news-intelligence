"""Create a privacy-conscious operational report for a completed GitHub Actions CI run."""
from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


def _parse_junit(path: Path) -> dict[str, Any]:
    empty = {"available": False, "tests": 0, "failures": 0, "errors": 0, "skipped": 0, "duration_seconds": 0.0}
    if not path.is_file():
        return empty
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        suites = [root]
    totals = {**empty, "available": True}
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            try:
                totals[key] += int(suite.attrib.get(key, "0"))
            except ValueError:
                continue
        try:
            totals["duration_seconds"] += float(suite.attrib.get("time", "0"))
        except ValueError:
            continue
    totals["duration_seconds"] = round(totals["duration_seconds"], 3)
    return totals


def _duration(started: str, updated: str) -> float | None:
    if not started or not updated:
        return None
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(updated)
    except ValueError:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def build_report(env: dict[str, str], junit_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "platform": "github-actions",
        "workflow": "CI",
        "run_id": env.get("GNI_RUN_ID", ""),
        "run_number": env.get("GNI_RUN_NUMBER", ""),
        "conclusion": env.get("GNI_CONCLUSION", "unknown"),
        "branch": env.get("GNI_BRANCH", ""),
        "commit_sha": env.get("GNI_SHA", ""),
        "run_url": env.get("GNI_URL", ""),
        "trigger": env.get("GNI_EVENT", ""),
        "actor": env.get("GNI_ACTOR", ""),
        "duration_seconds": _duration(env.get("GNI_STARTED_AT", ""), env.get("GNI_UPDATED_AT", "")),
        "tests": _parse_junit(junit_path),
    }


def render_markdown(report: dict[str, Any]) -> str:
    tests = report["tests"]
    lines = [
        "# Global News Intelligence — CI operational report",
        "",
        f"- **Result:** {str(report['conclusion']).upper()}",
        f"- **Run:** #{report['run_number']} (ID: {report['run_id']})",
        f"- **Branch:** {report['branch']}",
        f"- **Commit:** {report['commit_sha'][:12]}",
        f"- **Trigger:** {report['trigger']}",
        f"- **Actor:** {report['actor']}",
        f"- **Workflow duration:** {report['duration_seconds'] if report['duration_seconds'] is not None else 'not available'} seconds",
        f"- **Run details:** {report['run_url']}",
        "",
        "## Test results",
    ]
    if tests["available"]:
        passed = max(0, tests["tests"] - tests["failures"] - tests["errors"] - tests["skipped"])
        lines.extend([
            f"- Tests: **{tests['tests']}**",
            f"- Passed: **{passed}**",
            f"- Failed: **{tests['failures']}**",
            f"- Errors: **{tests['errors']}**",
            f"- Skipped: **{tests['skipped']}**",
            f"- Test duration: **{tests['duration_seconds']} seconds**",
        ])
    else:
        lines.append("JUnit report unavailable (the test step may not have started or the artifact could not be downloaded).")
    lines.extend([
        "",
        "> This report contains workflow metadata and test counts only. It does not include source content, credentials, job payloads, or raw test failure output.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, default=Path("artifacts/junit.xml"))
    parser.add_argument("--json-out", type=Path, default=Path("artifacts/monitoring-report.json"))
    parser.add_argument("--markdown-out", type=Path, default=Path("artifacts/monitoring-report.md"))
    args = parser.parse_args()

    report = build_report(dict(os.environ), args.junit)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown_out.write_text(render_markdown(report), encoding="utf-8")
    print(f"Operational report created for CI run #{report['run_number']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
