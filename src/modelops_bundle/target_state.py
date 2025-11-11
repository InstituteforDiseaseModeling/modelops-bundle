"""Target state management for tracking target readiness and sync status."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# Reuse existing enums from model_state
from .model_state import FileDigestState, ModelSyncState


class TargetReadiness(Enum):
    """Whether target can run."""
    READY = "ready"          # All deps present and valid
    STALE = "stale"          # Some deps modified but present
    BROKEN = "broken"        # Required deps missing
    UNKNOWN = "unknown"      # Can't determine state


@dataclass
class TargetDependencyState:
    """State of a single target dependency.

    Attributes:
        path: Canonical POSIX path relative to bundle root
        expected_digest: Expected digest from registry (if available)
        actual_digest: Actual digest from file system
        file_state: Current state of file (current, modified, missing, unknown)
        size: File size in bytes
        last_modified: Last modification time
    """
    path: str
    expected_digest: Optional[str]
    actual_digest: Optional[str]
    file_state: FileDigestState
    size: Optional[int]
    last_modified: Optional[datetime]

    @property
    def is_valid(self) -> bool:
        """Check if dependency is valid (present and not missing)."""
        return (self.actual_digest is not None and
                self.file_state in (FileDigestState.CURRENT, FileDigestState.UNKNOWN))


@dataclass
class TargetState:
    """Complete state of a target including all dependencies.

    Attributes:
        target_id: Unique identifier for target
        entrypoint: Module path and function (e.g., "targets.prevalence:prevalence_target")
        target_file: Canonical path to target Python file
        model_output: Name of model output this target calibrates against
        target_file_state: State of the target file itself
        data_dependencies: List of observation file states
        local_readiness: Whether target can run locally
        cloud_sync_state: Sync state with cloud
        local_target_digest: Digest of all target dependencies
        cloud_target_digest: Digest from cloud manifest
        cloud_timestamp: When target was last pushed to cloud
        last_push_digest: Digest from last successful push
        issues: List of human-readable issues
    """
    target_id: str
    entrypoint: str
    target_file: str
    model_output: str

    # Dependency states
    target_file_state: TargetDependencyState
    data_dependencies: List[TargetDependencyState]

    # Computed states
    local_readiness: TargetReadiness
    cloud_sync_state: ModelSyncState

    # Target-level digests for fast comparison
    local_target_digest: Optional[str] = None
    cloud_target_digest: Optional[str] = None

    # Cloud info
    cloud_timestamp: Optional[datetime] = None
    last_push_digest: Optional[str] = None

    # Issues found
    issues: List[str] = field(default_factory=list)

    @property
    def all_dependencies(self) -> List[TargetDependencyState]:
        """Get all dependencies including target file."""
        return [self.target_file_state] + self.data_dependencies

    @property
    def dependency_paths(self) -> List[str]:
        """Get all dependency paths for this target."""
        paths = [self.target_file]
        paths.extend(d.path for d in self.data_dependencies)
        return paths

    @property
    def is_ready_locally(self) -> bool:
        """Check if target can run locally."""
        return self.local_readiness == TargetReadiness.READY

    def compute_readiness(self) -> TargetReadiness:
        """Compute if target can run based on dependencies.

        Returns:
            BROKEN if any dependencies are missing
            STALE if any dependencies are modified but present
            READY if all dependencies are current
            UNKNOWN otherwise
        """
        missing = [d for d in self.all_dependencies if d.file_state == FileDigestState.MISSING]
        modified = [d for d in self.all_dependencies if d.file_state == FileDigestState.MODIFIED]

        if missing:
            return TargetReadiness.BROKEN
        elif modified:
            return TargetReadiness.STALE
        elif all(d.file_state in (FileDigestState.CURRENT, FileDigestState.UNKNOWN)
                 for d in self.all_dependencies):
            return TargetReadiness.READY
        else:
            return TargetReadiness.UNKNOWN


def compute_target_digest(paths: List[str], digests: Dict[str, str]) -> str:
    """Compute deterministic digest for entire target.

    Creates a Merkle-style hash over sorted path/digest pairs for
    fast target equivalence checking. Uses same algorithm as models.

    Args:
        paths: List of canonical paths for this target
        digests: Dict mapping canonical paths to their digests

    Returns:
        Target-level digest as sha256:xxxx
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        d = digests.get(p, "absent")
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        h.update(d.encode("utf-8"))
        h.update(b"\n")
    return f"sha256:{h.hexdigest()}"
