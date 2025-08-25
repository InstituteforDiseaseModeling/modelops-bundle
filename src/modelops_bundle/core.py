"""Core data models for modelops-bundle."""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set
import hashlib
import time

from pydantic import BaseModel, Field


# ============= Configuration =============

class BundleConfig(BaseModel):
    """Bundle configuration (stored in .modelops-bundle/config.yaml)."""
    
    registry_ref: str  # e.g. localhost:5555/epi_model
    default_tag: str = "latest"
    artifact_type: str = "application/vnd.modelops.bundle.v1"


# ============= File Tracking =============

class TrackedFiles(BaseModel):
    """Tracked files list (stored in .modelops-bundle/tracked)."""
    
    files: Set[str] = Field(default_factory=set)
    
    def add(self, *paths: Path) -> None:
        """Add paths to tracking."""
        for p in paths:
            self.files.add(str(p))
    
    def remove(self, *paths: Path) -> None:
        """Remove paths from tracking."""
        for p in paths:
            self.files.discard(str(p))


# ============= State Management =============

class SyncState(BaseModel):
    """
    Sync state (stored in .modelops-bundle/state.json).
    """
    
    last_push_digest: Optional[str] = None
    last_pull_digest: Optional[str] = None
    last_synced_files: Dict[str, str] = Field(default_factory=dict)  # path -> digest
    timestamp: float = Field(default_factory=time.time)


# ============= File Information =============

class FileInfo(BaseModel):
    """Information about a single file."""
    
    path: str
    digest: str  # sha256:...
    size: int
    mtime: Optional[float] = None


class WorkingTreeState(BaseModel):
    """Current state of tracked files in working directory."""
    
    files: Dict[str, FileInfo] = Field(default_factory=dict)
    
    @classmethod
    def scan(cls, tracked: Set[str], root: Path = Path(".")) -> "WorkingTreeState":
        """Scan tracked files and return their current state."""
        files = {}
        for path_str in tracked:
            path = root / path_str
            if path.exists():
                files[path_str] = FileInfo(
                    path=path_str,
                    digest=_compute_digest(path),
                    size=path.stat().st_size,
                    mtime=path.stat().st_mtime
                )
        return cls(files=files)


class RemoteState(BaseModel):
    """State of files in remote registry."""
    
    manifest_digest: str
    files: Dict[str, FileInfo] = Field(default_factory=dict)


# ============= Change Detection =============

class ChangeType(str, Enum):
    """Type of change detected."""
    
    UNCHANGED = "unchanged"
    ADDED_LOCAL = "added_local"
    ADDED_REMOTE = "added_remote"
    MODIFIED_LOCAL = "modified_local"
    MODIFIED_REMOTE = "modified_remote"
    DELETED_LOCAL = "deleted_local"
    DELETED_REMOTE = "deleted_remote"
    CONFLICT = "conflict"


class FileChange(BaseModel):
    """Single file change."""
    
    path: str
    change_type: ChangeType
    local: Optional[FileInfo] = None
    remote: Optional[FileInfo] = None
    last_synced: Optional[str] = None


class DiffResult(BaseModel):
    """Result of comparing local and remote states."""
    
    changes: List[FileChange]
    
    @property
    def summary(self) -> Dict[str, int]:
        """Get summary counts by change type."""
        counts = {}
        for change in self.changes:
            counts[change.change_type] = counts.get(change.change_type, 0) + 1
        return counts
    
    def to_push_plan(self) -> "PushPlan":
        """Convert diff to push plan."""
        to_upload = []
        unchanged = []
        
        for change in self.changes:
            if change.change_type in (ChangeType.ADDED_LOCAL, ChangeType.MODIFIED_LOCAL):
                if change.local:
                    to_upload.append(change.local)
            elif change.change_type == ChangeType.UNCHANGED:
                unchanged.append(change.path)
        
        total_size = sum(f.size for f in to_upload)
        return PushPlan(
            files_to_upload=to_upload,
            files_unchanged=unchanged,
            total_upload_size=total_size
        )
    
    def to_pull_plan(self, overwrite: bool = False) -> "PullPlan":
        """Convert diff to pull plan."""
        to_download = []
        to_skip = []
        conflicts = []
        
        for change in self.changes:
            if change.change_type in (ChangeType.ADDED_REMOTE, ChangeType.MODIFIED_REMOTE):
                if change.remote:
                    to_download.append(change.remote)
            elif change.change_type == ChangeType.CONFLICT:
                if overwrite and change.remote:
                    to_download.append(change.remote)
                else:
                    conflicts.append(change.path)
            elif change.change_type == ChangeType.MODIFIED_LOCAL:
                to_skip.append(change.path)
        
        total_size = sum(f.size for f in to_download)
        return PullPlan(
            files_to_download=to_download,
            files_to_skip=to_skip,
            conflicts=conflicts,
            total_download_size=total_size
        )


# ============= Execution Plans =============

class PushPlan(BaseModel):
    """Plan for push operation."""
    
    files_to_upload: List[FileInfo]
    files_unchanged: List[str]
    total_upload_size: int = 0
    
    def summary(self) -> str:
        """Get human-readable summary."""
        return f"↑ {len(self.files_to_upload)} files to upload ({_humanize_size(self.total_upload_size)}), {len(self.files_unchanged)} unchanged"


class PullPlan(BaseModel):
    """Plan for pull operation."""
    
    files_to_download: List[FileInfo]
    files_to_skip: List[str]
    conflicts: List[str]
    total_download_size: int = 0
    
    def summary(self) -> str:
        """Get human-readable summary."""
        parts = [f"↓ {len(self.files_to_download)} files to download ({_humanize_size(self.total_download_size)})"]
        if self.conflicts:
            parts.append(f"⚠ {len(self.conflicts)} conflicts")
        if self.files_to_skip:
            parts.append(f"{len(self.files_to_skip)} skipped")
        return ", ".join(parts)


# ============= Utilities =============

def _compute_digest(path: Path) -> str:
    """Compute SHA256 digest of file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _humanize_size(size: int) -> str:
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
