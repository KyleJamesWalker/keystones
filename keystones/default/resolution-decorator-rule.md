# resolution-decorator-rule

```toml
target = "src/keystones/adapters/python.py::_start_line"
hasher = "keystones-ast/1"
semantic = "sha256:b3998477af1e839c1f6d4a0b5a809d069a0e706bd8da52cb185bac11e8afc8ce"
text = "sha256:a0b2ebecaaf1f409a7faaa7ff596c3b6c2e1d7e51de8a292d7f3ad246d90c8d5"
```

## Why

Decorators are part of the definition they decorate, so they set its first line. Without this a marker above a decorator resolves to the enclosing scope or the file instead of the function.

## Canonical source

```python
def _start_line(node: ast.AST) -> int:
    """Decorators are part of the definition, so they set its first line."""
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])
```

## History

- 2026-09-16 - initial keystone. Kyle James Walker
