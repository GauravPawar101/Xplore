"""
Universal Code Parser — EzDocs

Extracts function and class definitions from source files in multiple languages
using tree-sitter for accurate, grammar-based parsing.

Supported extensions: .py  .js  .ts  .tsx  .java  .rs
"""

import logging
import sys
from pathlib import Path
from typing import Any

import tree_sitter
from tree_sitter_languages import get_language

log = logging.getLogger("ezdocs.parser")

# ─── Constants ────────────────────────────────────────────────────────────────

# Map file extension → tree-sitter language name
_EXT_TO_LANG: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".java": "java",
    ".rs":   "rust",
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_EXT_TO_LANG)

# Default max file size to parse (1 MB). Larger files are skipped.
DEFAULT_MAX_FILE_SIZE = 1024 * 1024

# Tree-sitter S-expression queries per language.
# Each query captures `*.def` (the full definition node) and
# `*.name` (the identifier node carrying the name).
_QUERIES: dict[str, str] = {
    "python": """
        (function_definition  name: (identifier) @function.name) @function.def
        (class_definition     name: (identifier) @class.name)    @class.def
    """,

    "javascript": """
        (function_declaration
            name: (identifier) @function.name) @function.def
        (method_definition
            name: (property_identifier) @function.name) @function.def
        (variable_declarator
            name: (identifier) @function.name
            value: (arrow_function)) @function.def
        (class_declaration
            name: (identifier) @class.name) @class.def
    """,

    "typescript": """
        (function_declaration
            name: (identifier) @function.name) @function.def
        (method_definition
            name: (property_identifier) @function.name) @function.def
        (variable_declarator
            name: (identifier) @function.name
            value: (arrow_function)) @function.def
        (class_declaration
            name: (type_identifier) @class.name) @class.def
        (interface_declaration
            name: (type_identifier) @class.name) @class.def
    """,

    "java": """
        (method_declaration      name: (identifier) @function.name) @function.def
        (constructor_declaration name: (identifier) @function.name) @function.def
        (class_declaration       name: (identifier) @class.name)    @class.def
        (interface_declaration   name: (identifier) @class.name)    @class.def
        (enum_declaration        name: (identifier) @class.name)    @class.def
    """,

    "rust": """
        (function_item name: (identifier)      @function.name) @function.def
        (struct_item   name: (type_identifier) @class.name)    @class.def
        (enum_item     name: (type_identifier) @class.name)    @class.def
        (trait_item    name: (type_identifier) @class.name)    @class.def
        (impl_item     trait: (type_identifier)? @class.name)  @class.def
    """,
}

# ─── Result type alias ────────────────────────────────────────────────────────

ParseResult = dict[str, Any]
# Keys: name (str), type (str), start_line (int), end_line (int), code (str)


# ─── Parser ───────────────────────────────────────────────────────────────────

class UniversalParser:
    """
    Grammar-based parser that extracts function and class definitions from
    source files.  Parsers and compiled queries are lazily initialised and
    cached so repeated calls to ``parse_file`` are inexpensive.

    Example::

        parser = UniversalParser()
        for item in parser.parse_file("src/main.py"):
            print(item["type"], item["name"], item["start_line"])
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._queries: dict[str, Any] = {}

    # ── Public helpers ─────────────────────────────────────────────────────

    def is_supported(self, filepath: str) -> bool:
        """Return ``True`` if the file extension is supported."""
        return Path(filepath).suffix.lower() in SUPPORTED_EXTENSIONS

    # ── Lazy cache accessors ───────────────────────────────────────────────

    def _parser(self, language: str) -> Any:
        if language not in self._parsers:
            try:
                lang_obj = get_language(language)
                p = tree_sitter.Parser()
                p.set_language(lang_obj)
                self._parsers[language] = p
            except Exception as exc:
                raise RuntimeError(f"Cannot load tree-sitter parser for {language!r}: {exc}") from exc
        return self._parsers[language]

    def _query(self, language: str) -> Any:
        if language not in self._queries:
            try:
                lang_obj = get_language(language)
                self._queries[language] = lang_obj.query(_QUERIES[language])
            except Exception as exc:
                raise RuntimeError(f"Cannot compile tree-sitter query for {language!r}: {exc}") from exc
        return self._queries[language]

    # ── Core parsing logic ─────────────────────────────────────────────────

    @staticmethod
    def _decode(raw: bytes) -> str:
        return raw.decode("utf-8", errors="replace")

    def _extract(self, node: Any, source: bytes, node_type: str, name: str | None) -> ParseResult:
        return {
            "name":       name or "<anonymous>",
            "type":       node_type,
            "start_line": node.start_point[0] + 1,   # 0-indexed → 1-indexed
            "end_line":   node.end_point[0] + 1,
            "code":       self._decode(source[node.start_byte:node.end_byte]),
        }

    def _process_captures(self, captures: list, source: bytes) -> list[ParseResult]:
        """
        Convert raw tree-sitter captures into ``ParseResult`` dicts.

        Two-pass approach:
          1. Walk `*.name` captures to build a ``(start_byte, end_byte) → name`` map.
          2. Walk `*.def` captures to emit results, deduplicating by byte span.
        """
        # Pass 1 — name map keyed by the *definition* node's byte span.
        # A name node's parent is the definition node.
        name_map: dict[tuple[int, int], str] = {}
        for node, capture_name in captures:
            if ".name" not in capture_name:
                continue
            parent = node.parent
            if parent is None:
                continue
            span = (parent.start_byte, parent.end_byte)
            if span not in name_map:
                name_map[span] = self._decode(source[node.start_byte:node.end_byte])

        # Pass 2 — emit one result per unique definition span.
        results: list[ParseResult] = []
        seen: set[tuple[int, int]] = set()

        for node, capture_name in captures:
            if ".def" not in capture_name:
                continue

            span = (node.start_byte, node.end_byte)
            if span in seen:
                continue
            seen.add(span)

            if "function" in capture_name:
                node_type = "function"
            elif "class" in capture_name:
                node_type = "class"
            else:
                continue

            results.append(self._extract(node, source, node_type, name_map.get(span)))

        results.sort(key=lambda r: r["start_line"])
        return results

    # ── Public API ─────────────────────────────────────────────────────────

    def parse_file(
        self,
        filepath: str,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> list[ParseResult]:
        """
        Parse *filepath* and return all function / class definitions found.

        Args:
            filepath:      Path to the source file.
            max_file_size: Files larger than this (bytes) are skipped silently.

        Returns:
            List of dicts with keys ``name``, ``type``, ``start_line``,
            ``end_line``, ``code``, sorted by ``start_line``.

        Raises:
            FileNotFoundError: File does not exist.
            IsADirectoryError: Path is a directory.
            PermissionError:   File cannot be read.
            ValueError:        Extension is not supported.
            RuntimeError:      Parser / query initialisation failed, or
                               an unexpected error occurred during parsing.
        """
        path = Path(filepath)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {filepath}")

        if path.stat().st_size > max_file_size:
            log.debug("Skipping oversized file (%d B): %s", path.stat().st_size, filepath)
            return []

        language = _EXT_TO_LANG.get(path.suffix.lower())
        if language is None:
            raise ValueError(
                f"Unsupported extension {path.suffix!r}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        try:
            source = path.read_bytes()
        except PermissionError:
            raise
        except OSError as exc:
            raise RuntimeError(f"Cannot read {filepath}: {exc}") from exc

        try:
            tree     = self._parser(language).parse(source)
            captures = self._query(language).captures(tree.root_node)
            return self._process_captures(captures, source)
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Unexpected parse error in {filepath}: {exc}") from exc


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _cli() -> None:
    """Quick smoke-test / manual inspection tool."""
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python parser.py <filepath>")
        print(f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    parser = UniversalParser()
    target = sys.argv[1]

    try:
        results = parser.parse_file(target)
    except (FileNotFoundError, ValueError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    bar = "─" * 72
    print(f"\n{bar}")
    print(f"  {target}  —  {len(results)} definition(s) found")
    print(bar)
    for i, item in enumerate(results, 1):
        preview = item["code"].replace("\n", "↵ ")[:80]
        ellipsis = "…" if len(item["code"]) > 80 else ""
        print(f"  {i:>3}. [{item['type']:8}] {item['name']}  (L{item['start_line']}–{item['end_line']})")
        print(f"       {preview}{ellipsis}")
    print()


if __name__ == "__main__":
    _cli()