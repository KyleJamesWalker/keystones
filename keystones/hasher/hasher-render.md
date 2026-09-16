# hasher-render

```toml
target = "src/keystones/hashing.py::_render"
hasher = "keystones-ast/1"
semantic = "sha256:45dfd628472e1f9114b75c708819e24d17963529be3caedcbcb4de83bf0fb926"
text = "sha256:33fb9527a859f7496c8c0ad68a9294d5dcbebc0e9d24f02d1d80bee8253197e3"
```

## Why

The hash basis itself. Any change to what this emits invalidates every sidecar in every consuming repo. Bump HASHER_ID and ship a migration before touching it.

## Canonical source

```python
def _render(node: object) -> str:
    if isinstance(node, ast.AST):
        parts = []
        for name in node._fields:
            if name in _SKIP_FIELDS:
                continue
            value = getattr(node, name, None)
            if _is_default(value):
                continue
            parts.append(f"{name}={_render(value)}")
        return f"{type(node).__name__}({','.join(parts)})"
    if isinstance(node, list):
        return f"[{','.join((_render(item) for item in node))}]"
    return f"{type(node).__name__}:{node!r}"
```

## History

- 2026-09-16 - initial keystone. Kyle James Walker
