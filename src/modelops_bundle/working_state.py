"""Working state abstraction that combines snapshot with deletion tracking."""

from dataclasses import dataclass, field
from typing import List, Optional, Set

from .context import ProjectContext
from .core import (
    ChangeType,
    DiffResult,
    FileInfo,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from .diffing import compute_diff
from .snapshot import TrackedFilesSnapshot


@dataclass
class ChangeRow:
    """A single change for UI display."""
    path: str
    change: ChangeType
    local: Optional[FileInfo] = None
    remote: Optional[FileInfo] = None


@dataclass(slots=True)
class StatusSummary:
    """High-level status summary for UI display."""
    
    total_tracked: int
    total_size: int  # Bytes of locally present tracked files
    
    # Change counts by type
    unchanged: int = 0
    added_local: int = 0
    added_remote: int = 0
    modified_local: int = 0
    modified_remote: int = 0
    deleted_local: int = 0
    deleted_remote: int = 0
    conflicts: int = 0
    
    # File lists for UI
    local_only_files: List[FileInfo] = field(default_factory=list)
    remote_only_files: List[FileInfo] = field(default_factory=list)
    changed_files: List[ChangeRow] = field(default_factory=list)
    
    @property
    def has_changes(self) -> bool:
        """Check if there are any local changes."""
        return (self.added_local > 0 or 
                self.modified_local > 0 or 
                self.deleted_local > 0)
    
    @property
    def has_remote_changes(self) -> bool:
        """Check if there are any remote changes to pull."""
        return (self.added_remote > 0 or 
                self.modified_remote > 0 or 
                self.deleted_remote > 0)
    
    @property
    def has_conflicts(self) -> bool:
        """Check if there are any conflicts."""
        return self.conflicts > 0
    
    @property
    def is_synced(self) -> bool:
        """Check if fully synced with remote."""
        return (self.unchanged == self.total_tracked and
                self.added_remote == 0 and
                self.modified_remote == 0 and
                self.deleted_remote == 0)


class TrackedWorkingState:
    """
    Represents the complete state of tracked files including deletions.
    
    This abstraction ensures deletions are never forgotten when computing diffs
    and provides a clean interface for status/diff operations.
    """
    
    def __init__(self, snapshot: TrackedFilesSnapshot, missing: Set[str]):
        self.snapshot = snapshot
        self.missing = missing
    
    @classmethod
    def from_tracked(
        cls,
        tracked: TrackedFiles,
        ctx: Optional[ProjectContext] = None
    ) -> "TrackedWorkingState":
        """
        Create working state from tracked files.
        
        Automatically detects which tracked files are missing (deleted).
        """
        if ctx is None:
            ctx = ProjectContext()
        
        # Scan existing files
        snapshot = TrackedFilesSnapshot.scan(tracked.files, ctx.root)
        
        # Calculate missing files (tracked but not on disk)
        missing = set(tracked.files) - set(snapshot.files.keys())
        
        return cls(snapshot, missing)
    
    @property
    def files(self):
        """Convenience accessor for snapshot files."""
        return self.snapshot.files
    
    @property
    def present_paths(self) -> Set[str]:
        """Get paths of files that exist on disk."""
        return set(self.snapshot.files.keys())
    
    @property
    def missing_paths(self) -> Set[str]:
        """Get paths of tracked files that are missing (deleted)."""
        return self.missing
    
    @property 
    def all_tracked_paths(self) -> Set[str]:
        """Get all tracked paths including missing ones."""
        return self.present_paths | self.missing_paths
    
    def has_deletions(self) -> bool:
        """Check if any tracked files are missing."""
        return bool(self.missing)
    
    def compute_diff(
        self,
        remote: RemoteState,
        last_sync: SyncState
    ) -> DiffResult:
        """
        Compute differences between this working state and remote.
        
        Automatically includes deletion detection.
        """
        return compute_diff(
            local=self.snapshot,
            remote=remote,
            last_sync=last_sync,
            missing_local=self.missing
        )
    
    def get_status(
        self,
        remote: Optional[RemoteState],
        last_sync: SyncState
    ) -> StatusSummary:
        """
        Get comprehensive status summary for UI display.
        
        Provides high-level summary with counts, categories, and actionable info.
        """
        # Calculate total tracked files and size of present files
        total_tracked = len(self.all_tracked_paths)
        total_size = sum(f.size for f in self.files.values())
        
        summary = StatusSummary(
            total_tracked=total_tracked,
            total_size=total_size
        )
        
        # If no remote, just return local-only summary
        if remote is None:
            summary.local_only_files = list(self.files.values())
            summary.added_local = len(self.files)
            summary.deleted_local = len(self.missing)
            return summary
        
        # Compute diff and categorize
        diff = self.compute_diff(remote, last_sync)
        
        # Group changes and count by type
        for change in diff.changes:
            # Count by type
            if change.change_type == ChangeType.UNCHANGED:
                summary.unchanged += 1
                
            elif change.change_type == ChangeType.ADDED_LOCAL:
                summary.added_local += 1
                summary.local_only_files.append(change.local)
                
            elif change.change_type == ChangeType.ADDED_REMOTE:
                summary.added_remote += 1
                summary.remote_only_files.append(change.remote)
                
            elif change.change_type == ChangeType.MODIFIED_LOCAL:
                summary.modified_local += 1
                summary.changed_files.append(
                    ChangeRow(change.path, change.change_type, local=change.local)
                )
                
            elif change.change_type == ChangeType.MODIFIED_REMOTE:
                summary.modified_remote += 1
                summary.changed_files.append(
                    ChangeRow(change.path, change.change_type, remote=change.remote)
                )
                
            elif change.change_type == ChangeType.DELETED_LOCAL:
                summary.deleted_local += 1
                summary.changed_files.append(
                    ChangeRow(change.path, change.change_type)
                )
                
            elif change.change_type == ChangeType.DELETED_REMOTE:
                summary.deleted_remote += 1
                summary.changed_files.append(
                    ChangeRow(change.path, change.change_type, local=change.local)
                )
                
            elif change.change_type == ChangeType.CONFLICT:
                summary.conflicts += 1
                # Keep both local and remote for conflicts
                summary.changed_files.append(
                    ChangeRow(change.path, change.change_type, 
                             local=change.local, remote=change.remote)
                )
        
        return summary