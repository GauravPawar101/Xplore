"""
Dependency Graph Builder — EzDocs

Builds a directed dependency graph of functions and classes within a codebase
using NetworkX and UniversalParser. Uses parallel file parsing when configured.
"""

import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import networkx as nx

from shared.parser import UniversalParser

log = logging.getLogger("ezdocs.graph")

# ─── Constants ────────────────────────────────────────────────────────────────

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules",
    "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "out", "target",
    # pip install locations — prevent analysing installed packages
    "site-packages", "dist-packages", "lib", "Lib",
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


def _parse_one_file(root_path: Path, file_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Parse one file in isolation (for parallel workers). Returns (relative_path_str, list of ParseResult)."""
    try:
        parser = UniversalParser()
        try:
            from shared.config import MAX_FILE_SIZE
            results = parser.parse_file(str(file_path), max_file_size=MAX_FILE_SIZE)
        except ImportError:
            results = parser.parse_file(str(file_path))
    except Exception as exc:
        log.warning("Parse error — %s: %s", file_path, exc)
        return (str(file_path.relative_to(root_path)), [])
    return (str(file_path.relative_to(root_path)), results)


# ─── GraphBuilder ─────────────────────────────────────────────────────────────

class GraphBuilder:
    """
    Builds a directed dependency graph from a directory of source files.

    Usage::

        builder = GraphBuilder("/path/to/repo")
        builder.build_graph(max_files=200)
        payload = builder.to_json()   # Ready for React Flow
    """

    def __init__(self, root_path: str) -> None:
        self.root_path: Path    = Path(root_path).resolve()
        self.parser:    UniversalParser = UniversalParser()
        self.graph:     nx.DiGraph      = nx.DiGraph()

    # ── Private helpers ────────────────────────────────────────────────────

    def _is_ignored(self, path: Path) -> bool:
        return any(part in IGNORED_DIRS for part in path.parts)

    def _collect_files(self, max_files: int) -> list[Path]:
        files: list[Path] = []
        for root, dirs, filenames in os.walk(self.root_path):
            root_p = Path(root)
            if self._is_ignored(root_p):
                dirs.clear()   # prune subtree — prevents descending
                continue

            # Prune ignored subdirs in-place so os.walk skips them
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

            for name in filenames:
                fp = root_p / name
                if self.parser.is_supported(str(fp)):
                    files.append(fp)
                    if len(files) >= max_files:
                        log.warning("File limit reached (%d). Truncating.", max_files)
                        return files
        return files

    def _process_file(self, file_path: Path) -> int:
        """Parse one file, add its nodes to the graph. Returns node count added."""
        relative = file_path.relative_to(self.root_path)
        try:
            try:
                from shared.config import MAX_FILE_SIZE
                results = self.parser.parse_file(str(file_path), max_file_size=MAX_FILE_SIZE)
            except ImportError:
                results = self.parser.parse_file(str(file_path))
        except Exception as exc:
            log.warning("Parse error — %s: %s", file_path, exc)
            return 0

        added = 0
        for item in results:
            node_id = f"{relative}::{item['name']}"
            if node_id in self.graph:
                continue
            self.graph.add_node(node_id, **item, filepath=str(relative))
            added += 1
        return added

    def _add_parsed_results(self, relative_path: str, results: list[dict]) -> int:
        """Add nodes from a parallel parse result. Returns count added."""
        added = 0
        for item in results:
            node_id = f"{relative_path}::{item['name']}"
            if node_id in self.graph:
                continue
            self.graph.add_node(node_id, **item, filepath=relative_path)
            added += 1
        return added

    def _create_edges(self) -> None:
        """
        Heuristic edge detection: for each node, tokenise its source code and
        check which other node names appear in those tokens.

        Two-pass approach:
          1. Build a name → [node_id] index for fast lookup.
          2. Scan each node's code and emit edges where names match.
        """
        # Index: name → list of node IDs that define that name
        name_index: dict[str, list[str]] = {}
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("name", "")
            if len(name) >= MIN_NAME_LEN and name not in _COMMON_TOKENS:
                name_index.setdefault(name, []).append(node_id)

        edge_count = 0
        for source_id, source_data in self.graph.nodes(data=True):
            code     = source_data.get("code", "")
            filepath = source_data.get("filepath", "")
            if not code:
                continue

            tokens = tokenise(code, filepath)
            for token in tokens:
                if token not in name_index:
                    continue
                for target_id in name_index[token]:
                    if target_id == source_id:
                        continue
                    if self.graph.has_edge(source_id, target_id):
                        continue

                    # Prefer INSTANTIATES when the target is a class
                    edge_type = (
                        "INSTANTIATES"
                        if self.graph.nodes[target_id].get("type") == "class"
                        else "CALLS"
                    )
                    self.graph.add_edge(source_id, target_id, type=edge_type)
                    edge_count += 1

        log.info("Created %d dependency edges.", edge_count)
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

    # ── Public API ─────────────────────────────────────────────────────────

    def build_graph(self, max_files: int = 200) -> nx.DiGraph:
        """
        Walk the root directory, parse source files, and build the graph.
        Uses parallel parsing when EZDOCS_PARSE_WORKERS is set (default: auto).
        """
        files = self._collect_files(max_files)
        log.info("Building graph from %d files in %s", len(files), self.root_path)

        try:
            from shared.config import PARSE_WORKERS
            workers = PARSE_WORKERS if PARSE_WORKERS > 0 else min(32, (os.cpu_count() or 1) + 4)
        except ImportError:
            workers = 1

        if workers <= 1:
            total_nodes = 0
            for i, fp in enumerate(files, 1):
                total_nodes += self._process_file(fp)
                if i % 50 == 0:
                    log.info("  …processed %d / %d files (%d nodes)", i, len(files), total_nodes)
        else:
            total_nodes = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_path = {
                    executor.submit(_parse_one_file, self.root_path, fp): fp
                    for fp in files
                }
                done = 0
                for future in as_completed(future_to_path):
                    rel_path, results = future.result()
                    total_nodes += self._add_parsed_results(rel_path, results)
                    done += 1
                    if done % 50 == 0:
                        log.info("  …processed %d / %d files (%d nodes)", done, len(files), total_nodes)

        log.info("Parsed %d code elements. Computing edges…", self.graph.number_of_nodes())
        self._create_edges()
        log.info(
            "Graph complete: %d nodes, %d edges.",
            self.graph.number_of_nodes(),
            self.graph.number_of_edges(),
        )
        return self.graph

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        """
        Serialise the graph for React Flow.

        Entry-point nodes (main, index, app, …) are positioned at y=0;
        everything else is laid out in a grid below them. The frontend
        Dagre layout will override these positions — they're just sensible
        fallbacks so an un-laid-out graph still looks reasonable.

        Returns:
            ``{"nodes": [...], "edges": [...]}``
        """
        entry_nodes:   list[dict] = []
        regular_nodes: list[dict] = []

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

            rf_node = {
                "id":   node_id,
                "type": "default",
                "data": {
                    "label":      name,
                    "type":       ntype,
                    "filepath":   filepath,
                    "start_line": data.get("start_line", 0),
                    "end_line":   data.get("end_line", 0),
                    "code":       code_display,
                },
                "position": {"x": 0, "y": 0},
            }

            name_lower = name.lower()
            file_lower = filepath.lower()
            is_entry   = any(kw in name_lower or kw in file_lower for kw in ENTRY_POINT_NAMES)

            (entry_nodes if is_entry else regular_nodes).append(rf_node)

        # ── Fallback positions (overridden by frontend Dagre) ──────────────
        _place_entry_row(entry_nodes, spacing=220)
        _place_grid(regular_nodes, offset_y=220 if entry_nodes else 0, col_spacing=190, row_spacing=130)

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

        return {"nodes": entry_nodes + regular_nodes, "edges": rf_edges}


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