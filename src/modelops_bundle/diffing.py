"""Diff computation logic - stable module for computing differences."""

from typing import Set

from .core import (
    ChangeType,
    DiffResult,
    FileChange,
    RemoteState,
    SyncState,
)
from .snapshot import TrackedFilesSnapshot


def compute_diff(
    local: TrackedFilesSnapshot,
    remote: RemoteState,
    last_sync: SyncState,
    missing_local: Set[str],
) -> DiffResult:
    """
    Compute differences between local and remote states.

    Args:
        local: Current local tracked files snapshot.
        remote: Current remote state from registry.
        last_sync: Last known sync state.
        missing_local: Set of paths known to be missing locally (deleted).

    Returns:
        DiffResult with list of FileChange entries.

    Note:
        missing_local is used to detect local deletions since TrackedFilesSnapshot
        only contains files that exist on disk.
    """
    changes = []
    
    # Handle local deletions first
    for path in missing_local:
        last_digest = last_sync.last_synced_files.get(path)
        remote_file = remote.files.get(path)
        
        if last_digest is None:
            # Never synced - skip (file was added then deleted before sync)
            continue
            
        if remote_file and remote_file.digest != last_digest:
            # Remote changed since last sync, local deleted -> CONFLICT
            change_type = ChangeType.CONFLICT
        else:
            # Remote unchanged or absent -> DELETED_LOCAL
            change_type = ChangeType.DELETED_LOCAL
            
        changes.append(FileChange(
            path=path,
            change_type=change_type,
            local=None,
            remote=remote_file,
            last_synced=last_digest
        ))
    
    # Get paths that are present locally and/or remotely (excluding missing)
    present_local = set(local.files.keys())
    all_paths = (present_local | set(remote.files.keys())) - missing_local
    
    for path in all_paths:
        local_file = local.files.get(path)
        remote_file = remote.files.get(path)
        last_digest = last_sync.last_synced_files.get(path)
        
        # Both exist
        if local_file and remote_file:
            if local_file.digest == remote_file.digest:
                change_type = ChangeType.UNCHANGED
            elif last_digest is None:
                # No baseline - conservative conflict
                change_type = ChangeType.CONFLICT
            elif local_file.digest == last_digest and remote_file.digest != last_digest:
                change_type = ChangeType.MODIFIED_REMOTE
            elif remote_file.digest == last_digest and local_file.digest != last_digest:
                change_type = ChangeType.MODIFIED_LOCAL
            else:
                # Both modified
                change_type = ChangeType.CONFLICT
        
        # Local only
        elif local_file and not remote_file:
            if last_digest:
                # File was previously synced
                if local_file.digest == last_digest:
                    change_type = ChangeType.DELETED_REMOTE  # Unchanged locally, deleted remotely
                else:
                    change_type = ChangeType.CONFLICT  # Modified locally, deleted remotely
            else:
                change_type = ChangeType.ADDED_LOCAL  # Never synced, local only
        
        # Remote only
        elif remote_file and not local_file:
            change_type = ChangeType.ADDED_REMOTE
        
        # Neither (shouldn't happen)
        else:
            continue
        
        changes.append(FileChange(
            path=path,
            change_type=change_type,
            local=local_file,
            remote=remote_file,
            last_synced=last_digest
        ))
    
    return DiffResult(changes=changes)