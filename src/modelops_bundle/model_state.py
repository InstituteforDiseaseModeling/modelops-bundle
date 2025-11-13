"""Model state management for tracking model readiness and sync status."""

import hashlib
import types
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Tuple, Union

from .context import ProjectContext
from .core import FileInfo


class FileDigestState(Enum):
    """State of a file's digest comparison."""
    CURRENT = "current"      # Digest matches expected
    MODIFIED = "modified"    # Digest differs from expected
    MISSING = "missing"      # File doesn't exist
    UNKNOWN = "unknown"      # No expected digest to compare


class ModelSyncState(Enum):
    """Model's sync state with cloud."""
    SYNCED = "synced"        # All files match cloud
    AHEAD = "ahead"          # Local changes not in cloud
    BEHIND = "behind"        # Cloud has newer version
    DIVERGED = "diverged"    # Both local and cloud changed
    UNTRACKED = "untracked"  # Never pushed to cloud
    UNKNOWN = "unknown"      # Can't determine state


class ModelReadiness(Enum):
    """Whether model can run."""
    READY = "ready"          # All deps present and valid
    STALE = "stale"          # Some deps modified but present
    BROKEN = "broken"        # Required deps missing
    UNKNOWN = "unknown"      # Can't determine state


@dataclass(frozen=True)
class DigestSnapshot:
    """Immutable snapshot of file digests at a point in time."""
    timestamp: datetime
    digests: Mapping[str, str]  # path -> sha256:xxxx

    def __post_init__(self):
        """Ensure immutability by converting dict to MappingProxyType."""
        if isinstance(self.digests, dict):
            object.__setattr__(self, 'digests',
                             types.MappingProxyType(self.digests))

    @classmethod
    def from_files(cls, paths: List[Path], ctx: ProjectContext) -> "DigestSnapshot":
        """Compute current digests for files.

        Args:
            paths: List of paths to compute digests for
            ctx: Project context for path canonicalization

        Returns:
            DigestSnapshot with current file digests
        """
        from .hashing import compute_file_digest

        digests = {}
        for path in paths:
            if path.exists():
                # Canonicalize path for consistent keys
                canonical = ctx.to_project_relative(path).as_posix()
                digests[canonical] = compute_file_digest(path)

        return cls(timestamp=datetime.now(), digests=digests)

    def compare_against_expected(self, expected: "DigestSnapshot") -> Dict[str, FileDigestState]:
        """Compare this (local) snapshot against expected digests.

        This method assumes self is the "current/local" state and other is "expected".

        Args:
            expected: The expected digest snapshot

        Returns:
            Dict mapping paths to their digest states
        """
        result = {}
        all_paths = set(self.digests.keys()) | set(expected.digests.keys())

        for path in all_paths:
            if path not in self.digests:
                # File missing locally
                result[path] = FileDigestState.MISSING
            elif path not in expected.digests:
                # No expected digest (new file)
                result[path] = FileDigestState.UNKNOWN
            elif self.digests[path] == expected.digests[path]:
                # Matches expected
                result[path] = FileDigestState.CURRENT
            else:
                # Different from expected
                result[path] = FileDigestState.MODIFIED

        return result


@dataclass
class ModelDependencyState:
    """State of a single model dependency."""
    path: str  # Canonical POSIX path
    expected_digest: Optional[str]
    actual_digest: Optional[str]
    file_state: FileDigestState
    size: Optional[int]
    last_modified: Optional[datetime]

    @property
    def is_valid(self) -> bool:
        """Check if dependency is valid (exists and matches if digest known).

        A file is valid if:
        - It exists (actual_digest is not None) AND
        - Either matches expected (CURRENT) or has no expected digest (UNKNOWN)
        """
        return (self.actual_digest is not None and
                self.file_state in (FileDigestState.CURRENT, FileDigestState.UNKNOWN))


@dataclass
class ModelState:
    """Complete state of a model including all dependencies."""
    model_id: str
    name: str
    entrypoint: str
    model_file: str  # Canonical path

    # Dependency states
    model_file_state: ModelDependencyState
    data_dependencies: List[ModelDependencyState]
    code_dependencies: List[ModelDependencyState]

    # Computed states
    local_readiness: ModelReadiness
    cloud_sync_state: ModelSyncState

    # Model-level digests for fast comparison
    local_model_digest: Optional[str] = None
    cloud_model_digest: Optional[str] = None

    # Cloud info
    cloud_timestamp: Optional[datetime] = None
    last_push_digest: Optional[str] = None

    # Issues found
    issues: List[str] = field(default_factory=list)

    @property
    def all_dependencies(self) -> List[ModelDependencyState]:
        """Get all dependencies including model file."""
        return [self.model_file_state] + self.data_dependencies + self.code_dependencies

    @property
    def dependency_paths(self) -> List[str]:
        """Get all dependency paths for this model."""
        paths = [self.model_file]
        paths.extend(d.path for d in self.data_dependencies)
        paths.extend(d.path for d in self.code_dependencies)
        return paths

    @property
    def is_ready_locally(self) -> bool:
        """Check if model can run locally."""
        return self.local_readiness == ModelReadiness.READY

    @property
    def needs_push(self) -> bool:
        """Check if local changes need pushing."""
        return self.cloud_sync_state in (ModelSyncState.AHEAD, ModelSyncState.DIVERGED)

    def compute_readiness(self) -> ModelReadiness:
        """Compute if model can run based on dependencies."""
        missing = [d for d in self.all_dependencies if d.file_state == FileDigestState.MISSING]
        modified = [d for d in self.all_dependencies if d.file_state == FileDigestState.MODIFIED]

        if missing:
            return ModelReadiness.BROKEN
        elif modified:
            return ModelReadiness.STALE
        else:
            return ModelReadiness.READY


@dataclass
class ModelStatusSnapshot:
    """Complete snapshot of all models' AND targets' states at a point in time."""
    timestamp: datetime
    models: Dict[str, ModelState]

    # Bundle-level info
    bundle_ref: str
    bundle_tag: str
    tracked_files: Set[str]

    # Cloud state
    cloud_manifest_digest: Optional[str]
    cloud_file_digests: Dict[str, str]  # From cloud manifest
    cloud_timestamp: Optional[datetime]

    # Targets (optional, added later)
    targets: Dict[str, "TargetState"] = field(default_factory=dict)

    @property
    def all_ready(self) -> bool:
        """Check if all models are ready."""
        return all(m.is_ready_locally for m in self.models.values())

    @property
    def all_synced(self) -> bool:
        """Check if all models and targets are synced with cloud."""
        models_synced = all(m.cloud_sync_state == ModelSyncState.SYNCED for m in self.models.values())
        targets_synced = all(t.cloud_sync_state == ModelSyncState.SYNCED for t in self.targets.values())
        return models_synced and targets_synced

    def get_models_needing_attention(self) -> List[ModelState]:
        """Get models with issues."""
        return [m for m in self.models.values() if m.issues]

    def get_models_by_readiness(self, readiness: ModelReadiness) -> List[ModelState]:
        """Get models with specific readiness state."""
        return [m for m in self.models.values() if m.local_readiness == readiness]

    def get_models_by_sync_state(self, sync_state: ModelSyncState) -> List[ModelState]:
        """Get models with specific sync state."""
        return [m for m in self.models.values() if m.cloud_sync_state == sync_state]

    def get_targets_needing_attention(self) -> List["TargetState"]:
        """Get targets with issues."""
        return [t for t in self.targets.values() if t.issues]

    def get_targets_by_readiness(self, readiness: "TargetReadiness") -> List["TargetState"]:
        """Get targets with specific readiness state."""
        return [t for t in self.targets.values() if t.local_readiness == readiness]

    def get_targets_by_sync_state(self, sync_state: ModelSyncState) -> List["TargetState"]:
        """Get targets with specific sync state."""
        return [t for t in self.targets.values() if t.cloud_sync_state == sync_state]


def compute_model_digest(paths: List[str], digests: Dict[str, str]) -> str:
    """Compute deterministic digest for entire model.

    Creates a Merkle-style hash over sorted path/digest pairs for
    fast model equivalence checking.

    Args:
        paths: List of canonical paths for this model
        digests: Dict mapping canonical paths to their digests

    Returns:
        Model-level digest as sha256:xxxx
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        d = digests.get(p, "absent")
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        h.update(d.encode("utf-8"))
        h.update(b"\n")
    return f"sha256:{h.hexdigest()}"