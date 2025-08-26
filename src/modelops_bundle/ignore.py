"""Gitignore-style pattern matching for modelops-bundle."""

from pathlib import Path
from typing import Iterable, Optional

from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern


# Default patterns to always ignore
DEFAULTS = [
    # Version control
    ".git/",
    
    # ModelOps Bundle metadata
    ".modelops-bundle/",
    
    # Python
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".Python",
    "*.egg-info/",
    "dist/",
    "build/",
    
    # Virtual environments
    "venv/",
    ".venv/",
    "env/",
    ".env/",
    "ENV/",
    "virtualenv/",
    
    # Node.js
    "node_modules/",
    ".npm/",
    ".yarn/",
    
    # IDE and editors
    ".idea/",
    ".vscode/",
    "*.swp",
    "*.swo",
    "*~",
    ".*.swp",
    
    # OS files
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    
    # Jupyter
    ".ipynb_checkpoints/",
    
    # Coverage and testing
    ".coverage",
    "htmlcov/",
    ".tox/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
]


class IgnoreSpec:
    """Manages gitignore-style patterns for file exclusion."""
    
    def __init__(self, root: Path, extra: Iterable[str] = ()):
        """Initialize ignore spec with default and custom patterns.
        
        Args:
            root: Project root directory
            extra: Additional patterns to include
        """
        self.root = root
        patterns = list(DEFAULTS)
        
        # Load project-specific .modelopsignore if it exists
        ignore_file = root / ".modelopsignore"
        if ignore_file.exists():
            content = ignore_file.read_text()
            # Split by lines, filter out empty lines and comments
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        
        # Add any extra patterns provided
        patterns.extend(extra)
        
        # Compile patterns once for efficiency
        self.spec = PathSpec.from_lines(GitWildMatchPattern, patterns)
    
    def is_ignored(self, relpath: str) -> bool:
        """Check if a project-relative POSIX path should be ignored.
        
        Args:
            relpath: Project-relative path in POSIX format (forward slashes)
            
        Returns:
            True if the path matches any ignore pattern
        """
        return self.spec.match_file(relpath)
    
    def should_traverse(self, dirpath: str) -> bool:
        """Check if a directory should be traversed during scanning.
        
        This is an optimization - if a directory is ignored, we can skip
        traversing it entirely.
        
        Args:
            dirpath: Project-relative directory path in POSIX format
            
        Returns:
            True if the directory should be traversed
        """
        # Always skip .modelops-bundle directory
        if dirpath == ".modelops-bundle" or dirpath.startswith(".modelops-bundle/"):
            return False
            
        # Check if directory itself is ignored
        # Add trailing slash to match directory patterns
        if not dirpath.endswith("/"):
            dirpath = dirpath + "/"
        
        return not self.spec.match_file(dirpath)