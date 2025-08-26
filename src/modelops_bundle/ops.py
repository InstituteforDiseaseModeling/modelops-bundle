"""Core operations for modelops-bundle."""

from pathlib import Path
from typing import List, Optional
import json
import yaml

from .context import ProjectContext

from .core import (
    BundleConfig,
    PullPlan,
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
) -> PullPlan:
    """Execute pull operation and return executed plan."""
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
        tracked.remove(Path(path))
    
    # Update sync state
    state.update_after_pull(remote.manifest_digest, plan.files_to_download)
    # Remove deleted files from sync state
    for path in plan.files_to_delete_local:
        state.last_synced_files.pop(path, None)
    save_state(state, ctx)
    
    # Update tracked files with new files from remote
    for file_info in plan.files_to_download:
        tracked.add(Path(file_info.path))
    save_tracked(tracked, ctx)
    
    return plan

