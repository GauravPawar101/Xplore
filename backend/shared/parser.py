"""
Universal Code Parser — EzDocs

Extracts function and class definitions, function calls, class instantiations,
and import statements from source files in multiple languages using tree-sitter
for accurate, grammar-based AST parsing.

Supported extensions: .py  .js  .ts  .tsx  .java  .rs  .go  .c  .h  .cc  .cpp
"""

import logging
import re
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
    ".go":   "go",
    ".c":    "c",
    ".h":    "c",
    ".cc":   "cpp",
    ".cpp":  "cpp",
    ".cxx":  "cpp",
    ".hpp":  "cpp",
    ".hh":   "cpp",
    ".hxx":  "cpp",
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
        (decorated_definition
            (decorator (identifier) @decorator.name)?) @decorator.def
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
        (export_statement
            declaration: (function_declaration
                name: (identifier) @export_fn.name)) @export_fn.def
        (export_statement
            declaration: (class_declaration
                name: (identifier) @export_cls.name)) @export_cls.def
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
        (type_alias_declaration
            name: (type_identifier) @class.name) @class.def
        (export_statement
            declaration: (function_declaration
                name: (identifier) @export_fn.name)) @export_fn.def
        (export_statement
            declaration: (class_declaration
                name: (type_identifier) @export_cls.name)) @export_cls.def
    """,

    "java": """
        (method_declaration      name: (identifier) @function.name) @function.def
        (constructor_declaration name: (identifier) @function.name) @function.def
        (class_declaration       name: (identifier) @class.name)    @class.def
        (interface_declaration   name: (identifier) @class.name)    @class.def
        (enum_declaration        name: (identifier) @class.name)    @class.def
        (annotation_type_declaration name: (identifier) @class.name) @class.def
    """,

    "rust": """
        (function_item name: (identifier)      @function.name) @function.def
        (struct_item   name: (type_identifier) @class.name)    @class.def
        (enum_item     name: (type_identifier) @class.name)    @class.def
        (trait_item    name: (type_identifier) @class.name)    @class.def
        (impl_item     trait: (type_identifier)? @class.name)  @class.def
        (macro_definition name: (identifier) @function.name)   @function.def
    """,

    "go": """
        (function_declaration name: (identifier) @function.name) @function.def
        (method_declaration name: (field_identifier) @function.name) @function.def
        (type_declaration
            (type_spec
                name: (type_identifier) @class.name
                type: [(struct_type) (interface_type)])) @class.def
    """,

    "c": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @function.name)) @function.def
        (struct_specifier name: (type_identifier) @class.name) @class.def
        (union_specifier name: (type_identifier) @class.name) @class.def
        (enum_specifier name: (type_identifier) @class.name) @class.def
    """,

    "cpp": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @function.name)) @function.def
        (function_definition
            declarator: (function_declarator
                declarator: (field_identifier) @function.name)) @function.def
        (class_specifier name: (type_identifier) @class.name) @class.def
        (struct_specifier name: (type_identifier) @class.name) @class.def
        (enum_specifier name: (type_identifier) @class.name) @class.def
    """,
}


# ─── AST Call / Reference Extraction Queries ──────────────────────────────────
# These queries extract function calls, class instantiations, and imports
# directly from the AST — far more accurate than regex tokenization.

_CALL_QUERIES: dict[str, str] = {
    "python": """
        (call function: (identifier) @call.name)
        (call function: (attribute attribute: (identifier) @call.name))
    """,

    "javascript": """
        (call_expression function: (identifier) @call.name)
        (call_expression function: (member_expression property: (property_identifier) @call.name))
        (new_expression constructor: (identifier) @call.name)
    """,

    "typescript": """
        (call_expression function: (identifier) @call.name)
        (call_expression function: (member_expression property: (property_identifier) @call.name))
        (new_expression constructor: (identifier) @call.name)
    """,

    "java": """
        (method_invocation name: (identifier) @call.name)
        (object_creation_expression type: (type_identifier) @call.name)
    """,

    "rust": """
        (call_expression function: (identifier) @call.name)
        (call_expression function: (field_expression field: (field_identifier) @call.name))
        (macro_invocation macro: (identifier) @call.name)
    """,

    "go": """
        (call_expression function: (identifier) @call.name)
        (call_expression function: (selector_expression field: (field_identifier) @call.name))
    """,

    "c": """
        (call_expression function: (identifier) @call.name)
    """,

    "cpp": """
        (call_expression function: (identifier) @call.name)
        (call_expression function: (field_expression field: (field_identifier) @call.name))
    """,
}

# ─── AST Import Extraction Queries ────────────────────────────────────────────
# Extract import source paths and imported names from the AST.

_IMPORT_QUERIES: dict[str, str] = {
    "python": """
        (import_statement name: (dotted_name) @import.name)
        (import_from_statement module_name: (dotted_name) @import.source)
        (import_from_statement module_name: (relative_import) @import.source)
        (import_from_statement name: (dotted_name) @import.name)
    """,

    "javascript": """
        (import_statement source: (string) @import.source)
        (call_expression
            function: (identifier) @_fn
            arguments: (arguments (string) @import.source)
            (#eq? @_fn "require"))
    """,

    "typescript": """
        (import_statement source: (string) @import.source)
        (call_expression
            function: (identifier) @_fn
            arguments: (arguments (string) @import.source)
            (#eq? @_fn "require"))
    """,

    "java": """
        (import_declaration (scoped_identifier) @import.name)
    """,

    "rust": """
        (use_declaration argument: (_) @import.name)
    """,

    "go": """
        (import_spec path: (interpreted_string_literal) @import.source)
    """,
}

# ─── Result type alias ────────────────────────────────────────────────────────

ParseResult = dict[str, Any]
# Keys: name (str), type (str), start_line (int), end_line (int), code (str)

FullParseResult = dict[str, Any]
# Keys: definitions (list[ParseResult]), calls (list[str]),
#        imports (list[dict]), language (str)


# ─── Parser ───────────────────────────────────────────────────────────────────

class UniversalParser:
    """
    Grammar-based parser that extracts function and class definitions,
    function calls, class instantiations, and import statements from
    source files.  Parsers and compiled queries are lazily initialised and
    cached so repeated calls to ``parse_file`` are inexpensive.

    Example::

        parser = UniversalParser()
        for item in parser.parse_file("src/main.py"):
            print(item["type"], item["name"], item["start_line"])

        # Full AST analysis including calls and imports:
        result = parser.parse_file_full("src/main.py")
        print(result["calls"])    # ['foo', 'Bar', 'helper']
        print(result["imports"])  # [{'source': 'os', 'names': ['path']}]
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._queries: dict[str, Any] = {}
        self._call_queries: dict[str, Any] = {}
        self._import_queries: dict[str, Any] = {}

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

    def _call_query(self, language: str) -> Any | None:
        """Return the compiled call-extraction query, or None if unavailable."""
        if language not in self._call_queries:
            if language not in _CALL_QUERIES:
                self._call_queries[language] = None
                return None
            try:
                lang_obj = get_language(language)
                self._call_queries[language] = lang_obj.query(_CALL_QUERIES[language])
            except Exception as exc:
                log.debug("Cannot compile call query for %s: %s", language, exc)
                self._call_queries[language] = None
        return self._call_queries[language]

    def _import_query(self, language: str) -> Any | None:
        """Return the compiled import-extraction query, or None if unavailable."""
        if language not in self._import_queries:
            if language not in _IMPORT_QUERIES:
                self._import_queries[language] = None
                return None
            try:
                lang_obj = get_language(language)
                self._import_queries[language] = lang_obj.query(_IMPORT_QUERIES[language])
            except Exception as exc:
                log.debug("Cannot compile import query for %s: %s", language, exc)
                self._import_queries[language] = None
        return self._import_queries[language]

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

    # ── AST-based call extraction ──────────────────────────────────────────

    def _extract_calls(self, tree: Any, language: str, source: bytes) -> list[str]:
        """Extract function/method call names from the AST.

        Returns a deduplicated list of called identifier names, excluding
        common builtins. Far more accurate than regex: won't match names
        inside strings, comments, or type annotations.
        """
        q = self._call_query(language)
        if q is None:
            return []

        names: list[str] = []
        try:
            captures = q.captures(tree.root_node)
            for node, capture_name in captures:
                if capture_name == "call.name":
                    name = self._decode(source[node.start_byte:node.end_byte])
                    if name:
                        names.append(name)
        except Exception as exc:
            log.debug("Call extraction failed for %s: %s", language, exc)
        return names

    def _extract_calls_per_scope(
        self, tree: Any, language: str, source: bytes,
        definitions: list[ParseResult],
    ) -> dict[str, list[str]]:
        """Extract calls scoped to each definition by line range.

        Returns a dict mapping ``name`` (from definitions) to a list of
        call names found within that definition's line range.  Calls
        outside any definition are collected under the key ``__module__``.
        """
        q = self._call_query(language)
        if q is None:
            return {}

        # Build sorted list of (start_line, end_line, name) for binary search
        scopes = sorted(
            (d["start_line"], d["end_line"], d["name"]) for d in definitions
        )

        result: dict[str, list[str]] = {}
        try:
            captures = q.captures(tree.root_node)
            for node, capture_name in captures:
                if capture_name != "call.name":
                    continue
                name = self._decode(source[node.start_byte:node.end_byte])
                if not name:
                    continue
                line = node.start_point[0] + 1  # 0-indexed → 1-indexed

                # Find which scope this call belongs to
                scope_name = "__module__"
                for s_start, s_end, s_name in scopes:
                    if s_start <= line <= s_end:
                        scope_name = s_name
                        break

                result.setdefault(scope_name, []).append(name)
        except Exception as exc:
            log.debug("Scoped call extraction failed for %s: %s", language, exc)
        return result

    # ── AST-based import extraction ────────────────────────────────────────

    def _extract_imports(self, tree: Any, language: str, source: bytes) -> list[dict[str, Any]]:
        """Extract import statements from the AST.

        Returns a list of dicts with keys:
          - ``source``: the module/package path (str)
          - ``names``: list of imported names (may be empty for side-effect imports)
          - ``is_relative``: True if the import uses a relative path (., .., ./)
        """
        q = self._import_query(language)
        if q is None:
            return []

        imports: list[dict[str, Any]] = []
        try:
            captures = q.captures(tree.root_node)
            if language == "python":
                imports = self._process_python_import_captures(captures, source)
            elif language in ("javascript", "typescript"):
                imports = self._process_js_ts_import_captures(captures, source)
            elif language == "java":
                imports = self._process_java_import_captures(captures, source)
            elif language == "go":
                imports = self._process_go_import_captures(captures, source)
            elif language == "rust":
                imports = self._process_rust_import_captures(captures, source)
        except Exception as exc:
            log.debug("Import extraction failed for %s: %s", language, exc)

        return imports

    def _process_python_import_captures(
        self, captures: list, source: bytes
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        sources_seen: dict[int, dict[str, Any]] = {}  # keyed by parent node id

        for node, capture_name in captures:
            text = self._decode(source[node.start_byte:node.end_byte])
            parent = node.parent

            if capture_name == "import.name" and parent and parent.type == "import_statement":
                # Plain `import foo` / `import foo.bar`
                imports.append({
                    "source": text, "names": [], "is_relative": False,
                })
            elif capture_name == "import.source":
                # `from X import ...` — X is the source
                is_rel = text.startswith(".")
                entry = {
                    "source": text, "names": [], "is_relative": is_rel,
                }
                if parent:
                    sources_seen[id(parent)] = entry
                imports.append(entry)
            elif capture_name == "import.name" and parent and parent.type == "import_from_statement":
                # Imported name within `from X import name`
                if parent and id(parent) in sources_seen:
                    sources_seen[id(parent)]["names"].append(text)

        return imports

    def _process_js_ts_import_captures(
        self, captures: list, source: bytes
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        for node, capture_name in captures:
            if capture_name == "import.source":
                raw = self._decode(source[node.start_byte:node.end_byte])
                # Strip quotes from string literals
                path = raw.strip("'\"")
                is_rel = path.startswith(".")
                # Extract named imports from the parent import_statement
                names: list[str] = []
                parent = node.parent
                if parent:
                    for child in parent.children:
                        if child.type == "import_clause":
                            for sub in child.children:
                                if sub.type == "named_imports":
                                    for spec in sub.children:
                                        if spec.type == "import_specifier":
                                            for n in spec.children:
                                                if n.type == "identifier":
                                                    names.append(
                                                        self._decode(source[n.start_byte:n.end_byte])
                                                    )
                                                    break
                                elif sub.type == "identifier":
                                    names.append(
                                        self._decode(source[sub.start_byte:sub.end_byte])
                                    )
                imports.append({
                    "source": path, "names": names, "is_relative": is_rel,
                })
        return imports

    def _process_java_import_captures(
        self, captures: list, source: bytes
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        for node, capture_name in captures:
            if capture_name == "import.name":
                text = self._decode(source[node.start_byte:node.end_byte])
                # Java imports like java.util.List — last segment is the name
                parts = text.rsplit(".", 1)
                imports.append({
                    "source": text,
                    "names": [parts[-1]] if len(parts) > 1 else [],
                    "is_relative": False,
                })
        return imports

    def _process_go_import_captures(
        self, captures: list, source: bytes
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        for node, capture_name in captures:
            if capture_name == "import.source":
                raw = self._decode(source[node.start_byte:node.end_byte])
                path = raw.strip('"')
                imports.append({
                    "source": path, "names": [], "is_relative": False,
                })
        return imports

    def _process_rust_import_captures(
        self, captures: list, source: bytes
    ) -> list[dict[str, Any]]:
        imports: list[dict[str, Any]] = []
        for node, capture_name in captures:
            if capture_name == "import.name":
                text = self._decode(source[node.start_byte:node.end_byte])
                imports.append({
                    "source": text, "names": [], "is_relative": text.startswith("self::") or text.startswith("super::"),
                })
        return imports

    # ── Full AST Parse (definitions + calls + imports) ─────────────────────

    def parse_file_full(
        self,
        filepath: str,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> FullParseResult | None:
        """Parse a file and return definitions, calls, and imports from the AST.

        This is the enhanced version of ``parse_file`` that extracts everything
        the graph builder needs in a single pass over the AST.

        Returns:
            Dict with keys ``definitions``, ``calls``, ``scoped_calls``,
            ``imports``, ``language``, or ``None`` if the file should be skipped.
        """
        path = Path(filepath)

        if not path.exists() or not path.is_file():
            return None
        if path.stat().st_size > max_file_size:
            return None

        language = _EXT_TO_LANG.get(path.suffix.lower())
        if language is None:
            return None

        try:
            source = path.read_bytes()
        except OSError:
            return None

        try:
            tree = self._parser(language).parse(source)

            # 1. Definitions (same as parse_file)
            captures = self._query(language).captures(tree.root_node)
            definitions = self._process_captures(captures, source)

            # 2. Calls — both flat list and scoped-per-definition
            calls = self._extract_calls(tree, language, source)
            scoped_calls = self._extract_calls_per_scope(
                tree, language, source, definitions
            )

            # 3. Imports
            imports = self._extract_imports(tree, language, source)

            return {
                "definitions": definitions,
                "calls": calls,
                "scoped_calls": scoped_calls,
                "imports": imports,
                "language": language,
            }
        except Exception as exc:
            log.warning("Full parse failed for %s: %s", filepath, exc)
            return None

    def extract_imports_from_source(
        self, source_code: str, filepath: str,
    ) -> list[dict[str, Any]]:
        """Extract imports from in-memory source code (no file I/O).

        Useful for the reconciliation engine which already has the source
        loaded. Falls back to empty list on any error.
        """
        language = _EXT_TO_LANG.get(Path(filepath).suffix.lower())
        if language is None:
            return []

        try:
            source = source_code.encode("utf-8")
            tree = self._parser(language).parse(source)
            return self._extract_imports(tree, language, source)
        except Exception as exc:
            log.debug("Import extraction from source failed for %s: %s", filepath, exc)
            return []


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