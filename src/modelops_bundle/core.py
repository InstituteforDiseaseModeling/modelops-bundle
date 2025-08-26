"""Core data models for modelops-bundle.

Two-Phase Plan/Apply Pattern for Tag Race Prevention:
------------------------------------------------------
Tags in registries are mutable and can change between preview and execution.
We use a two-phase pattern to prevent races:

1. Plan Phase: Resolve tags to immutable digests, capture current state
2. Apply Phase: Execute using resolved digests, verify state unchanged

This ensures operations are atomic and predictable - what you preview
is exactly what you get, even if tags move concurrently.
"""

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
    # TODO: Add artifact_type when oras-py supports setting it in manifests


# ============= File Tracking =============

class TrackedFiles(BaseModel):
    """Tracked files list (stored in .modelops-bundle/tracked).
    
    All paths are stored as POSIX strings (forward slashes) for consistency
    across platforms.
    """
    
    files: Set[str] = Field(default_factory=set)
    
    def add(self, *paths) -> None:
        """Add paths to tracking (normalized to POSIX)."""
        for p in paths:
            # Convert to Path if string, then to POSIX for cross-platform consistency
            if isinstance(p, str):
                p = Path(p)
            self.files.add(p.as_posix())
    
    def remove(self, *paths) -> None:
        """Remove paths from tracking (normalized to POSIX)."""
        for p in paths:
            # Convert to Path if string, then normalize to POSIX for consistent lookups
            if isinstance(p, str):
                p = Path(p)
            self.files.discard(p.as_posix())


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
    
    def to_push_plan(self, tag: str = "latest", tag_base_digest: Optional[str] = None) -> "PushPlan":
        """Convert diff to push plan with tag tracking."""
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
            tag=tag,
            tag_base_digest=tag_base_digest,
            manifest_files=manifest_files,
            files_to_upload=to_upload,
            files_unchanged=unchanged,
            deletes=deletes,
            total_upload_size=total_size
        )
    
    def to_pull_preview(self, overwrite: bool = False,
                        resolved_digest: str = "",
                        original_reference: str = "") -> "PullPreview":
        """Generate preview with resolved digest for race-free execution."""
        conflicts = []
        will_delete_local = []
        will_update_or_add = []
        
        for change in self.changes:
            # Files that would be added or updated from remote
            if change.change_type in (ChangeType.ADDED_REMOTE, ChangeType.MODIFIED_REMOTE):
                if change.remote:
                    will_update_or_add.append(change.remote)
                    
            # Files that would be deleted locally (remote deleted them)
            elif change.change_type == ChangeType.DELETED_REMOTE:
                if overwrite:
                    will_delete_local.append(change.path)
                else:
                    # Without overwrite, deletion is a conflict
                    conflicts.append(change.path)
                    
            # Conflicting changes
            elif change.change_type == ChangeType.CONFLICT:
                if overwrite and change.remote:
                    # With overwrite, remote wins
                    will_update_or_add.append(change.remote)
                else:
                    conflicts.append(change.path)
                    
            # Local modifications
            elif change.change_type == ChangeType.MODIFIED_LOCAL:
                if overwrite and change.remote:
                    # With overwrite, remote replaces local
                    will_update_or_add.append(change.remote)
                # Without overwrite, local changes block the pull (handled by safety guards)
        
        total_size = sum(f.size for f in will_update_or_add)
        
        return PullPreview(
            resolved_digest=resolved_digest,
            original_reference=original_reference,
            conflicts=conflicts,
            will_delete_local=will_delete_local,
            will_update_or_add=will_update_or_add,
            total_download_size=total_size
        )


# ============= Execution Plans =============

class PushPlan(BaseModel):
    """Push plan with tag_base_digest to detect concurrent tag updates."""
    
    # Tag race prevention
    tag: str = "latest"
    tag_base_digest: Optional[str] = None  # Current digest when plan was created
    
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


class PullPreview(BaseModel):
    """Pull preview with resolved_digest to ensure exactly what's previewed gets pulled."""
    
    # Race prevention - the resolved reference
    resolved_digest: str
    original_reference: str
    
    conflicts: List[str] = Field(default_factory=list)  # Files with conflicts
    will_delete_local: List[str] = Field(default_factory=list)  # Files that would be deleted locally
    will_update_or_add: List[FileInfo] = Field(default_factory=list)  # Files that would be added/updated
    will_overwrite_untracked: List[str] = Field(default_factory=list)  # Untracked files that would be overwritten
    total_download_size: int = 0
    
    def summary(self) -> str:
        """Get human-readable summary."""
        parts = []
        
        # Show what would be downloaded
        if self.will_update_or_add:
            parts.append(f"↓ {len(self.will_update_or_add)} files ({humanize_size(self.total_download_size)})")
        else:
            parts.append("No changes")
            
        # Show conflicts and deletions
        if self.conflicts:
            parts.append(f"⚠ {len(self.conflicts)} conflicts")
        if self.will_delete_local:
            parts.append(f"{len(self.will_delete_local)} to delete locally")
        if self.will_overwrite_untracked:
            parts.append(f"⚠ {len(self.will_overwrite_untracked)} untracked files to overwrite")
            
        return ", ".join(parts)
    
    def has_destructive_changes(self) -> bool:
        """Check if this pull would destroy local data."""
        return bool(self.conflicts or self.will_delete_local or self.will_overwrite_untracked)


class PullResult(BaseModel):
    """Result of a completed pull operation."""
    
    downloaded: int = 0  # Number of files downloaded
    deleted: int = 0  # Number of local files deleted
    manifest_digest: str  # Digest of pulled manifest
    
    def summary(self) -> str:
        """Get human-readable summary."""
        parts = [f"✓ Pulled {self.downloaded} files"]
        if self.deleted:
            parts.append(f"deleted {self.deleted} local files")
        return ", ".join(parts)
