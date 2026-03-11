"""
Reconciliation Engine — EzDocs

Surfaces the 'entry layer' of a codebase before any full analysis descends
into subdirectories.

Given a repo like:

    index.js          ← root file
    middleware.js     ← root file
    models/           ← subdirectory
    views/            ← subdirectory
    controllers/      ← subdirectory

The engine builds a three-layer map:

  Layer 0  – files living directly in the repo root (index.js, middleware.js …)
  Layer 1  – local project files those root files directly import/require,
             filtering out every 3rd-party package (npm / PyPI / stdlib)
  Layer 2  – everything else (models, views, controllers internals …)

Import detection uses **AST-based extraction** (tree-sitter) when available,
falling back to regex for source that wasn't parsed by the graph builder.

  * JS / TS / JSX / TSX – ESM (import … from), CommonJS (require()), dynamic
    import(), re-exports (export … from) — both relative ('./routes') and
    project-local bare paths ('routes', 'models/user').
  * Python – relative (from .module import …) and absolute bare imports
    (from models.user import …, import config) resolved against the project
    file tree.

Third-party packages are identified purely by file-existence: if the import
path does not resolve to any file inside the project directory it is flagged
as unresolved / third-party and excluded from Layer 1.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("ezdocs.reconciliation")


# ─── Import extraction ────────────────────────────────────────────────────────

def _extract_js_ts_imports(code: str) -> tuple[list[str], list[str]]:
    """Return (relative_paths, bare_paths) from JS/TS source."""
    all_paths: list[str] = []

    # ESM: import … from 'path'  (named, default, namespace, side-effect)
    all_paths += re.findall(
        r'(?:^|[\s;{])import\b(?:[^;(]|(?!\s*\())*?\bfrom\s+[\'"]([^\'"\s]+)[\'"]',
        code, re.MULTILINE,
    )
    # CommonJS: require('path')
    all_paths += re.findall(r'require\s*\(\s*[\'"]([^\'"\s]+)[\'"]', code)
    # Dynamic: import('path')
    all_paths += re.findall(r'\bimport\s*\(\s*[\'"]([^\'"\s]+)[\'"]', code)
    # Re-export: export { X } from 'path'  /  export * from 'path'
    all_paths += re.findall(
        r'\bexport\b(?:[^;])*?\bfrom\s+[\'"]([^\'"\s]+)[\'"]', code
    )

    relative: list[str] = []
    bare: list[str] = []
    for p in all_paths:
        if p.startswith('.'):
            relative.append(p)
        elif not p.startswith('/'):       # skip FS-absolute paths
            bare.append(p)
    return relative, bare


def _extract_python_imports(code: str) -> tuple[list[str], list[str]]:
    """Return (relative_paths, bare_module_names) from Python source."""
    relative: list[str] = []
    bare: list[str] = []

    # from .module import X  /  from ..pkg import Y
    for m in re.finditer(r'^[ \t]*from\s+(\S+)\s+import', code, re.MULTILINE):
        path = m.group(1)
        if path.startswith('.'):
            relative.append(path)
        else:
            bare.append(path)

    # import x  /  import x, y  /  import x as Z
    for m in re.finditer(r'^[ \t]*import\s+(.+?)(?:#.*)?$', code, re.MULTILINE):
        for chunk in m.group(1).split(','):
            name = chunk.strip().split()[0]   # handles 'import x as y'
            if name and not name.startswith('.') and name.isidentifier():
                bare.append(name)

    return relative, bare


def extract_imports(code: str, filepath: str) -> tuple[list[str], list[str]]:
    """Extract (relative_imports, bare_imports) from any supported source file."""
    ext = Path(filepath).suffix.lower()
    if ext in ('.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs'):
        return _extract_js_ts_imports(code)
    if ext == '.py':
        return _extract_python_imports(code)
    return [], []


# ─── Import resolution ────────────────────────────────────────────────────────

_JS_EXTS = ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs')


def _resolve_js_ts_relative(
    import_path: str,
    source_file: str,
    all_filepaths: frozenset[str],
) -> str | None:
    source_dir = str(Path(source_file).parent)
    base = os.path.normpath(os.path.join(source_dir, import_path)).replace('\\', '/')
    if base.startswith('./'):
        base = base[2:]
    for cext in _JS_EXTS:
        if f'{base}{cext}' in all_filepaths:
            return f'{base}{cext}'
        if f'{base}/index{cext}' in all_filepaths:
            return f'{base}/index{cext}'
    return None


def _resolve_python_relative(
    import_path: str,
    source_file: str,
    all_filepaths: frozenset[str],
) -> str | None:
    source_dir = str(Path(source_file).parent)
    dot_count = len(import_path) - len(import_path.lstrip('.'))
    parent = source_dir
    for _ in range(dot_count - 1):
        parent = str(Path(parent).parent)
    module = import_path.lstrip('.').replace('.', '/')
    if not module:
        return None
    mod_base = os.path.normpath(os.path.join(parent, module)).replace('\\', '/')
    if mod_base.startswith('./'):
        mod_base = mod_base[2:]  # BUG FIX: was missing this assignment
    for candidate in (f'{mod_base}.py', f'{mod_base}/__init__.py'):
        if candidate in all_filepaths:
            return candidate
    return None


def _resolve_js_ts_bare(
    import_path: str,
    all_filepaths: frozenset[str],
) -> str | None:
    """
    Resolve a bare JS/TS import ('routes', 'models/user') to a project file.

    Scoped packages like '@babel/core' or '@org/lib' are skipped unless they
    look like a local workspace path (more than one slash after the scope).
    Single-word lowercase names that don't match any project file are 3rd party.
    """
    # Pure scoped npm packages: @scope/package  (exactly one slash after @)
    if import_path.startswith('@'):
        parts = import_path.lstrip('@').split('/')
        if len(parts) <= 1:
            return None          # @scope only — definitely 3rd party
        # @scope/package — check once as a possible monorepo local package
        path = '/'.join(parts[1:])
    else:
        path = import_path

    for cext in _JS_EXTS:
        if f'{path}{cext}' in all_filepaths:
            return f'{path}{cext}'
        if f'{path}/index{cext}' in all_filepaths:
            return f'{path}/index{cext}'
    return None

def _resolve_python_bare(
    import_path: str,
    all_filepaths: frozenset[str],
) -> str | None:
    """
    Resolve a bare Python import ('models.user', 'config') to a project file.

    Converts dotted names to file paths and checks for both module.py and
    package/__init__.py variants.  Names that have no matching file are stdlib
    or third-party packages.
    """
    # Try the full dotted path AND each progressive prefix (handles
    # 'from models.user import User' where models/user.py is the target).
    segments = import_path.split('.')
    for depth in range(len(segments), 0, -1):
        mod_path = '/'.join(segments[:depth])
        for candidate in (f'{mod_path}.py', f'{mod_path}/__init__.py'):
            if candidate in all_filepaths:
                return candidate
    return None


def resolve_import(
    import_path: str,
    source_file: str,
    all_filepaths: frozenset[str],
    is_relative: bool,
) -> str | None:
    """Resolve any import string to a project-local filepath, or None."""
    ext = Path(source_file).suffix.lower()
    if is_relative:
        if ext in ('.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs'):
            return _resolve_js_ts_relative(import_path, source_file, all_filepaths)
        if ext == '.py':
            return _resolve_python_relative(import_path, source_file, all_filepaths)
    else:
        if ext in ('.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs'):
            return _resolve_js_ts_bare(import_path, all_filepaths)
        if ext == '.py':
            return _resolve_python_bare(import_path, all_filepaths)
    return None


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ReconciliationSurface:
    """
    Structured result of reconciliation analysis.

    Attributes:
        root_files    Files living directly in the repo root (Layer 0).
        direct_deps   Mapping of root_file → sorted list of local project
                      filepaths it directly imports (Layer 1).
        unresolved    Mapping of root_file → import strings that could not be
                      resolved to a project file (third-party / stdlib).
        layer_map     Complete {filepath: 0|1|2} mapping for every file in the
                      project.  Passed directly to GraphBuilder.to_json().
    """
    root_files: list[str]
    direct_deps: dict[str, list[str]]
    unresolved: dict[str, list[str]]
    layer_map: dict[str, int]

    # ── Convenience accessors ─────────────────────────────────────────────

    @property
    def all_layer1_files(self) -> set[str]:
        """Set of all Layer-1 filepaths (union across every root file)."""
        return {fp for deps in self.direct_deps.values() for fp in deps}

    def deps_for(self, root_file: str) -> list[str]:
        """Direct local deps of a specific root file (empty list if unknown)."""
        return self.direct_deps.get(root_file, [])

    def summary(self) -> str:
        l0 = sum(1 for v in self.layer_map.values() if v == 0)
        l1 = sum(1 for v in self.layer_map.values() if v == 1)
        l2 = sum(1 for v in self.layer_map.values() if v == 2)
        lines = [
            f'Layer 0 ({l0} files): {", ".join(self.root_files) or "-"}',
            f'Layer 1 ({l1} files, direct local deps):',
        ]
        for rf, deps in self.direct_deps.items():
            if deps:
                lines.append(f'  {rf} -> {", ".join(deps)}')
        lines.append(f'Layer 2 ({l2} files): rest of codebase')
        return '\n'.join(lines)

    def to_api_dict(self) -> dict:
        """Serialise for inclusion in the /analyze API response."""
        return {
            'root_files': self.root_files,
            'direct_deps': self.direct_deps,
            'unresolved': self.unresolved,
            'layer_counts': {
                'layer0': sum(1 for v in self.layer_map.values() if v == 0),
                'layer1': sum(1 for v in self.layer_map.values() if v == 1),
                'layer2': sum(1 for v in self.layer_map.values() if v == 2),
            },
        }


# ─── ReconciliationEngine ─────────────────────────────────────────────────────

class ReconciliationEngine:
    """
    Builds the entry-surface layer map for a codebase.

    The engine is intentionally decoupled from GraphBuilder so it can be
    run early (before or during graph construction) and its results reused
    throughout the pipeline.

    Typical usage inside GraphBuilder::

        engine = ReconciliationEngine(str(self.root_path), all_filepaths)
        surface = engine.build_surface()
        # surface.layer_map  →  drives to_json() column ordering
        # surface.to_api_dict()  →  included in the REST response

    Parameters
    ----------
    root_path:
        Absolute path to the cloned / extracted project root.
    all_filepaths:
        Frozenset of relative forward-slash filepaths for every source file
        that was collected for analysis (i.e. the keys used in GraphBuilder's
        node graph).
    """

    def __init__(
        self,
        root_path: str,
        all_filepaths: frozenset[str],
        ast_imports: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.all_filepaths = all_filepaths
        # AST-extracted imports keyed by relative filepath, provided by GraphBuilder
        self._ast_imports = ast_imports or {}

    # ── Discovery ─────────────────────────────────────────────────────────

    def get_root_files(self) -> list[str]:  # BUG FIX: removed orphaned docstring fragments
        """
        Return files that live directly in the repo root, alphabetically sorted.

        A file is considered root-level when its relative path contains no
        directory separator — e.g. 'index.js', 'middleware.js', 'app.py'.
        Files in any subdirectory (models/, views/, …) are excluded.
        """
        return sorted(
            fp for fp in self.all_filepaths
            if '/' not in fp and '\\' not in fp
        )

    # ── Dependency tracing ────────────────────────────────────────────────

    def get_direct_deps(
        self,
        root_file: str,
    ) -> tuple[set[str], set[str]]:
        """
        Trace the direct local dependencies of a single root-level file.

        Uses **AST-extracted imports** when available (from GraphBuilder's
        parse_file_full), falling back to regex-based extraction otherwise.
        This produces more accurate results because AST parsing ignores
        import-like text inside strings and comments.

        Parameters
        ----------
        root_file:
            Relative path of the root file to analyse, e.g. 'index.js'.

        Returns
        -------
        local_deps:
            Set of relative project filepaths this file directly imports.
        unresolved:
            Set of import strings that could not be matched to a project file.
        """
        local_deps: set[str] = set()
        unresolved: set[str] = set()

        # Prefer AST-extracted imports if available
        if root_file in self._ast_imports:
            for imp_info in self._ast_imports[root_file]:
                source = imp_info.get("source", "")
                is_relative = imp_info.get("is_relative", False)
                if not source:
                    continue

                resolved = resolve_import(
                    source, root_file, self.all_filepaths, is_relative=is_relative,
                )
                if resolved and resolved != root_file:
                    local_deps.add(resolved)
                elif not is_relative:
                    unresolved.add(source)
            return local_deps, unresolved

        # Fallback: read source and use regex extraction
        try:
            code = (self.root_path / root_file).read_text(
                encoding='utf-8', errors='replace'
            )
        except OSError as exc:
            log.warning('ReconciliationEngine: cannot read %s: %s', root_file, exc)
            return set(), set()

        relative_imports, bare_imports = extract_imports(code, root_file)

        for imp in relative_imports:
            resolved = resolve_import(
                imp, root_file, self.all_filepaths, is_relative=True
            )
            if resolved and resolved != root_file:
                local_deps.add(resolved)

        for imp in bare_imports:
            resolved = resolve_import(
                imp, root_file, self.all_filepaths, is_relative=False
            )
            if resolved and resolved != root_file:
                local_deps.add(resolved)
            else:
                unresolved.add(imp)

        return local_deps, unresolved

    # ── Surface construction ───────────────────────────────────────────────

    def build_surface(self) -> ReconciliationSurface:
        """
        Analyse all root files and build the complete entry surface.

        Algorithm
        ---------
        1. Collect root-level files (no path separator → living in repo root).
        2. For each root file, read its source and extract every import.
        3. Resolve each import against the full file list:
             - resolves to a project file  →  Layer 1
             - doesn't resolve            →  third-party / stdlib (unresolved)
        4. Assign layers:
             Layer 0  root files
             Layer 1  direct local deps of any root file  (excluding other
                      root files — they already have Layer 0)
             Layer 2  everything else

        Returns
        -------
        ReconciliationSurface
            Fully populated surface with layer_map ready for GraphBuilder.
        """
        root_files = self.get_root_files()
        root_set = set(root_files)

        direct_deps: dict[str, list[str]] = {}
        unresolved_map: dict[str, list[str]] = {}
        layer1_files: set[str] = set()

        for rf in root_files:
            deps, unresolved = self.get_direct_deps(rf)
            # Root files importing each other stay at Layer 0
            deps -= root_set
            direct_deps[rf] = sorted(deps)
            unresolved_map[rf] = sorted(unresolved)
            layer1_files.update(deps)

        layer_map: dict[str, int] = {}
        for fp in self.all_filepaths:
            if fp in root_set:
                layer_map[fp] = 0
            elif fp in layer1_files:
                layer_map[fp] = 1
            else:
                layer_map[fp] = 2

        surface = ReconciliationSurface(
            root_files=root_files,
            direct_deps=direct_deps,
            unresolved=unresolved_map,
            layer_map=layer_map,
        )
        log.info('Reconciliation complete:\n%s', surface.summary())
        return surface