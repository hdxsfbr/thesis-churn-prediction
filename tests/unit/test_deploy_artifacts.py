"""The image, compose file, env example, and production-view switch stay config-only."""

import ast
from pathlib import Path

import yaml

ROOT = Path(".")
SNOWFLAKE = {
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
}


def _env_names(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if isinstance(node.args[0].value, str):
            owner = func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "environ":
                found.add(node.args[0].value)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Subscript):
            pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            value = node.slice.value
            owner = node.value
            if isinstance(value, str) and isinstance(owner, ast.Attribute) and owner.attr == "environ":
                found.add(value)
    return found


def test_compose_parses_and_defines_api_and_worker():
    doc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = doc["services"]
    assert {"api", "worker"} <= set(services)
    assert services["api"]["build"] == services["worker"]["build"]
    assert services["api"]["build"]["dockerfile"] == "docker/Dockerfile"
    assert "serve" in services["api"]["command"]
    assert services["worker"]["command"] == ["churn", "worker"]
    assert services["api"]["restart"] == "unless-stopped"
    assert services["worker"]["restart"] == "unless-stopped"


def test_dockerfile_non_root_and_python_312():
    text = (ROOT / "docker" / "Dockerfile").read_text()
    assert "python:3.12-slim" in text.splitlines()[0]
    users = [line.split()[1] for line in text.splitlines() if line.startswith("USER ")]
    assert users
    assert users[-1] != "root"
    assert "libgomp1" in text
    assert ".[snowflake,llm]" in text


def test_env_example_lists_every_settings_env_var():
    found = set()
    for path in Path("src/churn").rglob("*.py"):
        found |= _env_names(ast.parse(path.read_text()))
    example = (ROOT / ".env.example").read_text()
    listed = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line and not line.startswith("#")}
    assert found
    assert found | SNOWFLAKE <= listed
    assert "CHURN_BACKEND" in listed
    assert "CHURN_API_KEY" in listed
    assert "CHURN_KEY_PEPPER" in listed
    assert "SNOWFLAKE_PRIVATE_KEY_PATH" in listed


def test_switch_section_names_every_contract_view_and_no_code_steps():
    contract = yaml.safe_load((ROOT / "config" / "source_contract.yaml").read_text())
    guide = (ROOT / "docs" / "deployment.md").read_text()
    start = guide.index("## Switch to production views")
    rest = guide[start + len("## Switch to production views"):]
    end = rest.find("\n## ")
    section = rest if end < 0 else rest[:end]
    for name in contract["views"]:
        assert name in section
    assert ".py" not in section
    assert "src/churn" not in section
    assert "source_contract.yaml" in section
