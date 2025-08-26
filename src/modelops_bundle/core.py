"""Core data models for modelops-bundle."""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, TYPE_CHECKING
import time

from pydantic import BaseModel, Field
from .utils import humanize_size

if TYPE_CHECKING:
    from .snapshot import TrackedFilesSnapshot


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
    
    def update_after_push(self, manifest_digest: str, tracked_files: "TrackedFilesSnapshot") -> None:
        """Update sync state after successful push."""
        self.last_push_digest = manifest_digest
        self.timestamp = time.time()
        # Rebuild last_synced_files with only files that exist
        # This prunes deleted files from the state
        self.last_synced_files = {}
        for path, file_info in tracked_files.files.items():
            self.last_synced_files[path] = file_info.digest
    
    def update_after_pull(self, manifest_digest: str, pulled_files: List["FileInfo"]) -> None:
        """Update sync state after successful pull."""
        self.last_pull_digest = manifest_digest
        self.timestamp = time.time()
        for file_info in pulled_files:
            self.last_synced_files[file_info.path] = file_info.digest


# ============= File Information =============

class FileInfo(BaseModel):
    """Information about a single file."""
    
    path: str
    digest: str  # sha256:...
    size: int
    mtime: Optional[float] = None


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
        manifest_files = []  # ALL files except DELETED_LOCAL
        to_upload = []       # Only changed files
        unchanged = []       # Unchanged paths for UI
        deletes = []         # Deleted locally
        
        for change in self.changes:
            if change.change_type == ChangeType.DELETED_LOCAL:
                deletes.append(change.path)
            elif change.local:  # File exists locally
                manifest_files.append(change.local)
                if change.change_type in (ChangeType.ADDED_LOCAL, ChangeType.MODIFIED_LOCAL):
                    to_upload.append(change.local)
                elif change.change_type == ChangeType.UNCHANGED:
                    unchanged.append(change.path)
        
        total_size = sum(f.size for f in to_upload)
        return PushPlan(
            manifest_files=manifest_files,
            files_to_upload=to_upload,
            files_unchanged=unchanged,
            deletes=deletes,
            total_upload_size=total_size
        )
    
    def to_pull_plan(self, overwrite: bool = False) -> "PullPlan":
        """Convert diff to pull plan."""
        to_download = []
        to_skip = []
        conflicts = []
        to_delete_local = []
        
        for change in self.changes:
            if change.change_type in (ChangeType.ADDED_REMOTE, ChangeType.MODIFIED_REMOTE):
                if change.remote:
                    to_download.append(change.remote)
            elif change.change_type == ChangeType.DELETED_REMOTE:
                if overwrite:
                    to_delete_local.append(change.path)
                else:
                    conflicts.append(change.path)  # Treat as conflict if not overwriting
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
            files_to_delete_local=to_delete_local,
            total_download_size=total_size
        )


# ============= Execution Plans =============

class PushPlan(BaseModel):
    """Plan for push operation."""
    
    manifest_files: List[FileInfo]      # ALL files for manifest (excludes DELETED_LOCAL)
    files_to_upload: List[FileInfo]     # Subset that changed (ADDED/MODIFIED_LOCAL)
    files_unchanged: List[str]          # For UI reporting
    deletes: List[str] = Field(default_factory=list)  # DELETED_LOCAL paths
    total_upload_size: int = 0
    
    def summary(self) -> str:
        """Get human-readable summary."""
        parts = [f"↑ {len(self.files_to_upload)} files to upload ({humanize_size(self.total_upload_size)})", 
                 f"{len(self.files_unchanged)} unchanged"]
        if self.deletes:
            parts.append(f"{len(self.deletes)} to delete")
        return ", ".join(parts)


class PullPlan(BaseModel):
    """Plan for pull operation."""
    
    files_to_download: List[FileInfo]
    files_to_skip: List[str]
    conflicts: List[str]
    files_to_delete_local: List[str] = Field(default_factory=list)  # DELETED_REMOTE paths
    total_download_size: int = 0
    
    def summary(self) -> str:
        """Get human-readable summary."""
        parts = [f"↓ {len(self.files_to_download)} files to download ({humanize_size(self.total_download_size)})"]
        if self.conflicts:
            parts.append(f"⚠ {len(self.conflicts)} conflicts")
        if self.files_to_skip:
            parts.append(f"{len(self.files_to_skip)} skipped")
        if self.files_to_delete_local:
            parts.append(f"{len(self.files_to_delete_local)} to delete locally")
        return ", ".join(parts)
