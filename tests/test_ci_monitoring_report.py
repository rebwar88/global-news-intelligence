from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ci_monitoring_report.py"
SPEC = importlib.util.spec_from_file_location("ci_monitoring_report", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
REPORT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT_MODULE)


def test_report_parses_junit_totals_without_failure_output(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites tests="4" failures="1" errors="0" skipped="1" time="2.5">'
        '<testsuite name="unit" tests="4" failures="1" errors="0" skipped="1" time="2.5">'
        '<testcase classname="test_safe" name="test_ok"/>'
        '<testcase classname="test_safe" name="test_bad"><failure message="secret-value">sensitive output</failure></testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    report = REPORT_MODULE.build_report(
        {"GNI_RUN_NUMBER": "42", "GNI_CONCLUSION": "failure"}, junit
    )
    assert report["tests"] == {
        "available": True,
        "tests": 4,
        "failures": 1,
        "errors": 0,
        "skipped": 1,
        "duration_seconds": 2.5,
    }
    markdown = REPORT_MODULE.render_markdown(report)
    assert "Passed: **2**" in markdown
    assert "secret-value" not in markdown
    assert "sensitive output" not in markdown


def test_report_handles_missing_junit_artifact(tmp_path):
    report = REPORT_MODULE.build_report({}, tmp_path / "missing.xml")
    assert report["tests"]["available"] is False
    assert report["tests"]["tests"] == 0
    assert "unavailable" in REPORT_MODULE.render_markdown(report)


def test_report_duration_and_json_output(tmp_path, monkeypatch):
    env = {
        "GNI_RUN_ID": "100",
        "GNI_RUN_NUMBER": "7",
        "GNI_CONCLUSION": "success",
        "GNI_BRANCH": "main",
        "GNI_SHA": "abcdef123456",
        "GNI_URL": "https://github.com/example/repo/actions/runs/100",
        "GNI_EVENT": "push",
        "GNI_ACTOR": "automation",
        "GNI_STARTED_AT": "2026-11-14T10:00:00Z",
        "GNI_UPDATED_AT": "2026-11-14T10:00:09Z",
    }
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1"/>', encoding="utf-8")
    report = REPORT_MODULE.build_report(env, junit)
    assert report["duration_seconds"] == 9.0
    assert report["tests"]["tests"] == 1
    json_path = tmp_path / "out" / "report.json"
    markdown_path = tmp_path / "out" / "report.md"
    monkeypatch.setattr(REPORT_MODULE.os, "environ", env)
    monkeypatch.setattr(
        "sys.argv",
        ["ci_monitoring_report.py", "--junit", str(junit), "--json-out", str(json_path), "--markdown-out", str(markdown_path)],
    )
    assert REPORT_MODULE.main() == 0
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["run_id"] == "100"
    assert markdown_path.is_file()
