"""Tests that verify pull implements true mirror semantics."""

import os
import tempfile
from pathlib import Path
import pytest

from modelops_bundle.core import (
    BundleConfig,
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
from modelops_bundle.storage_models import BundleIndex, BundleFileEntry, StorageType
from modelops_bundle.constants import BUNDLE_VERSION
from modelops_bundle.errors import MissingIndexError

from tests.test_registry_utils import skip_if_no_registry


# Base mock adapter for tests
class BaseMockAdapter:
    """Base mock adapter with common methods."""
    def __init__(self, remote=None, auth_provider=None):
        """Initialize mock adapter, ignoring auth_provider for testing."""
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
    
    def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None, cas=None, link_mode="auto"):
        """Mock implementation of pull_selected."""
        # Write files based on the entries
        for entry in entries:
            file_path = output_dir / entry.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Mock file content based on path
            if entry.path == "unchanged.txt":
                file_path.write_text("same content")
            elif entry.path == "modified.txt":
                file_path.write_text("original content")  # Remote version
            elif entry.path == "remote_only.txt":
                file_path.write_text("remote file content")
            else:
                file_path.write_text("mock content")


REGISTRY_AVAILABLE = os.environ.get("REGISTRY_URL", "localhost:5555")


@pytest.fixture
def registry_ref():
    """Get a unique registry reference for testing."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    return f"{REGISTRY_AVAILABLE}/test_mirror_{unique_id}"


class TestPullMirrorSemantics:
    """Test that pull truly mirrors the remote state."""
    
    def test_pull_mirrors_all_remote_files(self, tmp_path, monkeypatch):
        """Test that pull fetches ALL remote files, not just changed ones."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(environment="local", registry_ref="test/repo")
        save_config(config)
        
        # Create local files
        unchanged = tmp_path / "unchanged.txt"
        unchanged.write_text("same content")
        
        modified = tmp_path / "modified.txt"
        modified.write_text("local version")
        
        local_only = tmp_path / "local_only.txt"
        local_only.write_text("not in remote")
        
        tracked = TrackedFiles()
        tracked.add(Path("unchanged.txt"), Path("modified.txt"), Path("local_only.txt"))
        save_tracked(tracked)
        
        # Simulate previous sync state
        state = SyncState(
            last_synced_files={
                "unchanged.txt": compute_digest(unchanged),
                "modified.txt": "sha256:original",
                "local_only.txt": compute_digest(local_only)
            }
        )
        save_state(state)
        
        # Create remote state with multiple files
        remote = RemoteState(
            manifest_digest="sha256:remote_manifest",
            files={
                "unchanged.txt": FileInfo(
                    path="unchanged.txt",
                    digest=compute_digest(unchanged),
                    size=len("same content")
                ),
                "modified.txt": FileInfo(
                    path="modified.txt",
                    digest="sha256:original",  # Remote unchanged
                    size=100
                ),
                "remote_only.txt": FileInfo(
                    path="remote_only.txt",
                    digest="sha256:remote_file",
                    size=50
                )
                # local_only.txt not in remote (deleted remotely)
            }
        )
        
        # Track ALL files that get pulled
        pulled_files = []
        
        # Mock adapter that records what gets pulled
        class MockAdapter(BaseMockAdapter):
            def __init__(self, auth_provider=None, registry_ref=None):
                super().__init__(remote, auth_provider=auth_provider)
                self.pulled_files = pulled_files  # Track what gets pulled
            def get_remote_state(self, ref, tag):
                return remote
            
            def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None, cas=None, link_mode="auto"):
                # Override to track what gets pulled
                for entry in entries:
                    self.pulled_files.append(entry.path)
                # Call parent implementation
                super().pull_selected(registry_ref, digest, entries, output_dir, blob_store, cas, link_mode)
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Pull with overwrite (required due to local changes and deletions)
            result = ops_pull(config, tracked, overwrite=True, ctx=ctx)
            
            # Verify ONLY changed files were pulled (new optimized behavior)
            assert len(pulled_files) == 2
            assert "modified.txt" in pulled_files  # Modified, so pulled
            assert "remote_only.txt" in pulled_files  # New file, so pulled
            # unchanged.txt NOT in pulled_files - optimization!
            
            # Verify local state matches remote exactly (mirror)
            assert unchanged.read_text() == "same content"  # Still same
            assert modified.read_text() == "original content"  # Reverted
            assert (tmp_path / "remote_only.txt").read_text() == "remote file content"  # Added
            assert not local_only.exists()  # Deleted (not in remote)
            
            # Verify state reflects full mirror
            new_state = load_state(ctx)
            assert len(new_state.last_synced_files) == 3  # All remote files
            assert new_state.last_pull_digest == "sha256:remote_manifest"
            
            # Verify tracking matches remote
            new_tracked = load_tracked(ctx)
            assert len(new_tracked.files) == 3
            assert "remote_only.txt" in new_tracked.files
            assert "local_only.txt" not in new_tracked.files
            
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    def test_pull_optimizes_unchanged_files(self, tmp_path, monkeypatch):
        """Test that pull optimizes by skipping unchanged files."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(environment="local", registry_ref="test/repo")
        save_config(config)
        
        # Create files that are identical locally and remotely
        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")
        file1_digest = compute_digest(file1)
        
        file2 = tmp_path / "file2.txt"
        file2.write_text("content2")
        file2_digest = compute_digest(file2)
        
        tracked = TrackedFiles()
        tracked.add(Path("file1.txt"), Path("file2.txt"))
        save_tracked(tracked)
        
        # Sync state shows files are in sync
        state = SyncState(
            last_synced_files={
                "file1.txt": file1_digest,
                "file2.txt": file2_digest
            }
        )
        save_state(state)
        
        # Remote has same files (unchanged)
        remote = RemoteState(
            manifest_digest="sha256:remote_manifest",
            files={
                "file1.txt": FileInfo(
                    path="file1.txt",
                    digest=file1_digest,
                    size=len("content1")
                ),
                "file2.txt": FileInfo(
                    path="file2.txt",
                    digest=file2_digest,
                    size=len("content2")
                ),
                "file3.txt": FileInfo(  # New file in remote
                    path="file3.txt",
                    digest="sha256:new_file",
                    size=50
                )
            }
        )
        
        # Track what gets pulled
        files_pulled = set()
        
        class MockAdapter(BaseMockAdapter):
            def __init__(self, auth_provider=None, registry_ref=None):
                super().__init__(remote, auth_provider=auth_provider)
            def get_remote_state(self, ref, tag):
                return remote
            
            def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None, cas=None, link_mode="auto"):
                # New optimized behavior: only pull requested entries
                for entry in entries:
                    files_pulled.add(entry.path)
                    file_path = output_dir / entry.path
                    if entry.path == "file1.txt":
                        file_path.write_text("content1")
                    elif entry.path == "file2.txt":
                        file_path.write_text("content2")
                    elif entry.path == "file3.txt":
                        file_path.write_text("new content")
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Pull - should pull everything even though file1 and file2 are unchanged
            result = ops_pull(config, tracked, overwrite=False, ctx=ctx)
            
            # Optimized: only changed file (file3.txt) should be pulled
            assert files_pulled == {"file3.txt"}  # Only new file pulled
            
            # Verify new file was added
            assert (tmp_path / "file3.txt").exists()
            assert (tmp_path / "file3.txt").read_text() == "new content"
            
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    def test_state_reflects_full_mirror_after_pull(self, tmp_path, monkeypatch):
        """Test that sync state reflects the complete remote state after pull."""
        monkeypatch.chdir(tmp_path)
        
        # Setup project
        ctx = ProjectContext.init()
        config = BundleConfig(environment="local", registry_ref="test/repo")
        save_config(config)
        
        # Start with one tracked file
        local_file = tmp_path / "local.txt"
        local_file.write_text("local")
        
        tracked = TrackedFiles()
        tracked.add(Path("local.txt"))
        save_tracked(tracked)
        
        # Previous sync includes local.txt (so it's DELETED_REMOTE, not ADDED_LOCAL)
        state = SyncState(
            last_synced_files={
                "local.txt": compute_digest(local_file)
            }
        )
        save_state(state)
        
        # Remote has completely different set of files
        remote = RemoteState(
            manifest_digest="sha256:remote_complete",
            files={
                "remote1.txt": FileInfo(
                    path="remote1.txt",
                    digest="sha256:r1",
                    size=10
                ),
                "remote2.txt": FileInfo(
                    path="remote2.txt",
                    digest="sha256:r2",
                    size=20
                ),
                "subdir/remote3.txt": FileInfo(
                    path="subdir/remote3.txt",
                    digest="sha256:r3",
                    size=30
                )
            }
        )
        
        class MockAdapter(BaseMockAdapter):
            def __init__(self, auth_provider=None, registry_ref=None):
                super().__init__(remote, auth_provider=auth_provider)
            def get_remote_state(self, ref, tag):
                return remote
            
            def pull_files(self, registry_ref=None, reference=None, output_dir=None, ctx=None, **kwargs):
                # Create all remote files
                for path, file_info in remote.files.items():
                    file_path = output_dir / path
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(f"content of {path}")
        
        import modelops_bundle.ops
        original_adapter = modelops_bundle.ops.OrasAdapter
        modelops_bundle.ops.OrasAdapter = MockAdapter
        
        try:
            # Pull with overwrite (required since local.txt will be deleted)
            result = ops_pull(config, tracked, overwrite=True, ctx=ctx)
            
            # Verify state reflects complete remote mirror
            new_state = load_state(ctx)
            
            # State should have ALL remote files
            assert len(new_state.last_synced_files) == 3
            assert new_state.last_synced_files["remote1.txt"] == "sha256:r1"
            assert new_state.last_synced_files["remote2.txt"] == "sha256:r2"
            assert new_state.last_synced_files["subdir/remote3.txt"] == "sha256:r3"
            
            # Should NOT have the old local file
            assert "local.txt" not in new_state.last_synced_files
            
            # Pull digest should be set
            assert new_state.last_pull_digest == "sha256:remote_complete"
            
            # Tracking should match remote exactly
            new_tracked = load_tracked(ctx)
            assert len(new_tracked.files) == 3
            assert "remote1.txt" in new_tracked.files
            assert "remote2.txt" in new_tracked.files
            assert "subdir/remote3.txt" in new_tracked.files
            assert "local.txt" not in new_tracked.files
            
            # Local filesystem should match
            assert (tmp_path / "remote1.txt").exists()
            assert (tmp_path / "remote2.txt").exists()
            assert (tmp_path / "subdir/remote3.txt").exists()
            assert not local_file.exists()
            
        finally:
            modelops_bundle.ops.OrasAdapter = original_adapter
    
    @pytest.mark.integration
    def test_pull_mirror_with_real_registry(self, tmp_path, registry_ref, monkeypatch):
        """Integration test verifying mirror semantics with real registry."""
        skip_if_no_registry()
        
        # Setup source project with multiple files
        source = tmp_path / "source"
        source.mkdir()
        monkeypatch.chdir(source)
        
        ctx_src = ProjectContext.init()
        config_src = BundleConfig(environment="local", registry_ref=registry_ref)
        save_config(config_src)
        
        # Create diverse file structure
        (source / "unchanged.txt").write_text("stays same")
        (source / "modified.txt").write_text("version 1")
        (source / "dir").mkdir()
        (source / "dir/nested.txt").write_text("nested file")
        
        tracked_src = TrackedFiles()
        tracked_src.add(
            Path("unchanged.txt"),
            Path("modified.txt"),
            Path("dir/nested.txt")
        )
        save_tracked(tracked_src)
        
        # Push initial state
        ops_push(config_src, tracked_src, ctx=ctx_src)
        
        # Setup destination project
        dest = tmp_path / "dest"
        dest.mkdir()
        monkeypatch.chdir(dest)
        
        ctx_dest = ProjectContext.init()
        config_dest = BundleConfig(environment="local", registry_ref=registry_ref)
        save_config(config_dest)
        
        tracked_dest = TrackedFiles()
        save_tracked(tracked_dest)
        
        # Pull initial state
        ops_pull(config_dest, tracked_dest, ctx=ctx_dest)
        
        # Verify full mirror
        assert (dest / "unchanged.txt").read_text() == "stays same"
        assert (dest / "modified.txt").read_text() == "version 1"
        assert (dest / "dir/nested.txt").read_text() == "nested file"
        
        # Update source: modify file, add file, remove file
        monkeypatch.chdir(source)
        (source / "modified.txt").write_text("version 2")
        (source / "new.txt").write_text("new file")
        (source / "dir/nested.txt").unlink()
        
        tracked_src.add(Path("new.txt"))
        tracked_src.remove(Path("dir/nested.txt"))
        save_tracked(tracked_src)
        
        # Push changes
        ops_push(config_src, tracked_src, ctx=ctx_src)
        
        # Pull in destination (with overwrite to handle deletion)
        monkeypatch.chdir(dest)
        tracked_dest = load_tracked(ctx_dest)
        ops_pull(config_dest, tracked_dest, overwrite=True, ctx=ctx_dest)
        
        # Verify destination mirrors source exactly
        assert (dest / "unchanged.txt").read_text() == "stays same"
        assert (dest / "modified.txt").read_text() == "version 2"
        assert (dest / "new.txt").read_text() == "new file"
        assert not (dest / "dir/nested.txt").exists()
        
        # Verify state and tracking
        final_state = load_state(ctx_dest)
        assert len(final_state.last_synced_files) == 3  # unchanged, modified, new
        assert "dir/nested.txt" not in final_state.last_synced_files
        
        final_tracked = load_tracked(ctx_dest)
        assert len(final_tracked.files) == 3
        assert "new.txt" in final_tracked.files
        assert "dir/nested.txt" not in final_tracked.files