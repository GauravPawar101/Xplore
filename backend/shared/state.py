"""
Shared in-memory state for the EzDocs API.

Used by graph and narrator logic. Survives uvicorn reload via mutable container.
"""

from typing import Any

# Graph cache: last analysis result for WebSocket narration and reuse
graph_cache: dict[str, Any] = {"graph": None}

# Lazy parser instance (shared across requests)
_parser: Any = None


def get_parser():
    """Return a lazily-initialised shared UniversalParser."""
    global _parser
    if _parser is None:
        from shared.parser import UniversalParser
        _parser = UniversalParser()
    return _parser
