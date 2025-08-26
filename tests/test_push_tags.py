"""Test push operations to different tags."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from modelops_bundle.ops import push
from modelops_bundle.core import (
    BundleConfig,
    TrackedFiles,
    RemoteState,
    SyncState,
    FileInfo,
)
from modelops_bundle.context import ProjectContext
from modelops_bundle.utils import compute_digest


@pytest.fixture
def mock_context(tmp_path):
    """Create a mock project context."""
    # Create project structure
    bundle_dir = tmp_path / ".modelops-bundle"
    bundle_dir.mkdir()
    
    # Create test files
    (tmp_path / "file1.txt").write_text("content1")
    (tmp_path / "file2.txt").write_text("content2")
    
    # Mock ProjectContext
    ctx = Mock(spec=ProjectContext)
    ctx.root = tmp_path
    ctx.storage_dir = bundle_dir
    ctx.config_path = bundle_dir / "config.yaml"
    ctx.tracked_path = bundle_dir / "tracked"
    ctx.state_path = bundle_dir / "state.json"
    
    return ctx


@pytest.fixture
def mock_config():
    """Create a mock bundle config."""
    return BundleConfig(
        registry_ref="localhost:5555/test",
        default_tag="latest",
        artifact_type="application/vnd.test"
    )


@pytest.fixture  
def mock_tracked():
    """Create mock tracked files."""
    tracked = TrackedFiles()
    tracked.add("file1.txt")
    tracked.add("file2.txt")
    return tracked


class TestPushToNewTag:
    """Test pushing to a new tag that doesn't exist yet."""
    
    def test_push_to_new_tag_uploads_all_files(self, mock_context, mock_config, mock_tracked):
        """Test that pushing to a new tag uploads ALL files, not just changed ones."""
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = MockAdapter.return_value
            
            # Mock: New tag doesn't exist in registry
            adapter.get_remote_state.side_effect = Exception("Tag not found")
            adapter.get_current_tag_digest.return_value = None  # Tag doesn't exist
            
            # Mock: Push succeeds
            adapter.push_files.return_value = "sha256:newdigest"
            
            # Mock load functions
            with patch("modelops_bundle.ops.load_config", return_value=mock_config), \
                 patch("modelops_bundle.ops.load_tracked", return_value=mock_tracked), \
                 patch("modelops_bundle.ops.load_state", return_value=SyncState()), \
                 patch("modelops_bundle.ops.save_state"):
                
                # Execute push to new tag
                digest = push(mock_config, mock_tracked, tag="v1.0", ctx=mock_context)
                
                # Verify push was called with ALL files
                adapter.push_files.assert_called_once()
                call_args = adapter.push_files.call_args
                
                # Check that all tracked files were included
                # push_files is called with keyword arguments
                pushed_files = call_args.kwargs.get('files')
                assert pushed_files is not None
                assert len(pushed_files) == 2
                assert any(f.path == "file1.txt" for f in pushed_files)
                assert any(f.path == "file2.txt" for f in pushed_files)
    
    def test_push_to_existing_tag_with_no_changes(self, mock_context, mock_config, mock_tracked):
        """Test that pushing to existing tag with no changes is optimized."""
        
        # Compute actual digests from the test files
        d1 = compute_digest(mock_context.root / "file1.txt")
        d2 = compute_digest(mock_context.root / "file2.txt")
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = MockAdapter.return_value
            
            # Mock: Tag exists with same files (using REAL digests)
            remote_files = {
                "file1.txt": FileInfo(
                    path="file1.txt",
                    digest=d1,  # Real digest
                    size=100
                ),
                "file2.txt": FileInfo(
                    path="file2.txt", 
                    digest=d2,  # Real digest
                    size=200
                )
            }
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:existingdigest",
                files=remote_files
            )
            adapter.get_current_tag_digest.return_value = "sha256:existingdigest"  # Tag exists
            
            # Mock: Sync state matches remote (using REAL digests)
            sync_state = SyncState()
            sync_state.last_synced_files = {
                "file1.txt": d1,  # Real digest
                "file2.txt": d2   # Real digest
            }
            
            with patch("modelops_bundle.ops.load_config", return_value=mock_config), \
                 patch("modelops_bundle.ops.load_tracked", return_value=mock_tracked), \
                 patch("modelops_bundle.ops.load_state", return_value=sync_state):
                
                # Execute push - should detect no changes since digests match
                digest = push(mock_config, mock_tracked, tag="latest", ctx=mock_context)
                
                # Verify no push was needed (optimization worked)
                adapter.push_files.assert_not_called()
                assert digest == "sha256:existingdigest"


class TestSyncStateBugs:
    """Test sync state handling that had bugs."""
    
    def test_sync_state_saves_all_tracked_files_after_push(self, mock_context, mock_config, mock_tracked):
        """Test that push saves ALL tracked files to sync state, not just uploaded ones."""
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = MockAdapter.return_value
            
            # Mock: Remote exists with one file
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:olddigest",
                files={
                    "file1.txt": FileInfo(
                        path="file1.txt",
                        digest="sha256:old1",
                        size=100
                    )
                }
            )
            
            # Mock push succeeds
            adapter.push_files.return_value = "sha256:newdigest"
            adapter.get_current_tag_digest.return_value = "sha256:olddigest"  # Existing tag
            
            saved_state = None
            def capture_state(state, ctx):
                nonlocal saved_state
                saved_state = state
            
            with patch("modelops_bundle.ops.load_config", return_value=mock_config), \
                 patch("modelops_bundle.ops.load_tracked", return_value=mock_tracked), \
                 patch("modelops_bundle.ops.load_state", return_value=SyncState()), \
                 patch("modelops_bundle.ops.save_state", side_effect=capture_state):
                
                # Execute push
                push(mock_config, mock_tracked, tag="latest", ctx=mock_context)
                
                # Verify ALL tracked files are in sync state
                assert saved_state is not None
                assert len(saved_state.last_synced_files) == 2
                assert "file1.txt" in saved_state.last_synced_files
                assert "file2.txt" in saved_state.last_synced_files


class TestManifestOperations:
    """Test manifest-related operations."""
    
    def test_manifest_list_shows_all_tags(self):
        """Test that manifest list shows all available tags."""
        from modelops_bundle.oras import OrasAdapter
        
        with patch.object(OrasAdapter, "list_tags") as mock_list:
            mock_list.return_value = ["latest", "v1.0", "v2.0", "dev"]
            
            adapter = OrasAdapter()
            tags = adapter.list_tags("localhost:5555/test")
            
            assert len(tags) == 4
            assert "latest" in tags
            assert "v1.0" in tags
            assert "v2.0" in tags
            assert "dev" in tags
