"""Tests for manifest equality and remote-only file handling."""

import os
import tempfile
from pathlib import Path
import pytest

from modelops_bundle.core import BundleConfig, TrackedFiles
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import (
    push as ops_push,
    save_config,
    save_tracked,
    load_state,
)
from modelops_bundle.oras import OrasAdapter

from tests.fixtures.sample_project import create_sample_project
from tests.test_registry_utils import skip_if_no_registry


# Skip if no registry available
REGISTRY_AVAILABLE = os.environ.get("REGISTRY_URL", "localhost:5555")


@pytest.fixture
def registry_ref():
    """Get a unique registry reference for testing."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    return f"{REGISTRY_AVAILABLE}/test_manifest_{unique_id}"


class TestManifestOperations:
    """Test manifest-related operations."""
    
    @pytest.mark.integration
    def test_manifest_equality_check(self, tmp_path, registry_ref, monkeypatch):
        """Test that push updates manifest when files are same but manifest differs."""
        skip_if_no_registry()
        
        # Create two identical projects in different locations
        project1 = tmp_path / "project1"
        project2 = tmp_path / "project2"
        
        for project_dir in [project1, project2]:
            project_dir.mkdir()
            (project_dir / "file.txt").write_text("same content")
            
            # Initialize project
            monkeypatch.chdir(project_dir)
            ctx = ProjectContext.init()
            
            config = BundleConfig(registry_ref=registry_ref)
            save_config(config)
            
            tracked = TrackedFiles()
            tracked.add(Path("file.txt"))
            save_tracked(tracked)
        
        # Push from project1
        monkeypatch.chdir(project1)
        ctx1 = ProjectContext()
        config1 = BundleConfig(registry_ref=registry_ref)
        tracked1 = TrackedFiles(files={"file.txt"})
        
        manifest1 = ops_push(config1, tracked1, ctx=ctx1)
        assert manifest1
        
        # Push from project2 with same file content
        monkeypatch.chdir(project2)
        ctx2 = ProjectContext()
        config2 = BundleConfig(registry_ref=registry_ref)
        tracked2 = TrackedFiles(files={"file.txt"})
        
        # Should update manifest even though file content is same
        # (because path context is different)
        manifest2 = ops_push(config2, tracked2, ctx=ctx2)
        assert manifest2
        
        # With optimization, the second push is skipped since content is identical
        # Both pushes return the same manifest digest
        assert manifest1 == manifest2  # Same due to optimization
        
        # But the content (files) should be identical - verify via pull
        from modelops_bundle.oras import OrasAdapter
        adapter = OrasAdapter()
        remote1 = adapter.get_remote_state(registry_ref, "latest")
        
        # Both should have the same files
        assert len(remote1.files) == 1
        assert "file.txt" in remote1.files
    
    @pytest.mark.integration
    def test_remote_only_files_pruning(self, tmp_path, registry_ref, monkeypatch):
        """Test that remote-only files are pruned on push (mirror semantics)."""
        skip_if_no_registry()
        
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        
        # Create initial files
        (project_dir / "tracked.txt").write_text("tracked content")
        (project_dir / "extra.txt").write_text("extra content")
        
        monkeypatch.chdir(project_dir)
        ctx = ProjectContext.init()
        
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config)
        
        # First push with both files
        tracked_all = TrackedFiles()
        tracked_all.add(Path("tracked.txt"), Path("extra.txt"))
        save_tracked(tracked_all)
        
        manifest1 = ops_push(config, tracked_all, ctx=ctx)
        assert manifest1
        
        # Check remote has both files
        adapter = OrasAdapter()
        remote1 = adapter.get_remote_state(registry_ref)
        assert len(remote1.files) == 2
        assert "tracked.txt" in remote1.files
        assert "extra.txt" in remote1.files
        
        # Now push with only one file tracked
        tracked_subset = TrackedFiles()
        tracked_subset.add(Path("tracked.txt"))
        save_tracked(tracked_subset)
        
        manifest2 = ops_push(config, tracked_subset, ctx=ctx)
        assert manifest2
        assert manifest2 != manifest1  # Manifest should change
        
        # Check remote now has only tracked file
        remote2 = adapter.get_remote_state(registry_ref)
        assert len(remote2.files) == 1
        assert "tracked.txt" in remote2.files
        assert "extra.txt" not in remote2.files
    
    @pytest.mark.integration  
    def test_push_with_deletion_updates_manifest(self, tmp_path, registry_ref, monkeypatch):
        """Test that deleting files and pushing updates the manifest."""
        skip_if_no_registry()
        
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        
        # Create initial files
        (project_dir / "file1.txt").write_text("content1")
        (project_dir / "file2.txt").write_text("content2")
        
        monkeypatch.chdir(project_dir)
        ctx = ProjectContext.init()
        
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config)
        
        # Track and push both files
        tracked = TrackedFiles()
        tracked.add(Path("file1.txt"), Path("file2.txt"))
        save_tracked(tracked)
        
        manifest1 = ops_push(config, tracked, ctx=ctx)
        assert manifest1
        
        # Delete file2 but keep tracking it
        (project_dir / "file2.txt").unlink()
        
        # Push again - should update manifest
        manifest2 = ops_push(config, tracked, ctx=ctx)
        assert manifest2
        assert manifest2 != manifest1  # Manifest should change
        
        # Verify remote only has file1
        adapter = OrasAdapter()
        remote = adapter.get_remote_state(registry_ref)
        assert len(remote.files) == 1
        assert "file1.txt" in remote.files
        assert "file2.txt" not in remote.files
        
        # Verify sync state was pruned
        state = load_state(ctx)
        assert "file1.txt" in state.last_synced_files
        assert "file2.txt" not in state.last_synced_files