"""High-level service layer for bundle operations."""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from modelops_contracts import AuthProvider


from .context import ProjectContext
from .core import (
    BundleConfig,
    ChangeType,
    PullPreview,
    PullResult,
    PushPlan,
    SyncState,
    TrackedFiles,
)
from .ops import (
    load_config,
    load_state,
    load_tracked,
    save_tracked,
    pull_preview,
    pull_apply,
    push_plan,
    push_apply,
)
from .oras import OrasAdapter
from .policy import StoragePolicy
from .service_types import (
    AddResult,
    ChangeInfo,
    ProgressCallback,
    PushResult,
    RemoveResult,
    StatusReport,
)
from .storage import make_blob_store
from .storage_models import StorageType
from .working_state import TrackedWorkingState


@dataclass
class BundleDeps:
    """Dependency injection container for testability."""
    ctx: ProjectContext
    adapter: OrasAdapter
    now: Callable[[], float] = time.time
    blob_store_factory: Callable = make_blob_store


class BundleService:
    """High-level service for bundle operations.
    
    This service provides a clean API for bundle operations with:
    - Explicit plan/apply pattern for race-free operations
    - Fresh state loading (no caching of mutable state)
    - Dependency injection for testability
    - Progress callback support for custom UIs
    """
    
    def __init__(
        self,
        auth_provider: Optional[AuthProvider] = None,
        deps: Optional[BundleDeps] = None
    ):
        """Initialize with auth provider OR deps.

        Args:
            auth_provider: Auth provider for registry/storage operations
            deps: Full dependency injection (for testing)
        """
        if deps is None:
            ctx = ProjectContext()

            # Always load config to get registry_ref
            config = load_config(ctx)

            # Use provided auth or get from local module
            if auth_provider is None:
                from .auth import get_auth_provider
                # Get auth based on environment config
                auth_provider = get_auth_provider(config.registry_ref)

            deps = BundleDeps(
                ctx=ctx,
                adapter=OrasAdapter(auth_provider=auth_provider, registry_ref=config.registry_ref)
            )
        self.deps = deps

    
    # Core properties (loaded fresh each time)
    @property
    def config(self) -> BundleConfig:
        """Load current bundle configuration."""
        return load_config(self.deps.ctx)
    
    @property
    def tracked(self) -> TrackedFiles:
        """Load current tracked files."""
        return load_tracked(self.deps.ctx)
    
    @property
    def state(self) -> SyncState:
        """Load current sync state."""
        return load_state(self.deps.ctx)
    
    # === Plan/Apply Pattern (explicit two-phase operations) ===
    
    def plan_push(self, tag: Optional[str] = None) -> PushPlan:
        """Plan a push operation (phase 1).
        
        Args:
            tag: Tag to push to (uses default if None)
            
        Returns:
            PushPlan ready for apply_push
            
        Raises:
            NoChangesToPush: If no local changes to push
        """
        return push_plan(
            self.config,
            self.tracked,
            tag=tag,
            ctx=self.deps.ctx
        )
    
    def apply_push(
        self,
        plan: PushPlan,
        *,
        force: bool = False,
        progress: Optional[ProgressCallback] = None
    ) -> PushResult:
        """Apply a push plan (phase 2).
        
        Args:
            plan: Push plan from plan_push
            force: Force push even if tag moved
            progress: Optional progress callback
            
        Returns:
            PushResult with operation details
            
        Raises:
            TagMovedError: If tag moved and force=False
        """
        # TODO: Add progress callback support
        manifest_digest = push_apply(self.config, plan, force=force, ctx=self.deps.ctx)
        
        # Wrap in PushResult
        return PushResult(
            manifest_digest=manifest_digest,
            tag=plan.tag,
            files_pushed=len(plan.manifest_files),
            bytes_uploaded=sum(f.size for f in plan.manifest_files),
            summary=f"Pushed {len(plan.manifest_files)} files to {plan.tag}"
        )
    
    def plan_pull(
        self,
        reference: Optional[str] = None,
        *,
        overwrite: bool = False
    ) -> PullPreview:
        """Plan a pull operation (phase 1).
        
        Args:
            reference: Tag or digest to pull (uses default if None)
            overwrite: Whether to overwrite local changes
            
        Returns:
            PullPreview ready for apply_pull
        """
        return pull_preview(
            self.config,
            self.tracked,
            reference=reference,
            overwrite=overwrite,
            ctx=self.deps.ctx
        )
    
    def apply_pull(
        self,
        preview: PullPreview,
        progress: Optional[ProgressCallback] = None
    ) -> PullResult:
        """Apply a pull preview (phase 2).
        
        Args:
            preview: Pull preview from plan_pull
            progress: Optional progress callback
            
        Returns:
            PullResult with operation details
        """
        # TODO: Add progress callback support
        return pull_apply(
            self.config,
            self.tracked,
            preview,
            ctx=self.deps.ctx
        )
    
    # === High-level Operations ===
    
    def sync_status(self, reference: Optional[str] = None) -> StatusReport:
        """Get comprehensive sync status.
        
        Args:
            reference: Remote reference to compare against
            
        Returns:
            StatusReport with local/remote differences
        """
        # Load working state
        working_state = TrackedWorkingState.from_tracked(self.tracked, self.deps.ctx)
        
        # Try to get remote state
        remote = None
        resolved_ref = None
        try:
            ref = reference or self.config.default_tag
            resolved_ref = self.deps.adapter.resolve_tag_to_digest(
                self.config.registry_ref, ref
            )
            remote = self.deps.adapter.get_remote_state(
                self.config.registry_ref, resolved_ref
            )
        except Exception:
            # Remote doesn't exist or network error
            pass
        
        # Compute differences
        if remote:
            diff = working_state.compute_diff(remote, self.state)
            
            # Convert to service types - separate local and remote changes
            local_changes = []
            remote_changes = []
            
            for change in diff.changes:
                change_info = ChangeInfo(
                    path=change.path,
                    change_type=change.change_type.value,
                    size=change.local.size if change.local else (change.remote.size if change.remote else 0)
                )
                
                if change.change_type in [ChangeType.ADDED_LOCAL, ChangeType.MODIFIED_LOCAL, ChangeType.DELETED_LOCAL]:
                    local_changes.append(change_info)
                elif change.change_type in [ChangeType.ADDED_REMOTE, ChangeType.MODIFIED_REMOTE, ChangeType.DELETED_REMOTE]:
                    remote_changes.append(change_info)
                elif change.change_type == ChangeType.CONFLICT:
                    # Conflicts are both local and remote changes
                    local_changes.append(change_info)
                    remote_changes.append(change_info)
            
            # Check for conflicts
            conflicts = [change.path for change in diff.changes if change.change_type == ChangeType.CONFLICT]
            
            return StatusReport(
                local_changes=local_changes,
                remote_changes=remote_changes,
                conflicts=conflicts,
                local_only=[c.path for c in local_changes if c.change_type == "ADDED_LOCAL"],
                remote_only=[c.path for c in remote_changes if c.change_type == "ADDED_REMOTE"],
                up_to_date=len(diff.changes) == 0 or all(c.change_type == ChangeType.UNCHANGED for c in diff.changes),
                summary=f"{len(local_changes)} local, {len(remote_changes)} remote changes"
            )
        else:
            # No remote - just local status
            local_changes = []
            for path, info in working_state.files.items():
                if path in self.state.last_synced_files:
                    if info.digest != self.state.last_synced_files[path]:
                        local_changes.append(
                            ChangeInfo(
                                path=path,
                                change_type="modified",
                                size=info.size,
                                digest=info.digest
                            )
                        )
                else:
                    local_changes.append(
                        ChangeInfo(
                            path=path,
                            change_type="added",
                            size=info.size,
                            digest=info.digest
                        )
                    )
            
            # Check for deletions
            for path in self.state.last_synced_files:
                if path not in working_state.files:
                    local_changes.append(
                        ChangeInfo(path=path, change_type="deleted")
                    )
            
            return StatusReport(
                local_changes=local_changes,
                remote_changes=[],
                conflicts=[],
                local_only=[c.path for c in local_changes if c.change_type != "deleted"],
                remote_only=[],
                up_to_date=len(local_changes) == 0,
                summary=f"{len(local_changes)} local changes (no remote)"
            )
    
    def add_files(self, patterns: List[str], recursive: bool = False) -> AddResult:
        """Add files to tracking.
        
        Args:
            patterns: File patterns to add
            recursive: Whether to add directories recursively
            
        Returns:
            AddResult with operation details
        """
        tracked = self.tracked
        added = []
        already_tracked = []
        ignored = []
        total_size = 0
        
        for pattern in patterns:
            path = Path(pattern)
            
            # Resolve pattern to actual files
            if path.is_absolute():
                # Convert to relative
                try:
                    rel_path = path.relative_to(self.deps.ctx.root)
                except ValueError:
                    ignored.append(str(path))
                    continue
                paths = [str(rel_path)]
            elif (self.deps.ctx.root / path).is_dir() and recursive:
                # Add directory recursively
                dir_path = self.deps.ctx.root / path
                paths = []
                for file_path in dir_path.rglob("*"):
                    if file_path.is_file():
                        rel = file_path.relative_to(self.deps.ctx.root)
                        paths.append(str(rel))
            elif (self.deps.ctx.root / path).exists():
                # Single file
                paths = [pattern]
            else:
                # Pattern matching
                import glob
                paths = glob.glob(pattern, root_dir=self.deps.ctx.root)
            
            # Process each resolved path
            for path_str in paths:
                if path_str in tracked.files:
                    already_tracked.append(path_str)
                elif self.deps.ctx.should_ignore(path_str):
                    ignored.append(path_str)
                else:
                    file_path = self.deps.ctx.root / path_str
                    if file_path.is_file():
                        tracked.add(path_str)
                        added.append(path_str)
                        total_size += file_path.stat().st_size
        
        # Save if changes were made
        if added:
            save_tracked(tracked, self.deps.ctx)
        
        return AddResult(
            added=added,
            already_tracked=already_tracked,
            ignored=ignored,
            total_size=total_size
        )
    
    def remove_files(self, patterns: List[str]) -> RemoveResult:
        """Remove files from tracking.
        
        Args:
            patterns: File patterns to remove
            
        Returns:
            RemoveResult with operation details
        """
        tracked = self.tracked
        removed = []
        not_tracked = []
        
        for pattern in patterns:
            # Match against tracked files
            import fnmatch
            matched = False
            for tracked_path in list(tracked.files):
                if fnmatch.fnmatch(tracked_path, pattern) or tracked_path == pattern:
                    tracked.remove(tracked_path)
                    removed.append(tracked_path)
                    matched = True
            
            if not matched:
                not_tracked.append(pattern)
        
        # Save if changes were made
        if removed:
            save_tracked(tracked, self.deps.ctx)
        
        return RemoveResult(removed=removed, not_tracked=not_tracked)
    
    # === Convenience Methods (sugar over plan/apply) ===
    
    def push(
        self,
        tag: Optional[str] = None,
        force: bool = False,
        progress: Optional[ProgressCallback] = None
    ) -> PushResult:
        """Push bundle to registry (plan and apply in one call).
        
        Args:
            tag: Tag to push to
            force: Force push even if tag moved
            progress: Optional progress callback
            
        Returns:
            PushResult with operation details
        """
        plan = self.plan_push(tag)
        return self.apply_push(plan, force=force, progress=progress)
    
    def pull(
        self,
        reference: Optional[str] = None,
        overwrite: bool = False,
        progress: Optional[ProgressCallback] = None
    ) -> PullResult:
        """Pull bundle from registry (plan and apply in one call).
        
        Args:
            reference: Tag or digest to pull
            overwrite: Whether to overwrite local changes
            progress: Optional progress callback
            
        Returns:
            PullResult with operation details
        """
        preview = self.plan_pull(reference, overwrite=overwrite)
        return self.apply_pull(preview, progress=progress)


def initialize_bundle(
    project_name: str,
    env_name: str = "local",
    tag: str = "latest",
    threshold_mb: int = 50,
) -> BundleConfig:
    """Initialize a bundle configuration from an environment.

    Args:
        project_name: Name of the project/model
        env_name: Environment to load from ~/.modelops/bundle-env/
        tag: Default tag for the bundle
        threshold_mb: Size threshold in MB for blob storage

    Returns:
        BundleConfig ready to be saved

    Raises:
        ValueError: If environment doesn't exist or is invalid
    """
    from modelops_contracts import BundleEnvironment

    # Load environment configuration
    try:
        environment = BundleEnvironment.load(env_name)
    except FileNotFoundError:
        raise ValueError(
            f"Environment '{env_name}' not found. "
            f"Available environments are in ~/.modelops/bundle-env/\n"
            f"Run 'make start' to set up local environment, or "
            f"'mops infra up' to set up cloud environment."
        )

    # Extract registry from environment
    if not environment.registry:
        raise ValueError(f"Environment '{env_name}' has no registry configured")

    registry = environment.registry.login_server

    # Build full registry reference with project name
    if "/" not in registry or registry.endswith("/"):
        registry_ref = f"{registry.rstrip('/')}/{project_name}"
    else:
        # Registry already includes project/repo, use as-is
        registry_ref = registry

    # Configure storage from environment
    if environment.storage:
        storage_policy = StoragePolicy(
            provider=environment.storage.provider,
            container=environment.storage.container,
            prefix="",
            threshold_bytes=threshold_mb * 1024 * 1024,
            mode="auto"
        )
    else:
        # OCI-only mode (no external storage)
        storage_policy = StoragePolicy(
            provider="",
            container="",
            prefix="",
            threshold_bytes=threshold_mb * 1024 * 1024,
            mode="oci-inline"  # Force all to OCI
        )

    return BundleConfig(
        environment=env_name,
        registry_ref=registry_ref,
        default_tag=tag,
        storage=storage_policy
    )



