"""Service layer types for modelops-bundle."""

from typing import List, Optional, Protocol
from pydantic import BaseModel


class ChangeInfo(BaseModel):
    """Information about a file change."""
    path: str
    change_type: str  # "added", "modified", "deleted"
    size: int = 0
    digest: Optional[str] = None


class StatusReport(BaseModel):
    """Comprehensive status report."""
    local_changes: List[ChangeInfo]
    remote_changes: List[ChangeInfo]
    conflicts: List[str]
    local_only: List[str]
    remote_only: List[str]
    up_to_date: bool
    summary: str


class AddResult(BaseModel):
    """Result of adding files to tracking."""
    added: List[str]
    already_tracked: List[str]
    ignored: List[str]
    total_size: int


class RemoveResult(BaseModel):
    """Result of removing files from tracking."""
    removed: List[str]
    not_tracked: List[str]


class PushResult(BaseModel):
    """Result of push operation."""
    manifest_digest: str
    tag: str
    files_pushed: int
    bytes_uploaded: int
    summary: str


class EnsureLocalResult(BaseModel):
    """Result of ensure_local operation."""
    resolved_digest: str
    downloaded: int
    deleted: int
    bytes_downloaded: int
    dry_run: bool = False


class ProgressCallback(Protocol):
    """Progress reporting interface."""
    
    def on_file_start(self, path: str, size: int) -> None:
        """Called when starting to process a file."""
        ...
    
    def on_file_complete(self, path: str) -> None:
        """Called when file processing is complete."""
        ...
    
    def on_file_error(self, path: str, error: str) -> None:
        """Called when file processing fails."""
        ...