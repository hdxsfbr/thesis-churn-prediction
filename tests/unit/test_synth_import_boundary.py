"""The generator emits events; it must never depend on the code that learns from them (ARCHITECTURE §2)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import churn.synth

FORBIDDEN = ("churn.features", "churn.model")


def _imports(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.append(base)
            found += [f"{base}.{a.name}" for a in node.names]
    return found


def test_synth_does_not_import_features_or_model():
    files = sorted(Path(churn.synth.__file__).parent.glob("*.py"))
    assert {"generator.py", "latent.py", "edge_cases.py", "profile.py"} <= {f.name for f in files}
    for f in files:
        for name in _imports(f):
            assert not name.startswith(FORBIDDEN), f"{f.name} imports {name}"

    code = (
        "import sys, churn.synth.generator, churn.synth.profile, churn.synth.edge_cases;"
        "bad=[m for m in sys.modules if m.startswith(('churn.features','churn.model'))];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
