"""Simplified deletion tests focusing on core logic."""

import os
from pathlib import Path
import pytest

from modelops_bundle.core import (
    BundleConfig,
    ChangeType,
    RemoteState,
    SyncState,
    TrackedFiles,
    FileInfo,
)
from modelops_bundle.working_state import TrackedWorkingState
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import (
    save_config,
    save_state,
    save_tracked,
    load_state,
)

from tests.fixtures.sample_project import create_sample_project


@pytest.fixture
def sample_project(tmp_path):
    """Create a sample project in a temp directory."""
    return create_sample_project(tmp_path, "test_del_simple")


class TestPullDeletions:
    """Test pull operations with file deletions."""
    
    def test_pull_deletes_files_when_deleted_remotely(self, sample_project):
        """Test that pull with overwrite deletes files that were deleted remotely."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        # Setup: Create config and tracked files
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked, ctx)
        
        # Get actual file digests for sync state
        from modelops_bundle.utils import compute_digest
        model_digest = compute_digest(sample_project / "src" / "model.py")
        data_digest = compute_digest(sample_project / "data" / "data.csv")
        
        # Create initial sync state with REAL digests (simulating a previous push)
        initial_state = SyncState(
            last_push_digest="sha256:initial",
            last_synced_files={
                "src/model.py": model_digest,
                "data/data.csv": data_digest
            }
        )
        save_state(initial_state, ctx)
        
        # Verify both files exist locally
        assert (sample_project / "src" / "model.py").exists()
        assert (sample_project / "data" / "data.csv").exists()
        
        # Mock a remote that only has model.py (data.csv was deleted)
        mock_remote = RemoteState(
            manifest_digest="sha256:newremote",
            files={
                "src/model.py": FileInfo(
                    path="src/model.py",
                    digest=model_digest,  # Same as before
                    size=100
                )
                # data/data.csv is missing from remote!
            }
        )
        
        # Create working state and compute diff
        working_state = TrackedWorkingState.from_tracked(tracked, ctx)
        
        # Debug: Check actual file digests
        actual_digest = working_state.files.get("data/data.csv")
        if actual_digest:
            print(f"Actual digest of data.csv: {actual_digest.digest}")
            print(f"Last synced digest: {initial_state.last_synced_files['data/data.csv']}")
        
        diff = working_state.compute_diff(mock_remote, initial_state)
        
        # Check that deletion is detected
        changes_by_path = {c.path: c.change_type for c in diff.changes}
        print(f"Change type for data.csv: {changes_by_path['data/data.csv']}")
        assert changes_by_path["data/data.csv"] == ChangeType.DELETED_REMOTE
        
        # Create pull preview with overwrite
        pull_preview = diff.to_pull_preview(overwrite=True)
        
        # Verify the preview includes the deletion
        assert "data/data.csv" in pull_preview.will_delete_local
        assert len(pull_preview.will_delete_local) == 1
        
    def test_pull_without_overwrite_skips_remote_deletions(self, sample_project):
        """Test that pull without overwrite doesn't delete files."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        # Setup
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked, ctx)
        
        # Get actual file digests
        from modelops_bundle.utils import compute_digest
        model_digest = compute_digest(sample_project / "src" / "model.py")
        data_digest = compute_digest(sample_project / "data" / "data.csv")
        
        # Create sync state with real digests
        initial_state = SyncState(
            last_push_digest="sha256:initial",
            last_synced_files={
                "src/model.py": model_digest,
                "data/data.csv": data_digest
            }
        )
        save_state(initial_state, ctx)
        
        # Mock remote with deletion
        mock_remote = RemoteState(
            manifest_digest="sha256:newremote",
            files={
                "src/model.py": FileInfo(
                    path="src/model.py",
                    digest=model_digest,
                    size=100
                )
            }
        )
        
        # Create diff and pull preview without overwrite
        working_state = TrackedWorkingState.from_tracked(tracked, ctx)
        diff = working_state.compute_diff(mock_remote, initial_state)
        pull_preview = diff.to_pull_preview(overwrite=False)
        
        # Without overwrite, deletions should be treated as conflicts
        assert len(pull_preview.will_delete_local) == 0
        assert "data/data.csv" in pull_preview.conflicts