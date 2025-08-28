"""Tests for pull safety guards and mirror semantics."""

import os
import tempfile
from pathlib import Path
import pytest

from modelops_bundle.core import (
    BundleConfig,
    ChangeType,
    FileInfo,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import (
    pull as ops_pull,
    push as ops_push,
    save_config,
    save_state,
    save_tracked,
    load_state,
    load_tracked,
)
from modelops_bundle.utils import compute_digest, get_iso_timestamp
from modelops_bundle.working_state import TrackedWorkingState
from modelops_bundle.storage_models import BundleIndex, BundleFileEntry, StorageType
from modelops_bundle.constants import BUNDLE_VERSION
from modelops_bundle.errors import MissingIndexError

from tests.test_registry_utils import skip_if_no_registry


# Base mock adapter for tests
class BaseMockAdapter:
    """Base mock adapter with common methods."""
    def __init__(self, remote=None):
        self.remote = remote
    def resolve_tag_to_digest(self, ref, tag):
        # Return the actual manifest digest if we have remote state
        if hasattr(self, 'remote') and self.remote:
            return self.remote.manifest_digest
        return f"sha256:{'0' * 64}"  # Fake digest
    def get_index(self, ref, digest):
        # Create a mock BundleIndex from remote state
        if not self.remote:
            raise MissingIndexError(f"{ref}@{digest[:12]}...")
        
        index = BundleIndex(
            version=BUNDLE_VERSION,
            created=get_iso_timestamp(),
            files={}
        )
        
        # Convert RemoteState files to BundleIndex entries
        for path, file_info in self.remote.files.items():
            index.files[path] = BundleFileEntry(
                path=path,
                digest=file_info.digest,
                size=file_info.size,
                storage=StorageType.OCI,
                blobRef=None
            )
        
        return index
    
    def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None):
        """Mock implementation of pull_selected."""
        # Write files based on the entries
        for entry in entries:
            file_path = output_dir / entry.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # Write mock content
            file_path.write_text("pulled content")


# Skip if no registry available
REGISTRY_AVAILABLE = os.environ.get("REGISTRY_URL", "localhost:5555")


@pytest.fixture
def registry_ref():
    """Get a unique registry reference for testing."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    return f"{REGISTRY_AVAILABLE}/test_pull_safety_{unique_id}"


class TestPullSafetyGuards:
    """Test safety guards prevent data loss during pull."""
    
    def test_pull_overwrites_untracked_files_bug(self, tmp_path, monkeypatch, registry_ref):
        """Test that pull correctly prevents overwriting untracked files without --overwrite.
        
        This test was previously marked as xfail but the bug has been fixed - pull now
        correctly raises an error when it would overwrite untracked files.
        """
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Setup: Create config and some tracked files
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config, ctx)
        
        # Create and track file1.txt
        file1 = tmp_path / "file1.txt"
        file1.write_text("tracked content")
        
        tracked = TrackedFiles()
        tracked.add(Path("file1.txt"))
        save_tracked(tracked, ctx)
        
        # Push to create remote state
        manifest_digest = ops_push(config, tracked, ctx=ctx)
        
        # Now create an UNTRACKED file that will collide
        untracked_file = tmp_path / "secret.txt"
        untracked_file.write_text("my secret untracked data")
        
        # Someone else pushes a file with same name to remote
        # Simulate by creating new tracked files and pushing
        other_tmp = Path(tempfile.mkdtemp())
        os.chdir(other_tmp)
        other_ctx = ProjectContext.init()
        save_config(config, other_ctx)
        
        # Other person creates secret.txt and file1.txt
        other_file1 = other_tmp / "file1.txt"
        other_file1.write_text("tracked content")  # Same content
        other_secret = other_tmp / "secret.txt"
        other_secret.write_text("remote secret data")  # Different content!
        
        other_tracked = TrackedFiles()
        other_tracked.add(Path("file1.txt"), Path("secret.txt"))
        save_tracked(other_tracked, other_ctx)
        
        # Push from other location
        ops_push(config, other_tracked, ctx=other_ctx)
        
        # Go back to original directory
        os.chdir(tmp_path)
        
        # Pull WITHOUT --overwrite should NOT overwrite untracked files
        # After our fix, this should raise an error
        
        # Store original content for verification
        original_content = untracked_file.read_text()
        assert original_content == "my secret untracked data"
        
        # This should now raise an error preventing data loss
        with pytest.raises(ValueError, match="untracked files would be overwritten"):
            ops_pull(config, tracked, ctx=ctx)
        
        # Verify the untracked file was NOT overwritten
        current_content = untracked_file.read_text() 
        assert current_content == "my secret untracked data", "Untracked file should be preserved!"
    
    def test_pull_with_overwrite_allows_untracked_overwrites(self, tmp_path, monkeypatch, registry_ref):
        """Test that pull with --overwrite allows overwriting untracked files."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Setup similar to previous test
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config, ctx)
        
        file1 = tmp_path / "file1.txt"
        file1.write_text("tracked content")
        
        tracked = TrackedFiles()
        tracked.add(Path("file1.txt"))
        save_tracked(tracked, ctx)
        
        ops_push(config, tracked, ctx=ctx)
        
        # Create untracked file
        untracked_file = tmp_path / "secret.txt"
        untracked_file.write_text("my secret untracked data")
        
        # Push from another location with secret.txt
        other_tmp = Path(tempfile.mkdtemp())
        os.chdir(other_tmp)
        other_ctx = ProjectContext.init()
        save_config(config, other_ctx)
        
        other_file1 = other_tmp / "file1.txt"
        other_file1.write_text("tracked content")
        other_secret = other_tmp / "secret.txt"
        other_secret.write_text("remote secret data")
        
        other_tracked = TrackedFiles()
        other_tracked.add(Path("file1.txt"), Path("secret.txt"))
        save_tracked(other_tracked, other_ctx)
        ops_push(config, other_tracked, ctx=other_ctx)
        
        os.chdir(tmp_path)
        
        # With --overwrite, it should succeed and overwrite the untracked file
        ops_pull(config, tracked, overwrite=True, ctx=ctx)
        
        # Verify the untracked file WAS overwritten
        current_content = untracked_file.read_text() 
        assert current_content == "remote secret data", "With --overwrite, untracked file should be overwritten"
        
        # And secret.txt should now be tracked
        tracked_after = load_tracked(ctx)
        assert "secret.txt" in tracked_after.files
    
    def test_pull_blocks_with_modified_local(self, tmp_path, monkeypatch):
        """Test that pull blocks when there are local modifications without --overwrite."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(registry_ref="test/repo")
        save_config(config)
        
        # Create and track a file
        test_file = tmp_path / "test.txt"
        test_file.write_text("original content")
        tracked = TrackedFiles()
        tracked.add(Path("test.txt"))
        save_tracked(tracked)
        
        # Simulate previous sync
        state = SyncState(
            last_synced_files={
                "test.txt": "sha256:original_digest"
            }
        )
        save_state(state)
        
        # Modify file locally
        test_file.write_text("modified content")
        
        # Create remote state (unchanged from last sync)
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "test.txt": FileInfo(
                    path="test.txt",
                    digest="sha256:original_digest",
                    size=100
                )
            }
        )
        
        # Mock adapter to return our remote state
        class MockAdapter(BaseMockAdapter):
            def __init__(self):
                super().__init__(remote)
            def get_remote_state(self, ref, tag):
                return remote
            def pull_files(self, registry_ref=None, reference=None, output_dir=None, ctx=None, **kwargs):
                pass  # Would pull files
        
        # Patch the OrasAdapter
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Attempt pull without overwrite - should fail
            with pytest.raises(ValueError) as exc_info:
                ops_pull(config, tracked, overwrite=False, ctx=ctx)
            
            assert "locally modified" in str(exc_info.value)
            assert "Use --overwrite to force" in str(exc_info.value)
            
            # Verify file wasn't changed
            assert test_file.read_text() == "modified content"
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    def test_pull_blocks_with_conflicts(self, tmp_path, monkeypatch):
        """Test that pull blocks when there are conflicts without --overwrite."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(registry_ref="test/repo")
        save_config(config)
        
        # Create and track a file
        test_file = tmp_path / "conflict.txt"
        test_file.write_text("local changes")
        tracked = TrackedFiles()
        tracked.add(Path("conflict.txt"))
        save_tracked(tracked)
        
        # Simulate previous sync with different content
        state = SyncState(
            last_synced_files={
                "conflict.txt": "sha256:original"
            }
        )
        save_state(state)
        
        # Create remote state (also modified)
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "conflict.txt": FileInfo(
                    path="conflict.txt",
                    digest="sha256:remote_changes",
                    size=200
                )
            }
        )
        
        # Mock adapter
        class MockAdapter(BaseMockAdapter):
            def __init__(self):
                super().__init__(remote)
            def get_remote_state(self, ref, tag):
                return remote
            def pull_files(self, registry_ref=None, reference=None, output_dir=None, ctx=None, **kwargs):
                pass
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Attempt pull without overwrite - should fail
            with pytest.raises(ValueError) as exc_info:
                ops_pull(config, tracked, overwrite=False, ctx=ctx)
            
            assert "conflicts" in str(exc_info.value)
            assert "Use --overwrite to force" in str(exc_info.value)
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    def test_pull_blocks_with_deleted_remote(self, tmp_path, monkeypatch):
        """Test that pull blocks when files would be deleted without --overwrite."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(registry_ref="test/repo")
        save_config(config)
        
        # Create and track a file
        test_file = tmp_path / "to_delete.txt"
        test_file.write_text("content")
        tracked = TrackedFiles()
        tracked.add(Path("to_delete.txt"))
        save_tracked(tracked)
        
        # Simulate previous sync
        original_digest = compute_digest(test_file)
        state = SyncState(
            last_synced_files={
                "to_delete.txt": original_digest
            }
        )
        save_state(state)
        
        # Create remote state (file deleted)
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={}  # File no longer exists remotely
        )
        
        # Mock adapter
        class MockAdapter(BaseMockAdapter):
            def __init__(self):
                super().__init__(remote)
            def get_remote_state(self, ref, tag):
                return remote
            def pull_files(self, registry_ref=None, reference=None, output_dir=None, ctx=None, **kwargs):
                pass
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Attempt pull without overwrite - should fail
            with pytest.raises(ValueError) as exc_info:
                ops_pull(config, tracked, overwrite=False, ctx=ctx)
            
            assert "would be deleted" in str(exc_info.value)
            assert "Use --overwrite to force" in str(exc_info.value)
            
            # Verify file wasn't deleted
            assert test_file.exists()
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    def test_pull_with_overwrite_allows_all_changes(self, tmp_path, monkeypatch):
        """Test that --overwrite allows all destructive changes."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project with multiple files
        ctx = ProjectContext.init()
        config = BundleConfig(registry_ref="test/repo")
        save_config(config)
        
        # Create files with different change types
        modified = tmp_path / "modified.txt"
        modified.write_text("local changes")
        
        to_delete = tmp_path / "to_delete.txt"
        to_delete.write_text("will be deleted")
        
        conflict = tmp_path / "conflict.txt"
        conflict.write_text("local version")
        
        tracked = TrackedFiles()
        tracked.add(Path("modified.txt"), Path("to_delete.txt"), Path("conflict.txt"))
        save_tracked(tracked)
        
        # Simulate previous sync
        state = SyncState(
            last_synced_files={
                "modified.txt": "sha256:original",
                "to_delete.txt": compute_digest(to_delete),
                "conflict.txt": "sha256:original"
            }
        )
        save_state(state)
        
        # Create remote state
        remote = RemoteState(
            manifest_digest="sha256:remote123",
            files={
                "modified.txt": FileInfo(
                    path="modified.txt",
                    digest="sha256:original",  # Unchanged remotely
                    size=100
                ),
                # to_delete.txt is missing (deleted remotely)
                "conflict.txt": FileInfo(
                    path="conflict.txt",
                    digest="sha256:remote_version",  # Changed remotely
                    size=200
                )
            }
        )
        
        # Track what pull_files was called
        pull_called = []
        
        # Mock adapter
        class MockAdapter(BaseMockAdapter):
            def __init__(self):
                super().__init__(remote)
            def get_remote_state(self, ref, tag):
                return remote
            def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None):
                pull_called.append(True)
                # Simulate pulling remote files based on entries
                for entry in entries:
                    if entry.path == "modified.txt":
                        (output_dir / "modified.txt").write_text("original content")
                    elif entry.path == "conflict.txt":
                        (output_dir / "conflict.txt").write_text("remote version")
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Pull with overwrite - should succeed
            result = ops_pull(config, tracked, overwrite=True, ctx=ctx)
            
            # Verify pull was called
            assert pull_called
            
            # Verify files were updated/deleted
            assert modified.read_text() == "original content"  # Reverted to remote
            assert not to_delete.exists()  # Deleted as per remote
            assert conflict.read_text() == "remote version"  # Overwritten with remote
            
            # Verify state was updated
            new_state = load_state(ctx)
            assert new_state.last_pull_digest == "sha256:remote123"
            assert "to_delete.txt" not in new_state.last_synced_files
            assert new_state.last_synced_files["modified.txt"] == "sha256:original"
            assert new_state.last_synced_files["conflict.txt"] == "sha256:remote_version"
            
            # Verify tracking was updated
            new_tracked = load_tracked(ctx)
            assert "to_delete.txt" not in new_tracked.files
            assert "modified.txt" in new_tracked.files
            assert "conflict.txt" in new_tracked.files
            
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    @pytest.mark.integration
    def test_pull_safety_with_real_registry(self, tmp_path, registry_ref, monkeypatch):
        """Integration test with real registry to verify safety guards."""
        skip_if_no_registry()
        
        # Create two project directories
        project1 = tmp_path / "project1"
        project2 = tmp_path / "project2"
        project1.mkdir()
        project2.mkdir()
        
        # Setup project1 and push initial state
        monkeypatch.chdir(project1)
        ctx1 = ProjectContext.init()
        config1 = BundleConfig(registry_ref=registry_ref)
        save_config(config1)
        
        file1 = project1 / "file.txt"
        file1.write_text("version 1")
        
        tracked1 = TrackedFiles()
        tracked1.add(Path("file.txt"))
        save_tracked(tracked1)
        
        # Push initial version
        ops_push(config1, tracked1, ctx=ctx1)
        
        # Setup project2 and pull
        monkeypatch.chdir(project2)
        ctx2 = ProjectContext.init()
        config2 = BundleConfig(registry_ref=registry_ref)
        save_config(config2)
        
        # Create empty tracked files (will be populated by pull)
        tracked2 = TrackedFiles()
        save_tracked(tracked2)
        
        # Pull initial version (this adds to tracking)
        ops_pull(config2, tracked2, ctx=ctx2)
        
        # Reload tracked to see what was added
        tracked2 = load_tracked(ctx2)
        assert (project2 / "file.txt").read_text() == "version 1"
        
        # Modify locally in project2
        (project2 / "file.txt").write_text("local changes")
        
        # Push new version from project1
        monkeypatch.chdir(project1)
        file1.write_text("version 2")
        ops_push(config1, tracked1, ctx=ctx1)
        
        # Try to pull in project2 - should be blocked
        monkeypatch.chdir(project2)
        with pytest.raises(ValueError) as exc_info:
            ops_pull(config2, tracked2, overwrite=False, ctx=ctx2)
        
        # It's a conflict because both local and remote changed
        assert "conflicts" in str(exc_info.value)
        
        # Verify local changes preserved
        assert (project2 / "file.txt").read_text() == "local changes"
        
        # Now pull with overwrite
        ops_pull(config2, tracked2, overwrite=True, ctx=ctx2)
        assert (project2 / "file.txt").read_text() == "version 2"