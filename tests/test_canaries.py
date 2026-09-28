"""Every hasher's output is pinned under its id.

A rendering change that ships without a version bump reads as drift in every
consuming repo, and `migrate` sees nothing to move. Four such changes went out
before this file existed. Here the digest is keyed by the hasher id, so a moved
digest can only be pinned again under a new id.
"""

import pytest

from keystones.adapters import fallback, python, structured

PY = "# keystone: k\ndef f(x):\n    return x * 2  # doubled\n"
TXT = "-- keystone:start: k\nselect 1 -- one\n/* two */\n-- keystone:end\n"
YML = "a:\n  # keystone(hash=yaml): k\n  b: 1  # note\n  c: [1, 2]\n"

# hasher id -> (semantic, text) digests of the samples above. Change the id
# with the rendering, never the digest alone.
CANARIES = {
    "keystones-ast/1": (
        "sha256:fc6a3318922aea0be99af1ca738456dfd9d39f83f3c11af628156076f8502214",
        "sha256:73c6382be3c16f43095e577a2f20dbda755d554f0750dfddc707047b2d85350b",
    ),
    "keystones-text/3": (
        "sha256:77449cb2e7efefbd776652db04381097dfbc50a373ebb5e402a82ed8c3f69b97",
        "sha256:1ef93593ff1ffb9273acba8a37478a84c1cabf18fe2bc0fd998f67d8bd87d57b",
    ),
    "keystones-yaml/3": (
        "sha256:6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b",
        "sha256:5f8f20502574eb8e738aba9a87035a9345dd659dd990a2c6c9d82e465de6a9ec",
    ),
}


def _pair(adapter, path, src):
    marker = adapter.markers(path, src)[0]
    target = adapter.resolve(src, marker)
    return adapter.hashes(src, target)


def test_python_canary():
    hasher = python.hasher_id_for_path("a.py")
    assert _pair(python, "a.py", PY) == CANARIES[hasher], hasher


def test_text_canary():
    hasher = fallback.hasher_id_for_path("a.ddl")
    assert _pair(fallback, "a.ddl", TXT) == CANARIES[hasher], hasher


def test_yaml_canary():
    if not structured.available():
        pytest.skip("needs PyYAML")
    hasher = structured.hasher_id_for_path("a.yaml").split("+")[0]
    assert _pair(structured, "a.yaml", YML) == CANARIES[hasher], hasher


def test_every_pinned_id_is_the_current_one():
    """A stale key means a bump happened without re-pinning."""
    current = {
        python.hasher_id_for_path("a.py"),
        fallback.hasher_id_for_path("a.ddl"),
        structured.hasher_id_for_path("a.yaml").split("+")[0],
    }
    assert set(CANARIES) == current
