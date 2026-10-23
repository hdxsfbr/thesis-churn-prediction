"""Pipeline code does not call Shopify, Klaviyo, Zendesk, or a general HTTP client."""

import ast
from pathlib import Path

ROOT = Path("src/churn")
BANNED = (
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "urllib.request",
    "http.client",
    "shopify",
    "klaviyo",
    "zendesk",
)


def test_no_shopify_klaviyo_zendesk_or_http_clients():
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                if module == "anthropic" or module.startswith("anthropic."):
                    continue
                if module == "openai" or module.startswith("openai."):
                    continue
                if module in BANNED or any(module.startswith(name + ".") for name in BANNED):
                    hits.append(f"{path}: {module}")
    assert hits == []
