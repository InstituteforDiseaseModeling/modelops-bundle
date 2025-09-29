"""Tests for file deletion detection and handling."""

import os
import tempfile
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
    load_config,
    load_state,
    load_tracked,
    push as ops_push,
    pull as ops_pull,
    save_config,
    save_state,
    save_tracked,
)
from modelops_bundle.oras import OrasAdapter

from tests.fixtures.sample_project import create_sample_project
from tests.test_registry_utils import skip_if_no_registry


# Skip if no registry available
REGISTRY_AVAILABLE = "localhost:5555"


@pytest.fixture
def sample_project(tmp_path):
    """Create a sample project in a temp directory."""
    return create_sample_project(tmp_path, "test_deletions")


@pytest.fixture
def registry_ref():
    """Get a unique registry reference for testing."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    return f"{REGISTRY_AVAILABLE}/test_del_{unique_id}"


class TestDeletionDetection:
    """Test deletion detection and handling."""
    
    def test_local_deletion_detected(self, sample_project):
        """Ensure local deletions are properly detected."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        # Track files
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        
        # Delete one file
        (sample_project / "data" / "data.csv").unlink()
        
        # Create working state - should detect deletion
        working_state = TrackedWorkingState.from_tracked(tracked)
        
        assert len(working_state.files) == 1  # Only model.py remains
        assert "src/model.py" in working_state.files
        assert "data/data.csv" not in working_state.files
        
        # Check missing files
        assert len(working_state.missing) == 1
        assert "data/data.csv" in working_state.missing
        assert working_state.has_deletions()
    
    def test_diff_with_local_deletion(self, sample_project):
        """Test diff computation with local deletions."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        # Setup tracked files
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        
        # Create initial state (simulating previous sync)
        state = SyncState(
            last_synced_files={
                "src/model.py": "sha256:abc123",
                "data/data.csv": "sha256:def456"
            }
        )
        
        # Create remote state (unchanged)
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "src/model.py": FileInfo(
                    path="src/model.py",
                    digest="sha256:abc123",
                    size=100
                ),
                "data/data.csv": FileInfo(
                    path="data/data.csv",
                    digest="sha256:def456",
                    size=200
                )
            }
        )
        
        # Delete local file
        (sample_project / "data" / "data.csv").unlink()
        
        # Create working state and compute diff
        working_state = TrackedWorkingState.from_tracked(tracked)
        diff = working_state.compute_diff(remote, state)
        
        # Find the deletion change
        changes_by_path = {c.path: c for c in diff.changes}
        assert "data/data.csv" in changes_by_path
        
        deletion_change = changes_by_path["data/data.csv"]
        assert deletion_change.change_type == ChangeType.DELETED_LOCAL
        assert deletion_change.local is None  # File doesn't exist locally
        assert deletion_change.remote is not None  # Still exists remotely
    
    @pytest.mark.integration
    def test_push_with_only_deletions(self, sample_project, registry_ref, monkeypatch):
        """Verify push works when only deletions occur."""
        skip_if_no_registry()
        monkeypatch.chdir(sample_project)
        
        # Initialize
        config = BundleConfig(environment="local", registry_ref=registry_ref)
        save_config(config)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        
        # Initial push with both files
        manifest1 = ops_push(config, tracked)
        assert manifest1
        
        # Verify remote has both files
        adapter = OrasAdapter()
        remote1 = adapter.get_remote_state(registry_ref)
        assert len(remote1.files) == 2
        assert "data/data.csv" in remote1.files
        
        # Delete one file
        (sample_project / "data" / "data.csv").unlink()
        
        # Push again - should update manifest without the deleted file
        manifest2 = ops_push(config, tracked)
        assert manifest2
        assert manifest2 != manifest1  # Different manifest
        
        # Verify remote now has only one file
        remote2 = adapter.get_remote_state(registry_ref)
        assert len(remote2.files) == 1
        assert "src/model.py" in remote2.files
        assert "data/data.csv" not in remote2.files
    
    @pytest.mark.integration
    def test_pull_remote_deletion_with_overwrite(self, sample_project, registry_ref, monkeypatch):
        """Test pull handles remote deletions with --overwrite."""
        skip_if_no_registry()
        monkeypatch.chdir(sample_project)
        
        # Initialize
        config = BundleConfig(environment="local", registry_ref=registry_ref)
        save_config(config)
        
        # Push both files initially
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        ops_push(config, tracked)
        
        # Important: Save the initial sync state for comparison
        initial_state = load_state()
        assert "data/data.csv" in initial_state.last_synced_files
        
        # Simulate remote deletion by pushing from another location
        other_dir = sample_project.parent / "other"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)
        
        # Initialize bundle in the other directory
        from modelops_bundle.context import ProjectContext
        other_ctx = ProjectContext.init()
        
        # Create only one file
        (other_dir / "src").mkdir(parents=True)
        (other_dir / "src" / "model.py").write_text("# Updated model")
        
        save_config(config, other_ctx)
        other_tracked = TrackedFiles()
        other_tracked.add(Path("src/model.py"))  # Only model.py
        save_tracked(other_tracked, other_ctx)
        
        manifest = ops_push(config, other_tracked, ctx=other_ctx)
        
        # Verify the remote now has only one file
        adapter2 = OrasAdapter()
        remote_after_push = adapter2.get_remote_state(registry_ref)
        assert len(remote_after_push.files) == 1
        assert "src/model.py" in remote_after_push.files
        
        # Go back to original and pull
        monkeypatch.chdir(sample_project)
        
        # Verify file exists before pull
        assert (sample_project / "data" / "data.csv").exists()
        
        # Pull with overwrite - should delete local file
        plan = ops_pull(config, tracked, overwrite=True)
        
        # Verify file was deleted locally
        assert not (sample_project / "data" / "data.csv").exists()
        assert (sample_project / "src" / "model.py").exists()
        
        # Check tracked files were updated
        updated_tracked = load_tracked()
        assert "src/model.py" in updated_tracked.files
        assert "data/data.csv" not in updated_tracked.files
    
    @pytest.mark.integration
    def test_pull_remote_deletion_without_overwrite(self, sample_project, registry_ref, monkeypatch):
        """Test pull without overwrite keeps local deletions."""
        skip_if_no_registry()
        monkeypatch.chdir(sample_project)
        
        # Initialize
        config = BundleConfig(environment="local", registry_ref=registry_ref)
        save_config(config)
        
        # Push both files initially
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        ops_push(config, tracked)
        
        # Simulate remote deletion
        other_dir = sample_project.parent / "other2"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)
        
        (other_dir / "src").mkdir(parents=True)
        (other_dir / "src" / "model.py").write_text("# Remote model")
        
        save_config(config)
        other_tracked = TrackedFiles()
        other_tracked.add(Path("src/model.py"))
        save_tracked(other_tracked)
        ops_push(config, other_tracked)
        
        # Back to original
        monkeypatch.chdir(sample_project)
        
        # Pull without overwrite - should skip deletion
        try:
            plan = ops_pull(config, tracked, overwrite=False)
            # File should still exist locally
            assert (sample_project / "data" / "data.csv").exists()
        except ValueError as e:
            # May raise conflict error depending on implementation
            assert "overwrite" in str(e).lower()
    
    def test_sync_state_pruning_after_deletion(self, sample_project):
        """Verify sync state removes deleted file entries."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        # Create initial sync state with two files
        state = SyncState(
            last_synced_files={
                "src/model.py": "sha256:aaa111",
                "data/data.csv": "sha256:bbb222",
                "data/extra.txt": "sha256:ccc333"  # Extra file to be pruned
            }
        )
        
        # Track only two files
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        
        # Delete one tracked file
        (sample_project / "data" / "data.csv").unlink()
        
        # Create working state (only model.py exists)
        working_state = TrackedWorkingState.from_tracked(tracked)
        
        # After a push, state should be updated to remove deleted entries
        # This tests that the state update logic works correctly
        state.update_after_push("sha256:newmanifest", working_state.snapshot)
        
        # Verify only existing file remains in state
        assert "src/model.py" in state.last_synced_files
        assert "data/data.csv" not in state.last_synced_files  # Deleted file removed
        assert "data/extra.txt" not in state.last_synced_files  # Untracked file removed
    
    def test_deletion_conflict_detection(self, sample_project):
        """Test conflict when file deleted locally but modified remotely."""
        os.chdir(sample_project)
        
        # Initialize context
        ctx = ProjectContext.init()
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        
        # Initial sync state
        state = SyncState(
            last_synced_files={
                "src/model.py": "sha256:original1",
                "data/data.csv": "sha256:original2"
            }
        )
        
        # Remote has modified version
        remote = RemoteState(
            manifest_digest="sha256:remote",
            files={
                "src/model.py": FileInfo(
                    path="src/model.py",
                    digest="sha256:original1",  # Unchanged
                    size=100
                ),
                "data/data.csv": FileInfo(
                    path="data/data.csv",
                    digest="sha256:modified2",  # Changed remotely!
                    size=250
                )
            }
        )
        
        # Delete local file
        (sample_project / "data" / "data.csv").unlink()
        
        # Create working state and compute diff
        working_state = TrackedWorkingState.from_tracked(tracked)
        diff = working_state.compute_diff(remote, state)
        
        # Should detect conflict (deleted locally, modified remotely)
        conflicts = [c for c in diff.changes if c.change_type == ChangeType.CONFLICT]
        assert len(conflicts) == 1
        assert conflicts[0].path == "data/data.csv"


class TestTrackedFilesRemove:
    """Test the fixed TrackedFiles.remove() method."""
    
    def test_remove_single_path(self, sample_project):
        """Verify TrackedFiles.remove() works with single path."""
        os.chdir(sample_project)
        
        # Initialize context (for consistency, though not strictly needed here)
        ctx = ProjectContext.init()
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        assert len(tracked.files) == 2
        
        # Remove single path (not a list!)
        tracked.remove(Path("data/data.csv"))
        assert len(tracked.files) == 1
        assert "src/model.py" in tracked.files
        assert "data/data.csv" not in tracked.files
    
    def test_remove_multiple_paths(self, sample_project):
        """Verify TrackedFiles.remove() works with multiple paths."""
        os.chdir(sample_project)
        
        # Initialize context (for consistency)
        ctx = ProjectContext.init()
        
        tracked = TrackedFiles()
        tracked.add(
            Path("src/model.py"),
            Path("data/data.csv"),
            Path("README.md")
        )
        assert len(tracked.files) == 3
        
        # Remove multiple paths using varargs
        tracked.remove(Path("data/data.csv"), Path("README.md"))
        assert len(tracked.files) == 1
        assert "src/model.py" in tracked.files
