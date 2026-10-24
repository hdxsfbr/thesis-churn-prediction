"""Turn a pytest JUnit file into docs/test-results.md."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def render_junit(xml_text: str) -> str:
    """Summarise a JUnit document. Slow and scale properties are copied through when the file has them."""
    root = ET.fromstring(xml_text)
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = failures = errors = skipped = 0
    failed_rows: list[str] = []
    slow_rows: list[str] = []
    for suite in suites:
        cases = list(suite.findall("testcase"))
        tests += int(suite.attrib.get("tests", len(cases)))
        failures += int(suite.attrib.get("failures", 0))
        errors += int(suite.attrib.get("errors", 0))
        skipped += int(suite.attrib.get("skipped", 0))
        for case in cases:
            name = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
            failure = case.find("failure")
            error = case.find("error")
            if failure is not None:
                failed_rows.append(f"- {name}: {failure.attrib.get('message', '').splitlines()[0]}")
            if error is not None:
                failed_rows.append(f"- {name}: {error.attrib.get('message', '').splitlines()[0]}")
            props = {
                prop.attrib.get("name", ""): prop.attrib.get("value", "")
                for prop in case.findall("properties/property")
            }
            marker = props.get("marker", "")
            if marker in {"slow", "scale"}:
                metrics = ", ".join(f"{key}={value}" for key, value in props.items() if key != "marker")
                slow_rows.append(f"| {name} | {marker} | {metrics or case.attrib.get('time', '')} |")
    lines = [
        "# Test results",
        "",
        f"- tests: {tests}",
        f"- failures: {failures}",
        f"- errors: {errors}",
        f"- skipped: {skipped}",
        "",
        "## Failures",
        "",
    ]
    lines.extend(failed_rows or ["None."])
    lines.extend(["", "## Slow and scale", ""])
    if slow_rows:
        lines.extend(["| test | marker | metrics |", "|---|---|---|", *slow_rows])
    else:
        lines.append("No slow or scale metrics were in this JUnit file.")
    lines.append("")
    return "\n".join(lines)


def write_report(junit: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_junit(junit.read_text()))
