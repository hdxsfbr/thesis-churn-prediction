import ast
from pathlib import Path


def test_synth_does_not_import_features_or_model():
    for path in Path('src/churn/synth').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ''] + [f'{node.module}.{a.name}' for a in node.names]
            else:
                continue
            assert not any(n.startswith(('churn.features', 'churn.model')) for n in names), path
