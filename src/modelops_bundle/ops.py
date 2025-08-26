"""Core operations for modelops-bundle."""

from pathlib import Path
from typing import List, Optional
import json
import time
import yaml

from .context import ProjectContext

from .core import (
    BundleConfig,
    PullPreview,
    PullResult,
    PushPlan,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from .oras import OrasAdapter
from .working_state import TrackedWorkingState


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
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
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
    
    # Compute diff with automatic deletion handling
    # If pushing to a new tag (remote doesn't exist), don't use sync state for comparison
    # as sync state is from default tag pushes
    diff = working_state.compute_diff(remote, state if remote_exists else SyncState())
    
    # Create push plan
    plan = diff.to_push_plan()
    
    # Only skip if the remote manifest already matches exactly what we'd produce
    if remote_exists and not plan.files_to_upload:
        local_manifest = {(f.path, f.digest) for f in plan.manifest_files}
        remote_manifest = {(p, fi.digest) for p, fi in remote.files.items()}
        if local_manifest == remote_manifest:
            return remote.manifest_digest
        # else: proceed to push so the manifest is replaced (e.g., to drop remote-only files)
    
    # Execute push - pass ALL manifest files, not just changed ones!
    # ORAS will create manifest with exactly these files
    # Sort for deterministic manifest order (helps stable digest comparisons across runs)
    files_for_push = sorted(plan.manifest_files, key=lambda f: f.path)
    manifest_digest = adapter.push_files(
        config.registry_ref,
        files_for_push,
        tag or config.default_tag,
        config.artifact_type,
        ctx=ctx
    )
    
    # Update sync state
    state.update_after_push(manifest_digest, working_state.snapshot)
    save_state(state, ctx)
    
    return manifest_digest


# ============= Pull Operation =============

def pull(
    config: BundleConfig,
    tracked: TrackedFiles,
    tag: Optional[str] = None,
    overwrite: bool = False,
    ctx: Optional[ProjectContext] = None
) -> PullResult:
    """Execute pull operation and return result."""
    if ctx is None:
        ctx = ProjectContext()
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Get remote state
    adapter = OrasAdapter()
    remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
    
    # Load sync state
    state = load_state(ctx)
    
    # Compute diff with automatic deletion handling
    diff = working_state.compute_diff(remote, state)
    
    # CRITICAL: Check for untracked file collisions BEFORE creating preview
    # Pull will write ALL remote files, potentially overwriting untracked local files
    # BUT: Ignored files can be overwritten without warning (like .pyc, node_modules, etc.)
    untracked_collisions = []
    for path in remote.files:
        local_path = ctx.root / path
        if local_path.exists() and path not in tracked.files:
            # Only count as collision if NOT ignored
            # Ignored untracked files can be overwritten safely
            if not ctx.should_ignore(path):
                untracked_collisions.append(path)
    
    # Generate preview of what would happen
    preview = diff.to_pull_preview(overwrite)
    
    # Add untracked collisions to preview if overwrite is enabled
    if overwrite and untracked_collisions:
        preview.will_overwrite_untracked = untracked_collisions
    
    # Safety guards: check for potentially destructive changes
    from .core import ChangeType
    
    # Find all potentially destructive changes
    local_mods = [c.path for c in diff.changes if c.change_type == ChangeType.MODIFIED_LOCAL]
    remote_deletes = [c.path for c in diff.changes if c.change_type == ChangeType.DELETED_REMOTE]
    conflicts = [c.path for c in diff.changes if c.change_type == ChangeType.CONFLICT]
    
    # Block pull without --overwrite if any destructive changes would occur
    if not overwrite and (conflicts or local_mods or remote_deletes or untracked_collisions):
        error_parts = []
        if conflicts:
            error_parts.append(f"{len(conflicts)} conflicts")
        if local_mods:
            error_parts.append(f"{len(local_mods)} locally modified")
        if remote_deletes:
            error_parts.append(f"{len(remote_deletes)} would be deleted")
        if untracked_collisions:
            error_parts.append(f"{len(untracked_collisions)} untracked files would be overwritten")
        
        raise ValueError(
            f"Pull would overwrite or delete local changes: {', '.join(error_parts)}. "
            "Use --overwrite to force."
        )
    
    # Check if there's anything to do
    if not preview.will_update_or_add and not preview.will_delete_local:
        # Nothing to change
        return PullResult(
            downloaded=0,
            deleted=0,
            manifest_digest=remote.manifest_digest
        )
    
    # Execute full mirror pull (safe because we checked guards above)
    # This mirrors the remote state completely
    adapter.pull_files(config.registry_ref, tag or config.default_tag, ctx.root, ctx=ctx)
    
    # Delete local files if requested (DELETED_REMOTE with overwrite=True)
    deleted_count = 0
    for path in preview.will_delete_local:
        file_path = ctx.root / path
        if file_path.exists():
            file_path.unlink()
            deleted_count += 1
        # Remove from tracked files
        tracked.remove(Path(path))
    
    # Update sync state to reflect the full mirror
    # Since we did a full mirror pull, state should reflect ALL remote files
    state.last_pull_digest = remote.manifest_digest
    state.last_synced_files = {}
    for path, file_info in remote.files.items():
        # Only add files that weren't deleted locally
        if path not in preview.will_delete_local:
            state.last_synced_files[path] = file_info.digest
    state.timestamp = time.time()
    save_state(state, ctx)
    
    # Update tracked files to match remote (full mirror)
    # Rebuild tracking from scratch to match remote exactly
    tracked.files.clear()  # Clear all existing tracked files
    for path in remote.files.keys():
        if path not in preview.will_delete_local:
            tracked.add(Path(path))
    save_tracked(tracked, ctx)
    
    # Return result of what actually happened
    return PullResult(
        downloaded=len(remote.files),  # We downloaded all remote files (mirror)
        deleted=deleted_count,
        manifest_digest=remote.manifest_digest
    )

