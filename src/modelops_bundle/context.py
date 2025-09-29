"""Project context management for modelops-bundle.

This module handles project root discovery and path resolution.
It does NOT handle environment management - that's in env_manager.py.
"""
from pathlib import Path
from typing import Optional, Union
from .constants import MODELOPS_BUNDLE_DIR
from .ignore import IgnoreSpec


class ProjectContext:
    """Manages project root discovery and path resolution.

    This class is responsible for:
    - Finding the project root (directory with .modelops-bundle/)
    - Resolving paths relative to the project
    - Managing ignore specifications
    - Providing standard paths (config, state, tracked files)

    It does NOT handle:
    - Environment loading (see env_manager.py)
    - Credential management (see env_manager.py)
    """

    def __init__(self, start_path: Optional[Path] = None):
        """Initialize by finding project root.

        Args:
            start_path: Path to start searching for project root

        Raises:
            ValueError: If not inside a modelops-bundle project
        """
        root = self._find_root((start_path or Path.cwd()).resolve())
        if root is None:
            raise ValueError(f"Not inside a modelops-bundle project (no {MODELOPS_BUNDLE_DIR} found)")
        self.root: Path = root
        self._ignore_spec: Optional[IgnoreSpec] = None

    @classmethod
    def is_initialized(cls, path: Optional[Path] = None) -> bool:
        """Check if a specific directory is initialized.

        Args:
            path: Directory to check (defaults to current)

        Returns:
            True if the directory has .modelops-bundle/ directory
        """
        target = (path or Path.cwd()).resolve()
        return (target / MODELOPS_BUNDLE_DIR).is_dir()

    @classmethod
    def init(cls, path: Optional[Path] = None) -> 'ProjectContext':
        """Initialize a new project at the given path.

        Args:
            path: Directory to initialize (defaults to current)

        Returns:
            ProjectContext for the initialized project
        """
        target = (path or Path.cwd()).resolve()
        marker = target / MODELOPS_BUNDLE_DIR
        marker.mkdir(exist_ok=True)
        return cls(target)

    def _find_root(self, start: Path) -> Optional[Path]:
        """Walk up directory tree to find project root.

        Args:
            start: Starting directory (must be absolute)

        Returns:
            Path to project root, or None if not found
        """
        current = start
        while True:
            marker = current / MODELOPS_BUNDLE_DIR
            if marker.is_dir():
                return current
            if current == current.parent:
                return None
            current = current.parent

    # --- New API: explicit and safe ---
    def to_project_relative(self, path: Union[str, Path], *, allow_outside: bool = False) -> Path:
        """Return a path relative to project root.

        If path is outside the project and allow_outside is False, raise ValueError.

        Args:
            path: Path to convert (can be absolute or relative)
            allow_outside: If True, return absolute paths for files outside project

        Returns:
            Path relative to project root (or absolute if outside and allow_outside=True)

        Raises:
            ValueError: If path is outside project and allow_outside=False
        """
        p = Path(path)
        abs_p = (p if p.is_absolute() else (Path.cwd() / p)).resolve()
        try:
            return abs_p.relative_to(self.root)
        except ValueError:
            if allow_outside:
                return abs_p
            raise ValueError(f"Path {abs_p} is outside project root {self.root}")


    def absolute(self, project_path: Union[str, Path]) -> Path:
        """Convert a project-relative path to absolute.

        Args:
            project_path: Path relative to project root

        Returns:
            Absolute path
        """
        p = Path(project_path)
        if p.is_absolute():
            return p
        # Do not resolve() here; keep symlink semantics for files we control
        return self.root / p

    # Standard paths
    @property
    def storage_dir(self) -> Path:
        """Get the .modelops-bundle directory."""
        return self.root / MODELOPS_BUNDLE_DIR

    @property
    def config_path(self) -> Path:
        """Get path to config.yaml."""
        return self.storage_dir / "config.yaml"

    @property
    def tracked_path(self) -> Path:
        """Get path to tracked.txt."""
        return self.storage_dir / "tracked.txt"

    @property
    def state_path(self) -> Path:
        """Get path to state.json."""
        return self.storage_dir / "state.json"

    # Ignore handling
    def get_ignore_spec(self) -> IgnoreSpec:
        """Get or create the ignore specification.

        Returns:
            IgnoreSpec for this project
        """
        if self._ignore_spec is None:
            self._ignore_spec = IgnoreSpec(self.root)
        return self._ignore_spec

    def should_ignore(self, relpath: Union[str, Path]) -> bool:
        """Check if a path should be ignored.

        Args:
            relpath: Path relative to project root (or absolute)

        Returns:
            True if the path should be ignored
        """
        # Convert to project-relative if absolute
        p = Path(relpath)
        if p.is_absolute():
            try:
                p = p.relative_to(self.root)
            except ValueError:
                # Absolute path outside project → never ignore via project ignore file
                return False

        # Normalize to POSIX for consistent comparison
        return self.get_ignore_spec().is_ignored(p.as_posix())