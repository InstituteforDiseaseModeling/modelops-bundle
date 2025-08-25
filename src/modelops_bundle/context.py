"""Project context for managing paths and project discovery."""

from pathlib import Path
from typing import Optional, Union

from .constants import MODELOPS_BUNDLE_DIR


class ProjectContext:
    """Manages project root discovery and path resolution."""
    
    def __init__(self, start_path: Optional[Path] = None):
        """Initialize context by finding project root."""
        self.root = self._find_root(start_path or Path.cwd())
        if not self.root:
            raise ValueError(f"Not inside a modelops-bundle project (no {MODELOPS_BUNDLE_DIR} found)")
    
    @classmethod
    def is_initialized(cls, path: Optional[Path] = None) -> bool:
        """Check if a specific directory is initialized (without traversing up)."""
        target = path or Path.cwd()
        return (target / MODELOPS_BUNDLE_DIR).exists()
    
    @classmethod
    def init(cls, path: Optional[Path] = None) -> 'ProjectContext':
        """Initialize a new project at the given path."""
        target = path or Path.cwd()
        marker = target / MODELOPS_BUNDLE_DIR
        marker.mkdir(exist_ok=True)
        return cls(target)
    
    def _find_root(self, start: Path) -> Optional[Path]:
        """Walk up directory tree to find project root."""
        current = start.resolve()
        
        while current != current.parent:
            if (current / MODELOPS_BUNDLE_DIR).exists():
                return current
            current = current.parent
        
        # Check root directory
        if (current / MODELOPS_BUNDLE_DIR).exists():
            return current
        return None
    
    def resolve(self, path: Union[str, Path]) -> Path:
        """Convert any path to project-relative path."""
        p = Path(path)
        
        if p.is_absolute():
            # Already absolute, make relative to root if inside project
            try:
                return p.relative_to(self.root)
            except ValueError:
                raise ValueError(f"Path {p} is outside project")
        else:
            # Relative path from CWD, convert to project-relative
            absolute = (Path.cwd() / p).resolve()
            try:
                return absolute.relative_to(self.root)
            except ValueError:
                raise ValueError(f"Path {p} is outside project")
    
    def absolute(self, project_path: Union[str, Path]) -> Path:
        """Get absolute path from project-relative path."""
        return self.root / project_path
    
    @property
    def storage_dir(self) -> Path:
        """Get the project storage directory."""
        return self.root / MODELOPS_BUNDLE_DIR
    
    @property
    def config_path(self) -> Path:
        """Get path to config file."""
        from .constants import CONFIG_FILE
        return self.storage_dir / CONFIG_FILE
    
    @property
    def tracked_path(self) -> Path:
        """Get path to tracked files list."""
        from .constants import TRACKED_FILE
        return self.storage_dir / TRACKED_FILE
    
    @property
    def state_path(self) -> Path:
        """Get path to sync state file."""
        from .constants import STATE_FILE
        return self.storage_dir / STATE_FILE