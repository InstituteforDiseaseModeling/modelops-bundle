"""Helper utilities for discovering model dependencies."""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import Iterable, List, Set

from .config import AutoCodeConfig, load_bundle_config


def _ignored(path: Path, root: Path, patterns: List[str]) -> bool:
    if not patterns:
        return False
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def _ast_import_roots(py_file: Path) -> Set[str]:
    mods: Set[str] = set()
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except Exception:
        return mods

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith("."):
                mods.add(node.module.split(".")[0])
    return mods


def _candidate_paths(module: str, code_roots: Iterable[Path]) -> List[Path]:
    candidates: List[Path] = []
    for root in code_roots:
        pkg_dir = root / module
        module_file = root / f"{module}.py"
        if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
            candidates.append(pkg_dir)
        elif module_file.exists():
            candidates.append(module_file)
    return candidates


def discover_local_imports(
    model_path: Path,
    project_root: Path,
    *,
    override_mode: str | None = None,
    override_roots: List[str] | None = None,
) -> List[Path]:
    """Discover code dependencies for a model file."""

    cfg: AutoCodeConfig = load_bundle_config(project_root)
    code_roots = [project_root / p for p in (override_roots or cfg.code_roots)]
    mode = (override_mode or cfg.import_mode).lower()
    ignore = cfg.ignore

    if mode not in ("package", "files"):
        mode = "package"

    top_modules = _ast_import_roots(model_path)

    if mode == "package":
        picks: Set[Path] = set()
        for module in top_modules:
            for candidate in _candidate_paths(module, code_roots):
                if not _ignored(candidate, project_root, ignore):
                    picks.add(candidate.relative_to(project_root))

        for root in code_roots:
            try:
                rel = model_path.relative_to(root)
            except ValueError:
                continue
            if len(rel.parts) >= 2:
                pkg_dir = root / rel.parts[0]
                if (pkg_dir / "__init__.py").exists() and not _ignored(pkg_dir, project_root, ignore):
                    picks.add(pkg_dir.relative_to(project_root))
        return sorted(picks)

    visited: Set[Path] = set()
    to_visit = [model_path]

    def in_roots(path: Path) -> bool:
        return any(path.is_relative_to(root) for root in code_roots)

    while to_visit:
        current = to_visit.pop()
        if current in visited or not current.exists():
            continue
        visited.add(current)

        for module in _ast_import_roots(current):
            for candidate in _candidate_paths(module, code_roots):
                if candidate.is_dir():
                    for sub in candidate.rglob("*.py"):
                        if in_roots(sub) and not _ignored(sub, project_root, ignore):
                            to_visit.append(sub)
                else:
                    if in_roots(candidate) and not _ignored(candidate, project_root, ignore):
                        to_visit.append(candidate)

    picks = [
        p.relative_to(project_root)
        for p in visited
        if p.is_file() and in_roots(p) and not _ignored(p, project_root, ignore)
    ]
    return sorted(set(picks))
