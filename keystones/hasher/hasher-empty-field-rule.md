# hasher-empty-field-rule

```toml
target = "src/keystones/hashing.py::_is_default"
hasher = "keystones-ast/1"
semantic = "sha256:d68dfd52467bccf212ad8d50efd00ec4f0bb97e72453f7599596e9133c00161d"
text = "sha256:97a250e44d1a5827992e598ae30e4c16cd422e7068ef71fd4f31252e80e6d913"
```

## Why

Omitting empty and None fields is what keeps the hash identical across CPython minors. Changing this rule changes every stored hash in every consuming repo and requires a keystones-ast version bump plus a migration path.

## Canonical source

```python
def _is_default(value: object) -> bool:
    """Fields absent on older Pythons must render identically to empty ones.

    `type_params` (3.12) and `posonlyargs` (3.8) arrive as empty lists on code
    that does not use them; omitting empties keeps the hash stable across the
    versions in the support matrix.
    """
    return value is None or value == []
```

## History

- 2026-09-16 - initial keystone. Kyle James Walker
