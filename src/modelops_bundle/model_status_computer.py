"""Compute model status from registry, local files, and cloud state."""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from modelops_contracts import BundleRegistry, ModelEntry

from .context import ProjectContext
from .core import SyncState
from .model_state import (
    DigestSnapshot,
    FileDigestState,
    ModelDependencyState,
    ModelReadiness,
    ModelState,
    ModelStatusSnapshot,
    ModelSyncState,
    compute_model_digest,
)
from .oras import OrasAdapter


class ModelStatusComputer:
    """Computes model states from various sources."""

    def __init__(self, ctx: ProjectContext, adapter: OrasAdapter):
        """Initialize computer with context and registry adapter.

        Args:
            ctx: Project context for paths and config
            adapter: OrasAdapter for cloud state
        """
        self.ctx = ctx
        self.adapter = adapter
        self.registry = self._load_registry()

    def _load_registry(self) -> Optional[BundleRegistry]:
        """Load model registry from project."""
        registry_path = self.ctx.storage_dir / "registry.yaml"
        if not registry_path.exists():
            return None
        return BundleRegistry.load(registry_path)

    def _load_sync_state(self) -> SyncState:
        """Load sync state from project."""
        state_path = self.ctx.storage_dir / "state.json"
        if not state_path.exists():
            return SyncState()
        return SyncState.model_validate_json(state_path.read_text())

    def compute_full_status(self, config, registry_ref: str, tag: str = "latest") -> ModelStatusSnapshot:
        """Compute complete status for all models.

        Args:
            config: Bundle configuration
            registry_ref: Registry reference
            tag: Tag to check against

        Returns:
            Complete snapshot of all model states
        """
        if not self.registry:
            # No registry means no models
            return ModelStatusSnapshot(
                timestamp=datetime.now(),
                models={},
                bundle_ref=registry_ref,
                bundle_tag=tag,
                tracked_files=set(),
                cloud_manifest_digest=None,
                cloud_file_digests={},
                cloud_timestamp=None,
            )

        # 1. Gather all dependency files
        all_files = self._gather_all_dependency_files(self.registry)

        # 2. Compute local file digests
        local_snapshot = DigestSnapshot.from_files(all_files, self.ctx)

        # 3. Get cloud state (if available)
        cloud_result = self._fetch_cloud_state(registry_ref, tag)
        cloud_manifest_digest = None
        cloud_snapshot = None
        if cloud_result:
            cloud_manifest_digest, cloud_snapshot = cloud_result

        # 4. Load sync state
        sync_state = self._load_sync_state()

        # 5. Compute per-model states
        models = {}
        for model_id, model_entry in self.registry.models.items():
            model_state = self._compute_model_state(
                model_id,
                model_entry,
                local_snapshot,
                cloud_snapshot,
                sync_state,
            )
            models[model_id] = model_state

        # 6. Get tracked files
        from .ops import load_tracked

        tracked = load_tracked(self.ctx)

        # 7. Return complete snapshot
        return ModelStatusSnapshot(
            timestamp=datetime.now(),
            models=models,
            bundle_ref=registry_ref,
            bundle_tag=tag,
            tracked_files=set(tracked.files),
            cloud_manifest_digest=cloud_manifest_digest,
            cloud_file_digests=dict(cloud_snapshot.digests) if cloud_snapshot else {},
            cloud_timestamp=cloud_snapshot.timestamp if cloud_snapshot else None,
        )

    def _gather_all_dependency_files(self, registry: BundleRegistry) -> List[Path]:
        """Gather all files that any model depends on.

        Args:
            registry: Model registry

        Returns:
            List of paths to check
        """
        all_paths = set()
        for model_entry in registry.models.values():
            # Add model file
            all_paths.add(Path(model_entry.path))

            # Add data dependencies
            for data_path in model_entry.data or []:
                all_paths.add(Path(data_path))

            # Add code dependencies
            for code_path in model_entry.code or []:
                all_paths.add(Path(code_path))

        # Convert to absolute paths
        return [self.ctx.absolute(p) for p in all_paths]

    def _fetch_cloud_state(self, registry_ref: str, tag: str) -> Optional[Tuple[str, DigestSnapshot]]:
        """Fetch cloud state from registry.

        Args:
            registry_ref: Registry reference
            tag: Tag to fetch

        Returns:
            Tuple of (manifest_digest, DigestSnapshot) or None if unavailable
        """
        try:
            # Get manifest digest
            digest = self.adapter.resolve_tag_to_digest(registry_ref, tag)

            # Get index with file info
            index = self.adapter.get_index(registry_ref, digest)

            # Convert to digest snapshot
            digests = {}
            for path, entry in index.files.items():
                # Store with canonical paths
                digests[path] = entry.digest

            snapshot = DigestSnapshot(
                timestamp=datetime.now(),  # Could extract from manifest annotations
                digests=digests,
            )
            return digest, snapshot
        except Exception:
            # Cloud unavailable
            return None

    def _compute_model_state(
        self,
        model_id: str,
        model_entry: ModelEntry,
        local_snapshot: DigestSnapshot,
        cloud_snapshot: Optional[DigestSnapshot],
        sync_state: SyncState,
    ) -> ModelState:
        """Compute state for a single model.

        Args:
            model_id: Model identifier
            model_entry: Model registry entry
            local_snapshot: Local file digests
            cloud_snapshot: Cloud file digests (if available)
            sync_state: Last sync state

        Returns:
            Complete model state
        """
        # Get canonical path for model file
        model_file_canonical = self.ctx.to_project_relative(model_entry.path).as_posix()

        # Check model file
        model_file_state = self._check_dependency(
            model_entry.path,
            model_entry.model_digest,
            local_snapshot,
        )

        # Check data dependencies
        data_states = []
        for data_path in model_entry.data or []:
            # Use string representation of path as key
            path_key = str(data_path)
            expected_digest = model_entry.data_digests.get(path_key) if model_entry.data_digests else None
            state = self._check_dependency(data_path, expected_digest, local_snapshot)
            data_states.append(state)

        # Check code dependencies
        code_states = []
        for code_path in model_entry.code or []:
            # Use string representation of path as key
            path_key = str(code_path)
            expected_digest = model_entry.code_digests.get(path_key) if model_entry.code_digests else None
            state = self._check_dependency(code_path, expected_digest, local_snapshot)
            code_states.append(state)

        # Create model state
        model_state = ModelState(
            model_id=model_id,
            name=model_entry.class_name,
            entrypoint=model_entry.entrypoint,
            model_file=model_file_canonical,
            model_file_state=model_file_state,
            data_dependencies=data_states,
            code_dependencies=code_states,
            local_readiness=ModelReadiness.UNKNOWN,
            cloud_sync_state=ModelSyncState.UNKNOWN,
        )

        # Compute model-level digests
        model_state.local_model_digest = compute_model_digest(
            model_state.dependency_paths,
            dict(local_snapshot.digests),
        )

        if cloud_snapshot:
            model_state.cloud_model_digest = compute_model_digest(
                model_state.dependency_paths,
                dict(cloud_snapshot.digests),
            )

        # Compute readiness
        model_state.local_readiness = model_state.compute_readiness()

        # Compute cloud sync state
        if cloud_snapshot:
            model_state.cloud_sync_state = self._compute_sync_state(
                model_state, local_snapshot, cloud_snapshot, sync_state
            )
        else:
            model_state.cloud_sync_state = ModelSyncState.UNTRACKED

        # Gather issues
        model_state.issues = self._gather_issues(model_state)

        return model_state

    def _check_dependency(
        self, path: str, expected: Optional[str], local_snapshot: DigestSnapshot
    ) -> ModelDependencyState:
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
            mtime = datetime.fromtimestamp(stat.st_mtime)

        # Determine state
        if actual is None:
            state = FileDigestState.MISSING
        elif expected is None:
            state = FileDigestState.UNKNOWN
        elif actual == expected:
            state = FileDigestState.CURRENT
        else:
            state = FileDigestState.MODIFIED

        return ModelDependencyState(
            path=canonical_path,
            expected_digest=expected,
            actual_digest=actual,
            file_state=state,
            size=size,
            last_modified=mtime,
        )

    def _compute_sync_state(
        self,
        model_state: ModelState,
        local: DigestSnapshot,
        cloud: Optional[DigestSnapshot],
        sync_state: SyncState,
    ) -> ModelSyncState:
        """Compute sync state between local and cloud.

        Uses the corrected algorithm with proper precedence:
        DIVERGED > AHEAD/BEHIND > UNTRACKED > SYNCED

        Args:
            model_state: Model to check
            local: Local digests
            cloud: Cloud digests
            sync_state: Last sync information

        Returns:
            Sync state for this model
        """
        if cloud is None:
            return ModelSyncState.UNTRACKED

        paths = model_state.dependency_paths
        s_map = sync_state.last_synced_files

        any_local_changed = False
        any_cloud_changed = False
        any_untracked = False
        any_unknown_baseline_diverge = False

        for p in paths:
            l = local.digests.get(p)
            c = cloud.digests.get(p)
            s = s_map.get(p)

            if l == c:
                # Equal (including both None)
                continue

            if s is None:
                # No baseline (never pushed/tracked individually)
                if l and not c:
                    any_untracked = True  # New local file
                elif not l and c:
                    any_cloud_changed = True  # Cloud-only file
                elif l and c and l != c:
                    any_unknown_baseline_diverge = True  # Both exist, differ
                continue

            # We have a baseline
            if l != s:
                any_local_changed = True
            if c != s:
                any_cloud_changed = True

        # Apply precedence
        if any_unknown_baseline_diverge or (any_local_changed and any_cloud_changed):
            return ModelSyncState.DIVERGED
        if any_local_changed and not any_cloud_changed:
            return ModelSyncState.AHEAD
        if any_cloud_changed and not any_local_changed:
            return ModelSyncState.BEHIND
        if any_untracked:
            return ModelSyncState.UNTRACKED
        return ModelSyncState.SYNCED

    def _gather_issues(self, model_state: ModelState) -> List[str]:
        """Gather human-readable issues for a model.

        Args:
            model_state: Model to check

        Returns:
            List of issue descriptions
        """
        issues = []

        # Check for missing files
        missing_deps = []
        modified_deps = []

        for dep in model_state.all_dependencies:
            if dep.file_state == FileDigestState.MISSING:
                missing_deps.append(dep.path)
            elif dep.file_state == FileDigestState.MODIFIED:
                modified_deps.append(dep.path)

        if missing_deps:
            if len(missing_deps) == 1:
                issues.append(f"Missing file: {missing_deps[0]}")
            else:
                issues.append(f"Missing {len(missing_deps)} files: {', '.join(missing_deps[:3])}")

        if modified_deps:
            if len(modified_deps) == 1:
                issues.append(f"Modified: {modified_deps[0]}")
            else:
                issues.append(f"Modified {len(modified_deps)} files")

        # Check sync state
        if model_state.cloud_sync_state == ModelSyncState.DIVERGED:
            issues.append("Local and cloud have diverged - manual resolution needed")
        elif model_state.cloud_sync_state == ModelSyncState.BEHIND:
            issues.append("Cloud has newer version - run 'pull' to update")
        elif model_state.cloud_sync_state == ModelSyncState.AHEAD:
            issues.append("Local changes not pushed - run 'push' to sync")

        return issues