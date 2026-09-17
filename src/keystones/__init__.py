"""Force SME review of load-bearing code by pinning a review gate to an AST node."""

from importlib.metadata import PackageNotFoundError, version

from keystones.hashing import HASHER_ID

try:
    __version__ = version("keystones")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0+unknown"

__all__ = ["HASHER_ID", "__version__"]
