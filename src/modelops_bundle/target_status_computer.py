"""Compute target status from registry, local files, and cloud state."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from modelops_contracts import BundleRegistry, TargetEntry

from .context import ProjectContext
from .core import SyncState
from .model_state import DigestSnapshot, FileDigestState, ModelSyncState
from .target_state import (
    TargetDependencyState,
    TargetReadiness,
    TargetState,
    compute_target_digest,
)


class TargetStatusComputer:
    """Computes target states from registry and file system."""

    def __init__(self, ctx: ProjectContext, registry: BundleRegistry):
        """Initialize computer with context and registry.

        Args:
            ctx: Project context for paths and config
            registry: Bundle registry containing target entries
        """
        self.ctx = ctx
        self.registry = registry

    def compute_target_states(
        self,
        local_snapshot: DigestSnapshot,
        cloud_snapshot: Optional[DigestSnapshot],
        sync_state: SyncState,
    ) -> Dict[str, TargetState]:
        """Compute states for all registered targets.

        Args:
            local_snapshot: Local file digests
            cloud_snapshot: Cloud file digests (if available)
            sync_state: Last sync state

        Returns:
            Dictionary mapping target IDs to their states
        """
        targets = {}
        for target_id, target_entry in self.registry.targets.items():
            target_state = self._compute_target_state(
                target_id,
                target_entry,
                local_snapshot,
                cloud_snapshot,
                sync_state,
            )
            targets[target_id] = target_state
        return targets

    def _compute_target_state(
        self,
        target_id: str,
        target_entry: TargetEntry,
        local_snapshot: DigestSnapshot,
        cloud_snapshot: Optional[DigestSnapshot],
        sync_state: SyncState,
    ) -> TargetState:
        """Compute state for a single target.

        Args:
            target_id: Target identifier
            target_entry: Target registry entry
            local_snapshot: Local file digests
            cloud_snapshot: Cloud file digests (if available)
            sync_state: Last sync state

        Returns:
            Complete target state
        """
        # Get canonical path for target file
        target_file_canonical = self.ctx.to_project_relative(target_entry.path).as_posix()

        # Check target file
        target_file_state = self._check_dependency(
            target_entry.path,
            target_entry.target_digest,
            local_snapshot,
        )

        # Check data dependencies (observation files)
        data_states = []
        for data_path in target_entry.data or []:
            # Future: add data_digests to TargetEntry like ModelEntry
            # For now, no expected digest available
            state = self._check_dependency(data_path, None, local_snapshot)
            data_states.append(state)

        # Create target state
        target_state = TargetState(
            target_id=target_id,
            entrypoint=target_entry.entrypoint,
            target_file=target_file_canonical,
            model_output=target_entry.model_output,
            target_file_state=target_file_state,
            data_dependencies=data_states,
            local_readiness=TargetReadiness.UNKNOWN,
            cloud_sync_state=ModelSyncState.UNKNOWN,
        )

        # Compute target-level digests
        target_state.local_target_digest = compute_target_digest(
            target_state.dependency_paths,
            dict(local_snapshot.digests),
        )

        if cloud_snapshot:
            target_state.cloud_target_digest = compute_target_digest(
                target_state.dependency_paths,
                dict(cloud_snapshot.digests),
            )

        # Compute readiness
        target_state.local_readiness = target_state.compute_readiness()

        # Compute cloud sync state
        if cloud_snapshot:
            target_state.cloud_sync_state = self._compute_sync_state(
                target_state, local_snapshot, cloud_snapshot, sync_state
            )
        else:
            target_state.cloud_sync_state = ModelSyncState.UNTRACKED

        # Gather issues
        target_state.issues = self._gather_issues(target_state)

        return target_state

    def _check_dependency(
        self, path: str, expected: Optional[str], local_snapshot: DigestSnapshot
    ) -> TargetDependencyState:
        """Check state of a single dependency.

        Args:
            path: Path to check
            expected: Expected digest (if known)
            local_snapshot: Local file digests

        Returns:
            Dependency state
        """
        # Get canonical path
        canonical_path = self.ctx.to_project_relative(path).as_posix()

        # Get actual digest
        actual = local_snapshot.digests.get(canonical_path)

        # Get file stats if exists
        abs_path = self.ctx.absolute(path)
        size = None
        mtime = None
        if abs_path.exists():
            stat = abs_path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        # Determine state
        if actual is None:
            state = FileDigestState.MISSING
        elif expected is None:
            state = FileDigestState.UNKNOWN
        elif actual == expected:
            state = FileDigestState.CURRENT
        else:
            state = FileDigestState.MODIFIED

        return TargetDependencyState(
            path=canonical_path,
            expected_digest=expected,
            actual_digest=actual,
            file_state=state,
            size=size,
            last_modified=mtime,
        )

    def _compute_sync_state(
        self,
        target_state: TargetState,
        local: DigestSnapshot,
        cloud: Optional[DigestSnapshot],
        sync_state: SyncState,
    ) -> ModelSyncState:
        """Compute sync state between local and cloud.

        Uses the same algorithm as models with proper precedence:
        DIVERGED > AHEAD/BEHIND > UNTRACKED > SYNCED

        Args:
            target_state: Target to check
            local: Local digests
            cloud: Cloud digests
            sync_state: Last sync information

        Returns:
            Sync state for this target
        """
        if cloud is None:
            return ModelSyncState.UNTRACKED

        paths = target_state.dependency_paths
        s_map = sync_state.last_synced_files

        any_local_changed = False
        any_cloud_changed = False
        any_untracked = False
        any_unknown_baseline_diverge = False

        for p in paths:
            l = local.digests.get(p)
            c = cloud.digests.get(p)
            s = s_map.get(p)

            # Path in local but not cloud → candidate for ahead
            if l and not c:
                any_local_changed = True
                continue

            # Path in cloud but not local → candidate for behind
            if c and not l:
                any_cloud_changed = True
                continue

            # Path in both
            if l and c:
                # If they differ, need to check sync baseline
                if l != c:
                    # If we have sync state, see who changed
                    if s is not None:
                        if l != s:
                            any_local_changed = True
                        if c != s:
                            any_cloud_changed = True
                    else:
                        # No baseline, can't determine direction
                        any_unknown_baseline_diverge = True
                # else l == c → synced
            # else both None → no-op

        # Apply precedence rules (same as models)
        # DIVERGED takes highest priority
        if (any_local_changed and any_cloud_changed) or any_unknown_baseline_diverge:
            return ModelSyncState.DIVERGED

        # AHEAD if only local changed
        if any_local_changed:
            return ModelSyncState.AHEAD

        # BEHIND if only cloud changed
        if any_cloud_changed:
            return ModelSyncState.BEHIND

        # UNTRACKED if some paths are untracked
        if any_untracked:
            return ModelSyncState.UNTRACKED

        # Otherwise synced
        return ModelSyncState.SYNCED

    def _gather_issues(self, target_state: TargetState) -> List[str]:
        """Gather human-readable issues for a target.

        Args:
            target_state: Target to check

        Returns:
            List of issue descriptions
        """
        issues = []

        # Check for missing target file
        if target_state.target_file_state.file_state == FileDigestState.MISSING:
            issues.append(f"Target file missing: {target_state.target_file}")

        # Check for missing data dependencies
        missing_data = [
            d.path for d in target_state.data_dependencies
            if d.file_state == FileDigestState.MISSING
        ]
        if missing_data:
            issues.append(f"Missing observation file: {missing_data[0]}")
            if len(missing_data) > 1:
                issues.append(f"...and {len(missing_data) - 1} more missing files")

        # Check for modified dependencies
        if target_state.local_readiness == TargetReadiness.STALE:
            modified_count = len([
                d for d in target_state.all_dependencies
                if d.file_state == FileDigestState.MODIFIED
            ])
            issues.append(f"{modified_count} dependencies modified since registration")

        # Check cloud sync issues
        if target_state.cloud_sync_state == ModelSyncState.DIVERGED:
            issues.append("Local and cloud have conflicting changes")
        elif target_state.cloud_sync_state == ModelSyncState.BEHIND:
            issues.append("Cloud has newer version - consider pulling")

        # Check if model output exists (optional - needs registry access)
        # This would require passing the full registry to check model outputs
        # Skipping for now, can be added later

        return issues
