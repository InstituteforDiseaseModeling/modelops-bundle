"""Core operations for modelops-bundle."""

from pathlib import Path
from typing import Dict, List, Optional, Set
import json
import yaml

from .context import ProjectContext

from .core import (
    BundleConfig,
    ChangeType,
    DiffResult,
    FileChange,
    FileInfo,
    PullPlan,
    PushPlan,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from .snapshot import TrackedFilesSnapshot
from .oras import OrasAdapter


# ============= File I/O =============

def load_config(ctx: Optional[ProjectContext] = None) -> BundleConfig:
    """Load bundle configuration."""
    if ctx is None:
        ctx = ProjectContext()
    
    if not ctx.config_path.exists():
        raise FileNotFoundError(f"Configuration not found at {ctx.config_path}")
    
    with ctx.config_path.open() as f:
        data = yaml.safe_load(f)
    return BundleConfig(**data)


def save_config(config: BundleConfig, ctx: Optional[ProjectContext] = None) -> None:
    """Save bundle configuration."""
    if ctx is None:
        ctx = ProjectContext.init()
    
    ctx.config_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.config_path.open("w") as f:
        yaml.safe_dump(config.model_dump(), f, default_flow_style=False)


def load_tracked(ctx: Optional[ProjectContext] = None) -> TrackedFiles:
    """Load tracked files list."""
    if ctx is None:
        ctx = ProjectContext()
    
    if not ctx.tracked_path.exists():
        return TrackedFiles()
    
    with ctx.tracked_path.open() as f:
        lines = [line.strip() for line in f if line.strip()]
    return TrackedFiles(files=set(lines))


def save_tracked(tracked: TrackedFiles, ctx: Optional[ProjectContext] = None) -> None:
    """Save tracked files list."""
    if ctx is None:
        ctx = ProjectContext()
    
    ctx.tracked_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.tracked_path.open("w") as f:
        for file in sorted(tracked.files):
            f.write(f"{file}\n")


def load_state(ctx: Optional[ProjectContext] = None) -> SyncState:
    """Load sync state."""
    if ctx is None:
        ctx = ProjectContext()
    
    if not ctx.state_path.exists():
        return SyncState()
    
    with ctx.state_path.open() as f:
        data = json.load(f)
    return SyncState(**data)


def save_state(state: SyncState, ctx: Optional[ProjectContext] = None) -> None:
    """Save sync state."""
    if ctx is None:
        ctx = ProjectContext()
    
    ctx.state_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.state_path.open("w") as f:
        json.dump(state.model_dump(), f, indent=2)


# ============= Diff Operations =============

def compute_diff(
    local: TrackedFilesSnapshot,
    remote: RemoteState,
    last_sync: SyncState,
    missing_local: Optional[Set[str]] = None
) -> DiffResult:
    """Compute differences between local and remote states."""
    changes = []
    missing_local = missing_local or set()
    
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


# ============= Push Operation =============

def push(
    config: BundleConfig,
    tracked: TrackedFiles,
    tag: Optional[str] = None,
    ctx: Optional[ProjectContext] = None
) -> str:
    """Execute push operation and return manifest digest."""
    if ctx is None:
        ctx = ProjectContext()
    
    # Scan working tree
    working = TrackedFilesSnapshot.scan(tracked.files, ctx.root)
    
    # Get remote state
    adapter = OrasAdapter()
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
        remote_exists = True
    except Exception:
        # Registry might be empty or tag doesn't exist
        remote = RemoteState(manifest_digest="", files={})
        remote_exists = False
    
    # Load sync state
    state = load_state(ctx)
    
    # Compute diff
    # If pushing to a new tag (remote doesn't exist), don't use sync state for comparison
    # as sync state is from default tag pushes
    if not remote_exists:
        # New tag - treat all files as needing upload
        diff = compute_diff(working, remote, SyncState())  # Empty sync state
    else:
        # Existing tag - use sync state for optimization
        diff = compute_diff(working, remote, state)
    
    # Create push plan
    plan = diff.to_push_plan()
    
    if not plan.files_to_upload and remote_exists:
        return remote.manifest_digest  # Nothing to push (only if remote exists)
    
    # Execute push - pass ALL manifest files, not just changed ones!
    # ORAS will create manifest with exactly these files
    manifest_digest = adapter.push_files(
        config.registry_ref,
        plan.manifest_files,  # CRITICAL: Full manifest, not just files_to_upload!
        tag or config.default_tag,
        config.artifact_type,
        ctx=ctx
    )
    
    # Update sync state
    state.update_after_push(manifest_digest, working)
    save_state(state, ctx)
    
    return manifest_digest


# ============= Pull Operation =============

def pull(
    config: BundleConfig,
    tracked: TrackedFiles,
    tag: Optional[str] = None,
    overwrite: bool = False,
    ctx: Optional[ProjectContext] = None
) -> PullPlan:
    """Execute pull operation and return executed plan."""
    if ctx is None:
        ctx = ProjectContext()
    
    # Scan working tree
    working = TrackedFilesSnapshot.scan(tracked.files, ctx.root)
    
    # Get remote state
    adapter = OrasAdapter()
    remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    
    # Load sync state
    state = load_state(ctx)
    
    # Compute diff
    diff = compute_diff(working, remote, state)
    
    # Create pull plan
    plan = diff.to_pull_plan(overwrite)
    
    if plan.conflicts and not overwrite:
        # Don't execute if there are conflicts - require explicit --overwrite
        raise ValueError(f"Pull would overwrite {len(plan.conflicts)} local changes. Use --overwrite to force.")
    
    if not plan.files_to_download and not plan.files_to_delete_local:
        return plan  # Nothing to change
    
    # Execute pull
    if plan.files_to_download:
        adapter.pull_files(config.registry_ref, tag or config.default_tag, ctx.root, ctx=ctx)
    
    # Delete local files if requested (DELETED_REMOTE with overwrite=True)
    for path in plan.files_to_delete_local:
        file_path = ctx.root / path
        if file_path.exists():
            file_path.unlink()
        # Remove from tracked files
        tracked.remove([Path(path)])
    
    # Update sync state
    state.update_after_pull(remote.manifest_digest, plan.files_to_download)
    save_state(state, ctx)
    
    # Update tracked files with new files from remote
    for file_info in plan.files_to_download:
        tracked.add(Path(file_info.path))
    save_tracked(tracked, ctx)
    
    return plan


# ============= Utilities =============

def _get_timestamp() -> float:
    """Get current timestamp."""
    import time
    return time.time()
