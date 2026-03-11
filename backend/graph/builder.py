"""
Dependency Graph Builder — EzDocs

Builds a directed dependency graph of functions and classes within a codebase
using NetworkX and UniversalParser. Uses parallel file parsing when configured.
"""

import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import networkx as nx

from shared.parser import UniversalParser
from shared.request_control import raise_if_cancelled

log = logging.getLogger("ezdocs.graph")

# ─── Constants ────────────────────────────────────────────────────────────────

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    # JS/TS package managers
    "node_modules", "bower_components", "jspm_packages",
    # Python virtualenvs and install locations
    "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "site-packages", "dist-packages",
    # Build output
    "dist", "build", "out", "target",
    # Vendored / bundled 3rd-party code
    "vendor",           # PHP Composer, Go modules, Ruby bundler
    "third_party",      # C++/Bazel, Chromium-style repos
    "Pods",             # iOS CocoaPods
    "Carthage",         # iOS Carthage
    ".yarn",            # Yarn Berry cache
    ".npm",             # npm cache
    # EzDocs runtime — cached clones / uploads from other analyses
    "ingested_codebases",
    # Common caches and tool outputs
    ".cache", "coverage", ".next", ".nuxt", ".turbo", "tmp", "temp",
})

# Process files in fixed batches so very large codebases remain stable while
# still completing full analysis before returning graph output.
ANALYSIS_BATCH_SIZE = 200

IGNORED_FILE_SUFFIXES: tuple[str, ...] = (
    ".min.js", ".min.css", ".map", ".lock", ".pyc",
)

IGNORED_FILE_NAMES: frozenset[str] = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "uv.lock",
})

ENTRY_POINT_NAMES: frozenset[str] = frozenset({
    "main", "index", "app", "run", "start", "init", "setup",
})

# Minimum name length to consider as a meaningful dependency token.
# Avoids edges to common short names like "i", "n", "ok".
MIN_NAME_LEN = 4

# Very common names that appear in almost every file and produce masses of
# false-positive edges if matched as dependencies.
_COMMON_TOKENS: frozenset[str] = frozenset({
    # Python builtins
    "self", "cls", "args", "kwargs", "None", "True", "False",
    "print", "range", "list", "dict", "tuple", "set", "type",
    "open", "str", "int", "float", "bool", "len", "super",
    "return", "yield", "raise", "pass", "break", "continue",
    "import", "from", "class", "def", "async", "await",
    "with", "for", "while", "if", "else", "elif", "try",
    "except", "finally", "lambda", "and", "not", "or", "in",
    "isinstance", "hasattr", "getattr", "setattr", "property",
    # JS/TS builtins
    "const", "let", "var", "function", "return", "this",
    "export", "default", "require", "module", "console",
    "Promise", "async", "await", "typeof", "undefined", "null",
    # Java builtins
    "void", "null", "this", "super", "public", "private",
    "static", "final", "class", "interface", "extends",
    # Generic noise
    "data", "result", "error", "value", "name", "path",
    "item", "node", "edge", "text", "code", "file", "line",
})

# ─── Language Tokenisers ──────────────────────────────────────────────────────

def _tokens_python(code: str) -> set[str]:
    func_calls = re.findall(r"\b([a-zA-Z_]\w*)\s*\(", code)
    # Explicit class instantiations (UpperCamelCase before parenthesis)
    class_insts = re.findall(r"\b([A-Z][a-zA-Z0-9_]*)\s*\(", code)
    # `from x import y` and `import y`
    raw_imports = re.findall(
        r"(?:from\s+\S+\s+import\s+([\w,\s]+)|^\s*import\s+([\w,\s]+))",
        code,
        re.MULTILINE,
    )
    imports = [
        name.strip()
        for pair in raw_imports
        for chunk in pair
        for name in chunk.split(",")
        if name.strip()
    ]
    return set(func_calls + class_insts + imports)


def _tokens_js_ts(code: str) -> set[str]:
    func_calls  = re.findall(r"\b([a-zA-Z_]\w*)\s*\(", code)
    class_insts = re.findall(r"\bnew\s+([A-Z][a-zA-Z0-9_]*)\s*\(", code)
    # Named imports: import { Foo, Bar } from '...'
    named = re.findall(r"import\s*\{([^}]+)\}\s*from", code)
    named_imports = [
        n.strip().split(" as ")[0].strip()
        for chunk in named
        for n in chunk.split(",")
        if n.strip()
    ]
    # Default imports: import Foo from '...'
    default_imports = re.findall(r"import\s+([A-Za-z_]\w*)\s+from", code)
    return set(func_calls + class_insts + named_imports + default_imports)


def _tokens_java(code: str) -> set[str]:
    func_calls  = re.findall(r"\b([a-zA-Z_]\w*)\s*\(", code)
    class_insts = re.findall(r"\bnew\s+([A-Z][a-zA-Z0-9_]*)\s*[<(]", code)
    raw_imports = re.findall(r"import\s+(?:static\s+)?([a-zA-Z0-9_.]+)\s*;", code)
    imports = [imp.rsplit(".", 1)[-1] for imp in raw_imports]
    return set(func_calls + class_insts + imports)


def _tokens_generic(code: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z_]\w*\b", code))


_TOKENISERS: dict[str, Any] = {
    ".py":   _tokens_python,
    ".js":   _tokens_js_ts,
    ".ts":   _tokens_js_ts,
    ".tsx":  _tokens_js_ts,
    ".jsx":  _tokens_js_ts,
    ".java": _tokens_java,
}


def tokenise(code: str, filepath: str) -> set[str]:
    ext = Path(filepath).suffix.lower()
    fn  = _TOKENISERS.get(ext, _tokens_generic)
    return {t for t in fn(code) if len(t) >= MIN_NAME_LEN and t not in _COMMON_TOKENS}


# Local-import helpers have been moved to graph/reconciliation.py.
# GraphBuilder now delegates layer classification entirely to ReconciliationEngine.


import threading

_thread_local = threading.local()


def _parse_one_file(root_path: Path, file_path: Path) -> tuple[str, dict[str, Any] | list[dict[str, Any]]]:
    """Parse one file in isolation (for parallel workers).

    Uses a thread-local parser so tree-sitter's native C library is only
    initialised once per thread, avoiding race conditions.

    Returns (relative_path, full_result_or_definitions).
    When parse_file_full succeeds the second element is a FullParseResult dict;
    otherwise falls back to the basic definitions list for compatibility.
    """
    rel = str(file_path.relative_to(root_path)).replace("\\", "/")
    if not hasattr(_thread_local, "parser"):
        _thread_local.parser = UniversalParser()

    try:
        from shared.config import MAX_FILE_SIZE
        max_fs = MAX_FILE_SIZE
    except ImportError:
        max_fs = 1024 * 1024

    try:
        # Prefer full AST parse (definitions + calls + imports)
        full = _thread_local.parser.parse_file_full(str(file_path), max_file_size=max_fs)
        if full is not None:
            return (rel, full)
        # Fallback to basic parse
        results = _thread_local.parser.parse_file(str(file_path), max_file_size=max_fs)
        return (rel, results)
    except Exception as exc:
        log.warning("Parse error — %s: %s", file_path, exc)
        return (rel, [])


# ─── GraphBuilder ─────────────────────────────────────────────────────────────

class GraphBuilder:
    """
    Builds a directed dependency graph from a directory of source files.

    Usage::

        builder = GraphBuilder("/path/to/repo")
        builder.build_graph(max_files=200)
        payload = builder.to_json()   # Ready for React Flow
    """

    def __init__(self, root_path: str, request_id: str | None = None) -> None:
        self.root_path: Path    = Path(root_path).resolve()
        self.parser:    UniversalParser = UniversalParser()
        self.graph:     nx.DiGraph      = nx.DiGraph()
        self.request_id = request_id
        self._collected_filepaths: set[str] = set()
        # AST-extracted call data: node_id → list of called names
        self._ast_calls: dict[str, list[str]] = {}
        # AST-extracted import data: filepath → list of import dicts
        self._ast_imports: dict[str, list[dict[str, Any]]] = {}

    def _add_file_placeholder(self, file_path: Path, relative_path: str | None = None) -> int:
        fp_str = relative_path or str(file_path.relative_to(self.root_path)).replace("\\", "/")
        node_id = f"{fp_str}::__file__"
        if node_id in self.graph:
            return 0

        try:
            raw = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""

        try:
            from shared.config import MAX_CODE_DISPLAY_LENGTH
            code_display = raw[:MAX_CODE_DISPLAY_LENGTH]
        except ImportError:
            code_display = raw[:4000]

        end_line = max(1, raw.count("\n") + 1) if raw else 1
        self.graph.add_node(
            node_id,
            name=Path(fp_str).name,
            type="file",
            filepath=fp_str,
            start_line=1,
            end_line=end_line,
            code=code_display,
            is_placeholder=True,
        )
        return 1

    # ── Private helpers ────────────────────────────────────────────────────

    def _is_ignored(self, path: Path) -> bool:
        """Check only relative path parts so absolute parent dirs don't
        accidentally match IGNORED_DIRS names (e.g. a project living inside
        a directory called 'build' or 'env' would otherwise be silently skipped)."""
        try:
            rel = path.relative_to(self.root_path)
        except ValueError:
            return False
        return any(part in IGNORED_DIRS for part in rel.parts)

    def _should_skip_file(self, file_path: Path, max_file_size: int) -> bool:
        name = file_path.name
        lname = name.lower()
        if name in IGNORED_FILE_NAMES:
            return True
        if any(lname.endswith(sfx) for sfx in IGNORED_FILE_SUFFIXES):
            return True
        try:
            if file_path.stat().st_size > max_file_size:
                return True
        except OSError:
            return True
        return False

    def _collect_files(self, max_files: int | None) -> list[Path]:
        """Breadth-first file collection.

        Processes ALL files at depth 0 (root), then ALL files at depth 1
        across every subfolder, then depth 2, etc.  This guarantees that the
        user's own top-level source files are always collected before the
        engine descends into any deeply nested directory.
        """
        files: list[Path] = []
        effective_limit = None if max_files is None or max_files <= 0 else max_files
        try:
            from shared.config import MAX_FILE_SIZE
            max_file_size = MAX_FILE_SIZE
        except ImportError:
            max_file_size = 1024 * 1024
        # BFS queue — start with the project root
        queue: list[Path] = [self.root_path]

        while queue and (effective_limit is None or len(files) < effective_limit):
            next_queue: list[Path] = []
            for dir_path in queue:
                raise_if_cancelled(self.request_id)
                if self._is_ignored(dir_path):
                    continue
                try:
                    entries = sorted(
                        dir_path.iterdir(),
                        key=lambda e: (e.is_dir(), e.name.lower()),
                    )
                except PermissionError:
                    continue

                for entry in entries:
                    if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                        continue
                    if entry.is_dir():
                        next_queue.append(entry)
                    elif entry.is_file() and self.parser.is_supported(str(entry)):
                        if self._should_skip_file(entry, max_file_size):
                            continue
                        files.append(entry)
                        if effective_limit is not None and len(files) >= effective_limit:
                            log.warning("File limit reached (%d). Truncating.", effective_limit)
                            return files

            queue = sorted(next_queue, key=lambda d: d.name.lower())

        return files

    def _process_file(self, file_path: Path) -> int:
        """Parse one file, add its nodes to the graph. Returns node count added."""
        relative = file_path.relative_to(self.root_path)
        fp_str = str(relative).replace("\\", "/")

        try:
            try:
                from shared.config import MAX_FILE_SIZE
                max_fs = MAX_FILE_SIZE
            except ImportError:
                max_fs = 1024 * 1024

            # Prefer full AST parse (definitions + calls + imports)
            full_result = self.parser.parse_file_full(str(file_path), max_file_size=max_fs)

            if full_result is not None:
                results = full_result["definitions"]
                scoped_calls = full_result.get("scoped_calls", {})
                ast_imports = full_result.get("imports", [])

                if ast_imports:
                    self._ast_imports[fp_str] = ast_imports

                if not results:
                    return self._add_file_placeholder(file_path, fp_str)

                added = 0
                for item in results:
                    node_id = f"{fp_str}::{item['name']}"
                    if node_id in self.graph:
                        continue
                    self.graph.add_node(node_id, **item, filepath=fp_str)
                    added += 1
                    # Store scoped calls for this node
                    if item["name"] in scoped_calls:
                        self._ast_calls[node_id] = scoped_calls[item["name"]]

                # Store module-level calls under a pseudo-node
                if "__module__" in scoped_calls:
                    file_node = f"{fp_str}::__file__"
                    if file_node in self.graph:
                        self._ast_calls[file_node] = scoped_calls["__module__"]
                return added

            # Fallback to basic parse
            results = self.parser.parse_file(str(file_path), max_file_size=max_fs)
        except Exception as exc:
            log.warning("Parse error — %s: %s", file_path, exc)
            return self._add_file_placeholder(file_path)

        if not results:
            return self._add_file_placeholder(file_path, fp_str)

        added = 0
        for item in results:
            node_id = f"{fp_str}::{item['name']}"
            if node_id in self.graph:
                continue
            self.graph.add_node(node_id, **item, filepath=fp_str)
            added += 1
        return added

    def _add_parsed_results(self, relative_path: str, results: dict | list) -> int:
        """Add nodes from a parallel parse result. Returns count added.

        ``results`` may be a FullParseResult dict (from parse_file_full) or
        a plain list of definitions (legacy fallback).
        """
        fp = relative_path.replace("\\", "/")

        # Handle full AST result
        if isinstance(results, dict) and "definitions" in results:
            definitions = results["definitions"]
            scoped_calls = results.get("scoped_calls", {})
            ast_imports = results.get("imports", [])

            if ast_imports:
                self._ast_imports[fp] = ast_imports

            if not definitions:
                return self._add_file_placeholder(self.root_path / fp, fp)

            added = 0
            for item in definitions:
                node_id = f"{fp}::{item['name']}"
                if node_id in self.graph:
                    continue
                self.graph.add_node(node_id, **item, filepath=fp)
                added += 1
                if item["name"] in scoped_calls:
                    self._ast_calls[node_id] = scoped_calls[item["name"]]

            if "__module__" in scoped_calls:
                file_node = f"{fp}::__file__"
                if file_node in self.graph:
                    self._ast_calls[file_node] = scoped_calls["__module__"]
            return added

        # Legacy: plain list of definitions
        if not results:
            return self._add_file_placeholder(self.root_path / fp, fp)
        added = 0
        for item in results:
            node_id = f"{fp}::{item['name']}"
            if node_id in self.graph:
                continue
            self.graph.add_node(node_id, **item, filepath=fp)
            added += 1
        return added

    def _create_edges(self) -> None:
        """
        Hybrid AST + heuristic edge detection.

        **Phase 1 (AST-precise):** For every node that has scoped call data
        from tree-sitter, look up each called name in the symbol index. This
        produces high-confidence edges because tree-sitter only captures
        actual call expressions — not names in strings, comments, or type
        annotations.

        **Phase 2 (regex fallback):** For nodes without AST call data (e.g.
        languages where the call query failed), fall back to the original
        regex tokenisation approach.

        This two-phase strategy ensures correctness improves for all supported
        languages while maintaining backward compatibility.
        """
        # Index: name → list of node IDs that define that name
        name_index: dict[str, list[str]] = {}
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("name", "")
            if len(name) >= MIN_NAME_LEN and name not in _COMMON_TOKENS:
                name_index.setdefault(name, []).append(node_id)

        edge_count = 0
        ast_resolved = 0
        regex_resolved = 0

        for source_id, source_data in self.graph.nodes(data=True):
            code     = source_data.get("code", "")
            filepath = source_data.get("filepath", "")
            if not code:
                continue

            # Phase 1: AST-based edges (preferred)
            if source_id in self._ast_calls:
                call_names = self._ast_calls[source_id]
                seen_targets: set[str] = set()
                for call_name in call_names:
                    if call_name not in name_index:
                        continue
                    if len(call_name) < MIN_NAME_LEN or call_name in _COMMON_TOKENS:
                        continue
                    for target_id in name_index[call_name]:
                        if target_id == source_id:
                            continue
                        if target_id in seen_targets:
                            continue
                        seen_targets.add(target_id)
                        if self.graph.has_edge(source_id, target_id):
                            continue

                        edge_type = (
                            "INSTANTIATES"
                            if self.graph.nodes[target_id].get("type") == "class"
                            else "CALLS"
                        )
                        self.graph.add_edge(source_id, target_id, type=edge_type)
                        edge_count += 1
                        ast_resolved += 1
                continue  # Skip regex for this node — AST data is authoritative

            # Phase 2: Regex fallback for nodes without AST call data
            tokens = tokenise(code, filepath)
            for token in tokens:
                if token not in name_index:
                    continue
                for target_id in name_index[token]:
                    if target_id == source_id:
                        continue
                    if self.graph.has_edge(source_id, target_id):
                        continue

                    edge_type = (
                        "INSTANTIATES"
                        if self.graph.nodes[target_id].get("type") == "class"
                        else "CALLS"
                    )
                    self.graph.add_edge(source_id, target_id, type=edge_type)
                    edge_count += 1
                    regex_resolved += 1

        log.info(
            "Created %d dependency edges (%d AST-precise, %d regex-fallback).",
            edge_count, ast_resolved, regex_resolved,
        )
        self._truncate_node_code_for_memory()

    def _truncate_node_code_for_memory(self) -> None:
        """
        After edges are built, replace full code with display-length truncation in the graph
        to reduce memory. Full code was only needed for tokenise() during _create_edges.
        """
        try:
            from shared.config import MAX_CODE_DISPLAY_LENGTH
            max_len = MAX_CODE_DISPLAY_LENGTH
        except ImportError:
            max_len = 4000
        for _nid, data in self.graph.nodes(data=True):
            code = data.get("code", "")
            if len(code) > max_len:
                self.graph.nodes[_nid]["code"] = code[:max_len]

    def _run_reconciliation(self) -> "ReconciliationSurface":  # noqa: F821
        """Run the ReconciliationEngine over the current graph's file set.

        Builds the entry-surface layer map (Layer 0 / 1 / 2) by delegating to
        :class:`~graph.reconciliation.ReconciliationEngine`.  The result is
        cached on ``self._reconciliation`` so it is computed only once per
        ``build_graph()`` call.

        Passes AST-extracted imports when available for higher accuracy.
        """
        from graph.reconciliation import ReconciliationEngine  # local import avoids circular refs

        all_filepaths: frozenset[str] = frozenset(self._collected_filepaths)
        engine = ReconciliationEngine(
            str(self.root_path), all_filepaths,
            ast_imports=self._ast_imports if self._ast_imports else None,
        )
        surface = engine.build_surface()
        self._reconciliation = surface
        return surface

    def _classify_node_layers(self) -> dict[str, int]:
        """Return the {filepath: 0|1|2} layer map via ReconciliationEngine.

        * **Layer 0** – root-level files (live directly in the repo root).
        * **Layer 1** – local project files those root files directly import
          (relative *and* bare-path imports), excluding third-party packages.
        * **Layer 2** – everything else (models, views, controllers internals …).
        """
        if hasattr(self, "_reconciliation"):
            return self._reconciliation.layer_map
        return self._run_reconciliation().layer_map

    # ── Public API ─────────────────────────────────────────────────────────

    def build_graph(self, max_files: int = 0) -> nx.DiGraph:
        """
        Walk the root directory, parse source files, and build the graph.
        Sequential by default; set EZDOCS_PARSE_WORKERS > 1 for parallel.
        """
        files = self._collect_files(max_files)
        self._collected_filepaths = {
            str(fp.relative_to(self.root_path)).replace("\\", "/")
            for fp in files
        }
        log.info("Building graph from %d files in %s", len(files), self.root_path)

        try:
            from shared.config import PARSE_WORKERS
            workers = PARSE_WORKERS
        except ImportError:
            workers = 1

        parsed_ok = 0
        if workers <= 1:
            total_nodes = 0
            total_batches = (len(files) + ANALYSIS_BATCH_SIZE - 1) // ANALYSIS_BATCH_SIZE
            for batch_idx in range(total_batches):
                raise_if_cancelled(self.request_id)
                start = batch_idx * ANALYSIS_BATCH_SIZE
                end = min(start + ANALYSIS_BATCH_SIZE, len(files))
                batch = files[start:end]
                for fp in batch:
                    raise_if_cancelled(self.request_id)
                    added = self._process_file(fp)
                    total_nodes += added
                    if added:
                        parsed_ok += 1
                log.info(
                    "  …processed batch %d/%d (%d/%d files, %d nodes)",
                    batch_idx + 1,
                    total_batches,
                    end,
                    len(files),
                    total_nodes,
                )
        else:
            total_nodes = 0
            total_batches = (len(files) + ANALYSIS_BATCH_SIZE - 1) // ANALYSIS_BATCH_SIZE
            with ThreadPoolExecutor(max_workers=workers) as executor:
                done = 0
                for batch_idx in range(total_batches):
                    raise_if_cancelled(self.request_id)
                    start = batch_idx * ANALYSIS_BATCH_SIZE
                    end = min(start + ANALYSIS_BATCH_SIZE, len(files))
                    batch = files[start:end]
                    future_to_path = {
                        executor.submit(_parse_one_file, self.root_path, fp): fp
                        for fp in batch
                    }
                    for future in as_completed(future_to_path):
                        raise_if_cancelled(self.request_id)
                        rel_path, results = future.result()
                        added = self._add_parsed_results(rel_path, results)
                        total_nodes += added
                        if added:
                            parsed_ok += 1
                        done += 1
                    log.info(
                        "  …processed batch %d/%d (%d/%d files, %d nodes)",
                        batch_idx + 1,
                        total_batches,
                        done,
                        len(files),
                        total_nodes,
                    )

        failed = len(files) - parsed_ok
        if failed:
            log.warning("Parse results: %d files OK, %d files yielded 0 symbols.", parsed_ok, failed)
        log.info("Parsed %d code elements. Computing edges…", self.graph.number_of_nodes())
        raise_if_cancelled(self.request_id)
        self._create_edges()
        # Run reconciliation immediately so the layer map and surface data are
        # cached before to_json() or any caller inspects the graph.
        self._run_reconciliation()
        log.info(
            "Graph complete: %d nodes, %d edges.",
            self.graph.number_of_nodes(),
            self.graph.number_of_edges(),
        )
        return self.graph

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        """
        Serialise the graph for React Flow.

        Nodes are ordered and positioned in three layers:

        * **Layer 0** – root-level files (files directly in the repo root).
          Displayed in a horizontal row at y=0.
        * **Layer 1** – files directly imported by root files via local imports
          (not third-party packages). Displayed in a grid below layer 0.
        * **Layer 2+** – everything else. Displayed in a grid below layer 1.

        When there are no root-level files the layout falls back to the
        original keyword-based entry-point detection (main, index, app, …).

        The frontend Dagre layout will override these positions – they are just
        sensible fallbacks so an un-laid-out graph still looks reasonable.

        Returns:
            ``{"nodes": [...], "edges": [...]}``
        """
        layer_map = self._classify_node_layers()
        has_root_files = any(lyr == 0 for lyr in layer_map.values())

        primary_entry_file: str | None = None
        root_files: list[str] = []
        if hasattr(self, "_reconciliation"):
            root_files = list(self._reconciliation.root_files)

        if root_files:
            preferred_order = (
                "main", "index", "app", "server", "cli", "run", "start", "init", "setup"
            )

            def _rank(fp: str) -> tuple[int, int, str]:
                stem = Path(fp).stem.lower()
                try:
                    name_rank = preferred_order.index(stem)
                except ValueError:
                    name_rank = 999
                # Prefer python/ts/js entry files when names tie
                ext_rank = {
                    ".py": 0,
                    ".ts": 1,
                    ".tsx": 2,
                    ".js": 3,
                    ".jsx": 4,
                }.get(Path(fp).suffix.lower(), 10)
                return (name_rank, ext_rank, fp)

            primary_entry_file = min(root_files, key=_rank)

        root_nodes:    list[dict] = []   # layer 0 – files in the repo root
        dep1_nodes:    list[dict] = []   # layer 1 – direct local deps of root files
        regular_nodes: list[dict] = []   # layer 2+ – everything else

        for node_id, data in self.graph.nodes(data=True):
            # Guard against nodes with missing required fields
            name     = data.get("name", node_id)
            ntype    = data.get("type", "function")
            filepath = data.get("filepath", "")

            code_full = data.get("code", "")
            try:
                from shared.config import MAX_CODE_DISPLAY_LENGTH
                code_display = code_full[:MAX_CODE_DISPLAY_LENGTH] if len(code_full) > MAX_CODE_DISPLAY_LENGTH else code_full
            except ImportError:
                code_display = code_full

            layer        = layer_map.get(filepath, 2)
            is_root_file = layer == 0
            is_root_dep  = layer == 1

            name_lower = name.lower()
            file_lower = filepath.lower()
            # Root-level files are always treated as entry points; keyword
            # matching is kept as a fallback for repos where code lives only
            # inside subdirectories.
            is_entry = bool(primary_entry_file and filepath == primary_entry_file)
            if not primary_entry_file:
                is_entry = is_root_file or any(
                    kw in name_lower or kw in file_lower for kw in ENTRY_POINT_NAMES
                )

            rf_node = {
                "id":   node_id,
                "type": "default",
                "data": {
                    "label":        name,
                    "type":         ntype,
                    "filepath":     filepath,
                    "start_line":   data.get("start_line", 0),
                    "end_line":     data.get("end_line", 0),
                    "code":         code_display,
                    "isEntry":      is_entry,
                    "is_root_file": is_root_file,
                    "is_root_dep":  is_root_dep,
                    "layer":        layer,
                },
                "position": {"x": 0, "y": 0},
            }

            if is_root_file:
                root_nodes.append(rf_node)
            elif is_root_dep:
                dep1_nodes.append(rf_node)
            elif not has_root_files and is_entry:
                # Keyword-based fallback: no root files detected, so use the
                # conventional entry-point heuristic.
                root_nodes.append(rf_node)
            else:
                regular_nodes.append(rf_node)

        # ── Fallback positions (overridden by frontend Dagre) ──────────────
        _place_entry_row(root_nodes, spacing=220)

        dep1_y = 220 if root_nodes else 0
        _place_grid(dep1_nodes, offset_y=dep1_y, col_spacing=190, row_spacing=130)

        if dep1_nodes:
            dep1_cols = max(5, min(15, int(len(dep1_nodes) ** 0.5)))
            dep1_rows = (len(dep1_nodes) + dep1_cols - 1) // dep1_cols
            regular_y = dep1_y + dep1_rows * 130 + 100
        else:
            regular_y = dep1_y + (220 if root_nodes else 0)
        _place_grid(regular_nodes, offset_y=regular_y, col_spacing=190, row_spacing=130)

        # ── Edges ──────────────────────────────────────────────────────────
        rf_edges: list[dict] = []
        for source, target, edata in self.graph.edges(data=True):
            rf_edges.append({
                "id":       f"e-{source}-{target}",
                "source":   source,
                "target":   target,
                "type":     "default",
                "label":    edata.get("type", "CALLS"),
                "animated": True,
                "style":    {"strokeWidth": 2, "strokeOpacity": 0.7},
            })

        return {"nodes": root_nodes + dep1_nodes + regular_nodes, "edges": rf_edges, "reconciliation": self._reconciliation.to_api_dict() if hasattr(self, "_reconciliation") else None}


# ─── Layout helpers (pure functions) ─────────────────────────────────────────

def _place_entry_row(nodes: list[dict], spacing: int = 220) -> None:
    if not nodes:
        return
    total_width = (len(nodes) - 1) * spacing
    for i, node in enumerate(nodes):
        node["position"] = {"x": i * spacing - total_width / 2, "y": 0}


def _place_grid(
    nodes: list[dict],
    offset_y: int = 0,
    col_spacing: int = 190,
    row_spacing: int = 130,
) -> None:
    if not nodes:
        return
    cols = max(5, min(15, int(len(nodes) ** 0.5)))
    half = (cols - 1) * col_spacing / 2
    for i, node in enumerate(nodes):
        col = i % cols
        row = i // cols
        node["position"] = {
            "x": col * col_spacing - half,
            "y": offset_y + row * row_spacing,
        }


# ─── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    builder = GraphBuilder(root)
    builder.build_graph()
    print(json.dumps(builder.to_json(), indent=2))