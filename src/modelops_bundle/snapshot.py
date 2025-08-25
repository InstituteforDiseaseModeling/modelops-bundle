"""File snapshot with digest computation."""

from pathlib import Path
from typing import Dict, Set

from pydantic import BaseModel, Field

from .core import FileInfo
from .utils import compute_digest


class TrackedFilesSnapshot(BaseModel):
    """
    Full snapshot of tracked files with digests.
    
    This is the expensive operation - computes SHA256 for all files.
    Renamed from WorkingTreeState for clarity.
    """
    
    files: Dict[str, FileInfo] = Field(default_factory=dict)
    
    @classmethod
    def scan(cls, tracked: Set[str], root: Path = Path(".")) -> "TrackedFilesSnapshot":
        """Scan tracked files and return their current state."""
        files = {}
        for path_str in tracked:
            path = root / path_str
            if path.exists():
                files[path_str] = FileInfo(
                    path=path_str,
                    digest=compute_digest(path),
                    size=path.stat().st_size,
                    mtime=path.stat().st_mtime
                )
        return cls(files=files)
