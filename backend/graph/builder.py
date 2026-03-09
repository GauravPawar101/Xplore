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
    "site-packages", "dist-packages",
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


# ─── Local-import helpers ─────────────────────────────────────────────────────

def _extract_local_import_paths(code: str, filepath: str) -> list[str]:
    """Extract relative (local) import path strings from source code.

    Only returns paths that start with ``'.'`` – these reference files within
    the project rather than third-party packages or the standard library.

    * JS/TS  – ``import X from './path'``  /  ``require('./path')``
    * Python – ``from .module import X``   /  ``from ..pkg import Y``
    """
    ext = Path(filepath).suffix.lower()
    paths: list[str] = []
    if ext in (".js", ".ts", ".tsx", ".jsx"):
        # ES module syntax
        paths += re.findall(r'from\s+[\'"](\.[^\'"\s]+)[\'"]', code)
        # CommonJS
        paths += re.findall(r'require\s*\(\s*[\'"](\.[^\'"\s]+)[\'"]', code)
    elif ext == ".py":
        paths += [
            p
            for p in re.findall(r"from\s+(\.+\w*)\s+import", code, re.MULTILINE)
            if p.startswith(".")
        ]
    return paths


def _resolve_local_import(
    import_path: str,
    source_file: str,
    all_filepaths: frozenset[str],
) -> str | None:
    """Resolve a relative import path to an actual file path within the project.

    Handles multi-level ``..`` traversal correctly so that, for example,
    ``'../../models/user'`` imported from ``'src/api/index.js'`` resolves to
    ``'models/user.js'`` when that file exists in *all_filepaths*.

    Returns the normalised relative path string if a matching file exists,
    otherwise ``None``.
    """
    source_dir = str(Path(source_file).parent)
    # os.path.normpath resolves all '..' components correctly without requiring
    # an absolute path, giving us a clean relative path like 'models/user'.
    base = os.path.normpath(os.path.join(source_dir, import_path)).replace("\\", "/")
    # Strip leading './' if present (root-level files sit under '.')
    if base.startswith("./"):
        base = base[2:]
    ext = Path(source_file).suffix.lower()

    if ext in (".js", ".ts", ".tsx", ".jsx"):
        for cext in (".js", ".jsx", ".ts", ".tsx"):
            if f"{base}{cext}" in all_filepaths:
                return f"{base}{cext}"
            if f"{base}/index{cext}" in all_filepaths:
                return f"{base}/index{cext}"
    elif ext == ".py":
        # Use normpath for Python too, counting the number of leading dots to
        # determine how many directory levels to ascend.
        dot_count = len(import_path) - len(import_path.lstrip("."))
        parent = source_dir
        for _ in range(dot_count - 1):
            parent = str(Path(parent).parent)
        module = import_path.lstrip(".").replace(".", "/")
        # 'from . import x' has an empty module part – we cannot resolve the
        # target without parsing the import clause, so skip gracefully.
        if module:
            mod_base = os.path.normpath(os.path.join(parent, module)).replace("\\", "/")
            if mod_base.startswith("./"):
                mod_base = mod_base[2:]
            for candidate in (
                f"{mod_base}.py",
                f"{mod_base}/__init__.py",
            ):
                if candidate in all_filepaths:
                    return candidate
    return None


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
        # Collect root-level files and subdir files separately so that
        # root (global) files are always processed first regardless of
        # directory walk order.
        root_files: list[Path] = []
        subdir_files: list[Path] = []

        for root, dirs, filenames in os.walk(self.root_path):
            root_p = Path(root)
            if self._is_ignored(root_p):
                dirs.clear()   # prune subtree — prevents descending
                continue

            # Prune ignored subdirs in-place so os.walk skips them
            dirs[:] = sorted([
                d for d in dirs
                if d not in IGNORED_DIRS and not d.startswith(".")
            ])

            is_root_level = root_p == self.root_path
            for name in filenames:
                fp = root_p / name
                if self.parser.is_supported(str(fp)):
                    if is_root_level:
                        root_files.append(fp)
                    else:
                        subdir_files.append(fp)

        # Root-level (global) files first, then subdirectory files.
        combined = root_files + subdir_files
        if len(combined) > max_files:
            log.warning("File limit reached (%d). Truncating.", max_files)
            combined = combined[:max_files]
        return combined

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

    def _classify_node_layers(self) -> dict[str, int]:
        """Classify each unique filepath in the graph into a display layer.

        * **Layer 0** – root-level files: files whose relative path contains no
          directory separator (i.e. they live directly in the repository root).
        * **Layer 1** – files directly imported by layer-0 files via local /
          relative import statements (not third-party packages).
        * **Layer 2** – everything else.

        Returns a ``{filepath: layer}`` mapping.
        """
        all_filepaths: frozenset[str] = frozenset(
            data["filepath"]
            for _, data in self.graph.nodes(data=True)
            if data.get("filepath")
        )

        # Root-level files have no directory separator in their relative path
        root_files: set[str] = {
            fp for fp in all_filepaths
            if "/" not in fp and "\\" not in fp
        }

        # Discover local dependencies of root files by reading their source
        root_deps: set[str] = set()
        for fp in root_files:
            try:
                code = (self.root_path / fp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for imp_path in _extract_local_import_paths(code, fp):
                resolved = _resolve_local_import(imp_path, fp, all_filepaths)
                if resolved and resolved not in root_files:
                    root_deps.add(resolved)

        layer_map: dict[str, int] = {}
        for fp in all_filepaths:
            if fp in root_files:
                layer_map[fp] = 0
            elif fp in root_deps:
                layer_map[fp] = 1
            else:
                layer_map[fp] = 2

        log.info(
            "Layer classification — root: %d, direct-deps: %d, other: %d",
            len(root_files),
            len(root_deps),
            len(all_filepaths) - len(root_files) - len(root_deps),
        )
        return layer_map

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

        return {"nodes": root_nodes + dep1_nodes + regular_nodes, "edges": rf_edges}


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