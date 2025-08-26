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
    ctx: Optional[ProjectContext] = None,
    force: bool = False
) -> str:
    """Execute push operation and return manifest digest.
    
    Simple wrapper that uses two-phase push internally.
    
    Args:
        config: Bundle configuration
        tracked: Tracked files
        tag: Tag to push to (defaults to config.default_tag)
        ctx: Project context
        force: If True, push even if tag has moved (bypass race protection)
    
    Returns:
        Manifest digest of the pushed artifact
        
    Raises:
        RuntimeError: If tag has moved and force=False
    """
    if ctx is None:
        ctx = ProjectContext()
    
    # Phase 1: Create plan
    plan = push_plan(config, tracked, tag, ctx)
    
    # Optimization: if nothing changed, return existing digest
    if plan.tag_base_digest and not plan.files_to_upload and not plan.deletes:
        # Need to verify manifest would be identical
        adapter = OrasAdapter()
        try:
            remote = adapter.get_remote_state(config.registry_ref, tag or config.default_tag)
            local_manifest = {(f.path, f.digest) for f in plan.manifest_files}
            remote_manifest = {(p, fi.digest) for p, fi in remote.files.items()}
            if local_manifest == remote_manifest:
                return remote.manifest_digest  # Nothing to do
        except Exception:
            pass  # Continue with push
    
    # Phase 2: Apply with proper force parameter
    return push_apply(config, plan, force=force, ctx=ctx)


# ============= Pull Operation =============

def pull(
    config: BundleConfig,
    tracked: TrackedFiles,
    tag: Optional[str] = None,
    overwrite: bool = False,
    ctx: Optional[ProjectContext] = None
) -> PullResult:
    """Execute pull operation and return result.
    
    Simple wrapper that uses two-phase pull internally.
    """
    if ctx is None:
        ctx = ProjectContext()
    
    # Phase 1: Generate preview
    preview = pull_preview(config, tracked, tag, overwrite, ctx)
    
    # For backwards compatibility, we need to check for local modifications
    # that aren't in the preview's conflicts list
    if not overwrite:
        # Get the diff to check for MODIFIED_LOCAL changes
        adapter = OrasAdapter()
        ref = tag or config.default_tag
        resolved_digest = adapter.resolve_tag_to_digest(config.registry_ref, ref)
        remote = adapter.get_remote_state(config.registry_ref, resolved_digest)
        working_state = TrackedWorkingState.from_tracked(tracked, ctx)
        state = load_state(ctx)
        diff = working_state.compute_diff(remote, state)
        
        from .core import ChangeType
        local_mods = [c.path for c in diff.changes if c.change_type == ChangeType.MODIFIED_LOCAL]
        remote_deletes = [c.path for c in diff.changes if c.change_type == ChangeType.DELETED_REMOTE]
        
        # Safety guards: check for potentially destructive changes
        if preview.conflicts or local_mods or remote_deletes or preview.will_overwrite_untracked:
            error_parts = []
            if preview.conflicts:
                error_parts.append(f"{len(preview.conflicts)} conflicts")
            if local_mods:
                error_parts.append(f"{len(local_mods)} locally modified")
            if remote_deletes:
                error_parts.append(f"{len(remote_deletes)} would be deleted")
            if preview.will_overwrite_untracked:
                error_parts.append(f"{len(preview.will_overwrite_untracked)} untracked files would be overwritten")
            
            raise ValueError(
                f"Pull would overwrite or delete local changes: {', '.join(error_parts)}. "
                "Use --overwrite to force."
            )
    
    # Phase 2: Apply
    return pull_apply(config, tracked, preview, ctx)


# ============= Two-Phase Pull Operations =============

def pull_preview(
    config: BundleConfig,
    tracked: TrackedFiles,
    reference: Optional[str] = None,
    overwrite: bool = False,
    ctx: Optional[ProjectContext] = None
) -> PullPreview:
    """Phase 1: Generate preview with resolved digest for race-free execution."""
    if ctx is None:
        ctx = ProjectContext()
    
    # CRITICAL: Resolve tag to digest ONCE for consistency
    adapter = OrasAdapter()
    ref = reference or config.default_tag
    resolved_digest = adapter.resolve_tag_to_digest(config.registry_ref, ref)
    
    # Get remote state using resolved digest
    remote = adapter.get_remote_state(config.registry_ref, resolved_digest)
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Load sync state
    state = load_state(ctx)
    
    # Compute diff with automatic deletion handling
    diff = working_state.compute_diff(remote, state)
    
    # Check for untracked file collisions
    untracked_collisions = []
    for path in remote.files:
        local_path = ctx.root / path
        if local_path.exists() and path not in tracked.files:
            if not ctx.should_ignore(path):
                untracked_collisions.append(path)
    
    # Generate preview with resolved digest
    preview = diff.to_pull_preview(overwrite, resolved_digest, ref)
    
    # Add untracked collisions to preview (needed for safety checks)
    if untracked_collisions:
        preview.will_overwrite_untracked = untracked_collisions
    
    return preview


def pull_apply(
    config: BundleConfig,
    tracked: TrackedFiles,
    preview: PullPreview,
    ctx: Optional[ProjectContext] = None
) -> PullResult:
    """Phase 2: Execute pull using resolved digest from preview."""
    if ctx is None:
        ctx = ProjectContext()
    
    adapter = OrasAdapter()
    
    # Execute pull using DIGEST from preview, not tag!
    # This ensures we pull exactly what was previewed
    adapter.pull_files(
        registry_ref=config.registry_ref,
        reference=preview.resolved_digest,  # USE DIGEST!
        output_dir=ctx.root,
        ctx=ctx
    )
    
    # Delete local files if requested
    deleted_count = 0
    for path in preview.will_delete_local:
        file_path = ctx.root / path
        if file_path.exists():
            file_path.unlink()
            deleted_count += 1
        # Remove from tracked files
        tracked.remove(Path(path))
    
    # Update tracked files for new/updated files
    for file_info in preview.will_update_or_add:
        tracked.add(Path(file_info.path))
    
    # Save updated tracked files
    save_tracked(tracked, ctx)
    
    # Update sync state
    state = load_state(ctx)
    state.last_pull_digest = preview.resolved_digest
    state.timestamp = time.time()
    
    # Update last_synced_files with all pulled files
    for file_info in preview.will_update_or_add:
        state.last_synced_files[file_info.path] = file_info.digest
    
    # Remove deleted files from sync state
    for path in preview.will_delete_local:
        state.last_synced_files.pop(path, None)
    
    save_state(state, ctx)
    
    # Return result
    return PullResult(
        downloaded=len(preview.will_update_or_add),
        deleted=deleted_count,
        manifest_digest=preview.resolved_digest
    )


# ============= Two-Phase Push Operations =============

def push_plan(
    config: BundleConfig,
    tracked: TrackedFiles,
    tag: Optional[str] = None,
    ctx: Optional[ProjectContext] = None
) -> PushPlan:
    """Phase 1: Generate push plan with current tag digest for race detection."""
    if ctx is None:
        ctx = ProjectContext()
    
    tag = tag or config.default_tag
    
    # Capture current digest of the tag (if it exists)
    adapter = OrasAdapter()
    tag_base_digest = adapter.get_current_tag_digest(config.registry_ref, tag)
    
    # Create working state with deletion tracking
    working_state = TrackedWorkingState.from_tracked(tracked, ctx)
    
    # Get remote state if exists
    try:
        remote = adapter.get_remote_state(config.registry_ref, tag)
    except Exception:
        # Registry might be empty or tag doesn't exist
        remote = RemoteState(manifest_digest="", files={})
    
    # Load sync state
    state = load_state(ctx)
    
    # Compute diff
    diff = working_state.compute_diff(remote, state)
    
    # Generate push plan with tag tracking
    plan = diff.to_push_plan(tag, tag_base_digest)
    
    return plan


def push_apply(
    config: BundleConfig,
    plan: PushPlan,
    force: bool = False,
    ctx: Optional[ProjectContext] = None
) -> str:
    """Phase 2: Execute push and verify tag hasn't moved."""
    if ctx is None:
        ctx = ProjectContext()
    
    adapter = OrasAdapter()
    
    # Check if tag has moved since plan was created
    if plan.tag_base_digest:
        current_digest = adapter.get_current_tag_digest(config.registry_ref, plan.tag)
        if current_digest != plan.tag_base_digest:
            if not force:
                raise RuntimeError(
                    f"Tag '{plan.tag}' has moved from {plan.tag_base_digest[:12]}... "
                    f"to {current_digest[:12] if current_digest else 'unknown'}. "
                    "Use --force to override."
                )
            # Log warning but proceed with force
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Tag '{plan.tag}' has moved but proceeding with --force")
    
    # Execute push
    manifest_digest = adapter.push_files(
        registry_ref=config.registry_ref,
        files=plan.manifest_files,
        tag=plan.tag,
        ctx=ctx
    )
    
    # Update sync state
    state = load_state(ctx)
    tracked_snapshot = TrackedWorkingState.from_tracked(
        TrackedFiles(files={f.path for f in plan.manifest_files}), ctx
    ).snapshot
    state.update_after_push(manifest_digest, tracked_snapshot)
    save_state(state, ctx)
    
    return manifest_digest

