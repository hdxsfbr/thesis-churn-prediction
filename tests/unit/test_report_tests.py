"""T28: churn report-tests renders JUnit XML, including slow and scale metrics when present."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from churn.cli import app

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="tests.unit.test_docs_coverage" name="test_ok" time="0.01"/>
  <testcase classname="tests.unit.test_docs_coverage" name="test_gap" time="0.02">
    <failure message="assert coverage">trace</failure>
  </testcase>
  <testcase classname="tests.integration.test_evaluation_run" name="test_coverage" time="15.1">
    <skipped message="slow"/>
    <properties>
      <property name="marker" value="slow"/>
      <property name="coverage" value="0.91"/>
    </properties>
  </testcase>
  <testcase classname="tests.scale.test_throughput" name="test_rows" time="40">
    <properties>
      <property name="marker" value="scale"/>
      <property name="rows" value="350000"/>
    </properties>
  </testcase>
</testsuite>
"""


def test_junit_xml_rendered_to_test_results_md(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    output = tmp_path / "test-results.md"
    junit.write_text(XML)
    result = CliRunner().invoke(app, ["report-tests", "--junit", str(junit), "--output", str(output)])
    assert result.exit_code == 0, result.output
    text = output.read_text()
    assert "- tests: 3" in text
    assert "- failures: 1" in text
    assert "- skipped: 1" in text
    assert "tests.unit.test_docs_coverage::test_gap: assert coverage" in text
    assert "coverage=0.91" in text
    assert "rows=350000" in text
    assert "| slow |" in text
    assert "| scale |" in text
