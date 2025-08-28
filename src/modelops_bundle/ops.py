"""Core operations for modelops-bundle."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import json
import os
import tempfile
import time
import yaml

from .context import ProjectContext

from .core import (
    BundleConfig,
    FileInfo,
    PullPreview,
    PullResult,
    PushPlan,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from .oras import OrasAdapter
from .storage import make_blob_store
from .storage_models import BundleIndex, BundleFileEntry, StorageType
from .utils import compute_digest, get_iso_timestamp
from .working_state import TrackedWorkingState


# ============= Atomic Write Helpers =============

def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text to file with crash safety.
    
    Args:
        path: Target file path
        text: Text content to write
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create temp file in same directory
    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.tmp-",
        suffix=""
    ) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
        tmp = Path(f.name)
    
    try:
        # Atomic rename
        os.replace(tmp, path)
        
        # Fsync directory to ensure rename is durable
        dirfd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except:
        # Clean up temp file on any error
        tmp.unlink(missing_ok=True)
        raise


# ============= Internal Storage Planning =============

@dataclass
class StoragePushPlan:
    """Internal storage plan for push operations (not exposed in API)."""
    tag: str
    oci_files: List[FileInfo]      # Files to store as OCI layers
    blob_files: List[FileInfo]     # Files to store in blob storage
    all_files: List[FileInfo]      # All files (for index)
    previous_index: Optional[BundleIndex] = None  # For blob ref reuse


def _build_storage_plan(plan: PushPlan, config: BundleConfig) -> StoragePushPlan:
    """Build internal storage plan from push plan."""
    policy = config.storage
    oci_files = []
    blob_files = []
    
    for file_info in plan.manifest_files:
        file_path = Path(file_info.path)
        storage_type, should_warn = policy.classify(file_path, file_info.size)
        
        # Note: We already checked for blob requirements in push_apply
        # so should_warn should never be True here, but handle it anyway
        if should_warn:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"File {file_info.path} ({file_info.size} bytes) would benefit from blob storage "
                f"but no provider configured - storing in OCI"
            )
        
        if storage_type == StorageType.OCI:
            oci_files.append(file_info)
        else:
            blob_files.append(file_info)
    
    return StoragePushPlan(
        tag=plan.tag,
        oci_files=oci_files,
        blob_files=blob_files,
        all_files=plan.manifest_files,
        previous_index=None  # TODO: fetch previous index if exists
    )


def _build_index(
    storage_plan: StoragePushPlan,
    blob_refs: Dict[str, 'BlobReference'],
    ctx: ProjectContext
) -> BundleIndex:
    """Build BundleIndex from storage plan and uploaded blob references."""
    from .constants import BUNDLE_VERSION
    
    files = {}
    for file_info in storage_plan.all_files:
        # Determine storage type
        is_blob = any(f.path == file_info.path for f in storage_plan.blob_files)
        
        entry = BundleFileEntry(
            path=file_info.path,
            digest=file_info.digest,
            size=file_info.size,
            storage=StorageType.BLOB if is_blob else StorageType.OCI,
            blobRef=blob_refs.get(file_info.path) if is_blob else None
        )
        files[file_info.path] = entry
    
    return BundleIndex(
        version="1.0",
        created=get_iso_timestamp(),
        tool={"name": "modelops-bundle", "version": BUNDLE_VERSION},
        files=files,
        metadata={}
    )


def _index_to_remote_state(index: BundleIndex, manifest_digest: str) -> RemoteState:
    """Convert BundleIndex to RemoteState for diffing."""
    files = {}
    for path, entry in index.files.items():
        files[path] = FileInfo(
            path=path,
            digest=entry.digest,
            size=entry.size
        )
    
    return RemoteState(
        manifest_digest=manifest_digest,
        files=files
    )


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
    """Save bundle configuration atomically."""
    if ctx is None:
        ctx = ProjectContext.init()
    
    # Use atomic write for crash safety
    config_text = yaml.safe_dump(config.model_dump(), default_flow_style=False)
    _atomic_write_text(ctx.config_path, config_text)


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
    """Save tracked files list atomically."""
    if ctx is None:
        ctx = ProjectContext()
    
    # Use atomic write for crash safety
    tracked_text = "".join(f"{file}\n" for file in sorted(tracked.files))
    _atomic_write_text(ctx.tracked_path, tracked_text)


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
    """Save sync state atomically."""
    if ctx is None:
        ctx = ProjectContext()
    
    # Use atomic write for crash safety
    state_text = json.dumps(state.model_dump(), indent=2)
    _atomic_write_text(ctx.state_path, state_text)


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
    
    # Get remote state (always from index via get_remote_state)
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
    """Phase 2: Execute pull with mandatory BundleIndex."""
    if ctx is None:
        ctx = ProjectContext()
    
    from .errors import MissingIndexError, BlobProviderMissingError
    
    adapter = OrasAdapter()
    
    # ALWAYS get index (no fallback)
    try:
        index = adapter.get_index(config.registry_ref, preview.resolved_digest)
    except MissingIndexError as e:
        # Re-raise with more context
        raise MissingIndexError(
            f"{config.registry_ref}@{preview.resolved_digest[:12]}..."
        ) from e
    
    # Initialize blob store if needed
    blob_store = None
    has_blob_files = any(
        entry.storage == StorageType.BLOB 
        for entry in index.files.values()
    )
    if has_blob_files:
        if not config.storage.uses_blob_storage:
            raise BlobProviderMissingError()
        blob_store = make_blob_store(config.storage)
    
    # Map preview files to index entries
    entries_to_pull = []
    for file_info in preview.will_update_or_add:
        if file_info.path not in index.files:
            # This shouldn't happen if preview was built correctly
            raise ValueError(
                f"File {file_info.path} in preview but not in index"
            )
        entries_to_pull.append(index.files[file_info.path])
    
    # Pull selected files (with built-in verification)
    if entries_to_pull:
        adapter.pull_selected(
            registry_ref=config.registry_ref,
            digest=preview.resolved_digest,
            entries=entries_to_pull,
            output_dir=ctx.root,
            blob_store=blob_store
        )
        # Note: pull_selected now includes digest verification
    
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
    """Phase 2: Execute push with mandatory BundleIndex."""
    if ctx is None:
        ctx = ProjectContext()
    
    from .errors import BlobStorageRequiredError, TagMovedError
    
    adapter = OrasAdapter()
    
    # Check if tag has moved since plan was created
    if plan.tag_base_digest:
        current_digest = adapter.get_current_tag_digest(config.registry_ref, plan.tag)
        if current_digest != plan.tag_base_digest:
            if not force:
                raise TagMovedError(
                    plan.tag,
                    plan.tag_base_digest,
                    current_digest or "unknown"
                )
            # Log warning but proceed with force
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Tag '{plan.tag}' has moved but proceeding with --force")
    
    # Check for blob storage requirements upfront
    files_to_check = [(Path(f.path), f.size) for f in plan.manifest_files]
    needs_blob = config.storage.check_files_for_blob_requirement(files_to_check)
    if needs_blob:
        raise BlobStorageRequiredError(needs_blob)
    
    # Always use index-based push
    manifest_digest = _push_apply_with_index(config, plan, ctx)
    
    # Verify tag didn't move during push (unless forced)
    if not force:
        final_digest = adapter.get_current_tag_digest(config.registry_ref, plan.tag)
        if final_digest and final_digest != manifest_digest:
            raise TagMovedError(plan.tag, manifest_digest, final_digest)
    
    # Update sync state
    state = load_state(ctx)
    tracked_snapshot = TrackedWorkingState.from_tracked(
        TrackedFiles(files={f.path for f in plan.manifest_files}), ctx
    ).snapshot
    state.update_after_push(manifest_digest, tracked_snapshot)
    save_state(state, ctx)
    
    return manifest_digest


def _push_apply_with_index(
    config: BundleConfig,
    plan: PushPlan,
    ctx: ProjectContext
) -> str:
    """Execute push with BundleIndex and external storage support."""
    adapter = OrasAdapter()
    
    # Build internal storage plan
    storage_plan = _build_storage_plan(plan, config)
    
    # Initialize blob store if needed
    blob_store = make_blob_store(config.storage)
    blob_refs = {}
    
    # Upload blob files if any
    if storage_plan.blob_files and blob_store:
        for file_info in storage_plan.blob_files:
            file_path = ctx.root / file_info.path
            blob_ref = blob_store.put(file_info.digest, file_path)
            blob_refs[file_info.path] = blob_ref
    
    # Build index with ALL files
    index = _build_index(storage_plan, blob_refs, ctx)
    
    # Prepare OCI file paths (relative paths for the index entries)
    oci_file_paths = [(ctx.root / f.path, f.path) for f in storage_plan.oci_files]
    
    # Push with index as config
    manifest_digest = adapter.push_with_index_config(
        registry_ref=config.registry_ref,
        tag=plan.tag,
        oci_file_paths=oci_file_paths,
        index=index,
        manifest_annotations=None
    )
    
    # State is updated in push_apply, not here (keep this function pure transport)
    return manifest_digest

