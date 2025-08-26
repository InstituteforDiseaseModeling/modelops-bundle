"""Test tag race prevention in two-phase operations."""

import os
import tempfile
import time
from pathlib import Path
import pytest
from unittest.mock import Mock, patch

from modelops_bundle.oras import OrasAdapter
from modelops_bundle.core import (
    BundleConfig, 
    FileInfo,
    PullPreview,
    PushPlan,
    TrackedFiles,
)
from modelops_bundle.ops import (
    pull_preview,
    pull_apply,
    push_plan,
    push_apply,
    save_config,
    save_tracked,
)
from modelops_bundle.context import ProjectContext
from modelops_bundle.utils import compute_digest


@pytest.fixture
def registry_ref():
    """Generate unique registry reference for tests."""
    import uuid
    return f"localhost:5555/test_races_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def test_project(tmp_path):
    """Create test project with tracked files."""
    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        
        # Create test files
        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")
        file2 = tmp_path / "subdir" / "file2.txt"
        file2.parent.mkdir(parents=True)
        file2.write_text("content2")
        
        # Initialize project context
        ctx = ProjectContext.init()
        
        # Save config
        config = BundleConfig(
            registry_ref="localhost:5555/test",
            default_tag="latest"
        )
        save_config(config, ctx)
        
        # Save tracked files
        tracked = TrackedFiles()
        tracked.add("file1.txt", "subdir/file2.txt")
        save_tracked(tracked, ctx)
        
        yield ctx, config, tracked
    finally:
        os.chdir(old_cwd)


class TestPullTagRaces:
    """Test tag race prevention during pull operations."""
    
    def test_pull_tag_moves_between_preview_and_apply(self, registry_ref, test_project):
        """Test that pull uses resolved digest even if tag moves."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push initial version
        adapter = OrasAdapter()
        files_v1 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v1 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v1,
            tag="latest",
            ctx=ctx
        )
        
        # Generate preview (resolves tag to digest_v1)
        preview = pull_preview(config, tracked, "latest", False, ctx=ctx)
        assert preview.resolved_digest == digest_v1
        assert preview.original_reference == "latest"
        
        # Push v2 to same tag (simulating concurrent update)
        (ctx.root / "file1.txt").write_text("modified content")
        files_v2 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v2 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v2,
            tag="latest",
            ctx=ctx
        )
        assert digest_v1 != digest_v2  # Tag has moved
        
        # Apply should still pull v1 (using resolved digest)
        result = pull_apply(config, tracked, preview, ctx=ctx)
        assert result.manifest_digest == digest_v1
        
        # Verify we got v1 content, not v2
        content = (ctx.root / "file1.txt").read_text()
        assert content == "content1"  # Original content
    
    def test_pull_by_digest_is_immutable(self, registry_ref, test_project):
        """Test that pulling by digest is immune to tag changes."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push v1
        adapter = OrasAdapter()
        files_v1 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v1 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v1,
            tag="mutable",
            ctx=ctx
        )
        
        # Preview using digest directly
        preview = pull_preview(config, tracked, digest_v1, False, ctx=ctx)
        assert preview.resolved_digest == digest_v1
        assert preview.original_reference == digest_v1
        
        # Push v2 to same tag
        (ctx.root / "file1.txt").write_text("changed")
        files_v2 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v2 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v2,
            tag="mutable",
            ctx=ctx
        )
        
        # Apply should still get v1
        result = pull_apply(config, tracked, preview, ctx=ctx)
        assert result.manifest_digest == digest_v1
        assert (ctx.root / "file1.txt").read_text() == "content1"
    
    def test_resolve_once_pattern(self, registry_ref, test_project):
        """Test that preview resolves tag exactly once."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push initial version
        adapter = OrasAdapter()
        files = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v1 = adapter.push_files(
            registry_ref=registry_ref,
            files=files,
            tag="latest",
            ctx=ctx
        )
        
        # Mock the OrasAdapter class to count resolution calls
        with patch('modelops_bundle.ops.OrasAdapter') as MockAdapter:
            mock_adapter = Mock()
            MockAdapter.return_value = mock_adapter
            mock_adapter.resolve_tag_to_digest.return_value = digest_v1
            mock_adapter.get_remote_state.return_value = adapter.get_remote_state(registry_ref, digest_v1)
            
            preview = pull_preview(config, tracked, "latest", False, ctx=ctx)
            
            # Should have resolved exactly once
            mock_adapter.resolve_tag_to_digest.assert_called_once_with(registry_ref, "latest")
            assert preview.resolved_digest == digest_v1
        
        # Apply should NOT resolve again (uses preview.resolved_digest)
        with patch('modelops_bundle.ops.OrasAdapter') as MockAdapter2:
            mock_adapter2 = Mock()
            MockAdapter2.return_value = mock_adapter2
            mock_adapter2.pull_files.return_value = []  # Mock successful pull
            
            result = pull_apply(config, tracked, preview, ctx=ctx)
            
            # Should not have called resolve_tag_to_digest
            assert not hasattr(mock_adapter2, 'resolve_tag_to_digest') or \
                   not mock_adapter2.resolve_tag_to_digest.called
            assert result.manifest_digest == digest_v1


class TestPushTagRaces:
    """Test tag race detection during push operations."""
    
    def test_push_detects_tag_movement(self, registry_ref, test_project):
        """Test that push detects when tag has moved since planning."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push initial version
        adapter = OrasAdapter()
        files_v1 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v1 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v1,
            tag="prod",
            ctx=ctx
        )
        
        # Create push plan (captures current tag digest)
        plan = push_plan(config, tracked, "prod", ctx=ctx)
        assert plan.tag == "prod"
        assert plan.tag_base_digest == digest_v1
        
        # Simulate concurrent push (tag moves)
        files_v2 = [
            FileInfo(
                path="file1.txt",
                digest="sha256:differentdigest",
                size=100
            )
        ]
        digest_v2 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v2,
            tag="prod",
            ctx=ctx
        )
        assert digest_v1 != digest_v2
        
        # Apply should detect tag movement and fail
        with pytest.raises(RuntimeError) as exc:
            push_apply(config, plan, force=False, ctx=ctx)
        
        assert "Tag 'prod' has moved" in str(exc.value)
        assert digest_v1[:12] in str(exc.value)
        assert "Use --force to override" in str(exc.value)
    
    def test_push_force_overrides_tag_check(self, registry_ref, test_project):
        """Test that --force allows push despite tag movement."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push initial version
        adapter = OrasAdapter()
        files_v1 = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest_v1 = adapter.push_files(
            registry_ref=registry_ref,
            files=files_v1,
            tag="dev",
            ctx=ctx
        )
        
        # Create push plan
        plan = push_plan(config, tracked, "dev", ctx=ctx)
        assert plan.tag_base_digest == digest_v1
        
        # Move the tag
        files_v2 = [
            FileInfo(
                path="file1.txt",
                digest="sha256:moved",
                size=10
            )
        ]
        adapter.push_files(
            registry_ref=registry_ref,
            files=files_v2,
            tag="dev",
            ctx=ctx
        )
        
        # Apply with force should succeed
        result = push_apply(config, plan, force=True, ctx=ctx)
        assert result  # Returns digest
    
    def test_push_to_new_tag_no_race_check(self, registry_ref, test_project):
        """Test that pushing to a new tag doesn't trigger race check."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Create push plan for new tag
        plan = push_plan(config, tracked, "v1.0.0", ctx=ctx)
        assert plan.tag == "v1.0.0"
        assert plan.tag_base_digest is None  # No existing tag
        
        # Apply should work without race check
        result = push_apply(config, plan, force=False, ctx=ctx)
        assert result  # Returns digest
        
        # Verify tag was created
        adapter = OrasAdapter()
        digest = adapter.get_digest_only(registry_ref, "v1.0.0")
        assert digest == result


class TestManifestIndexDetection:
    """Test detection of multi-platform images."""
    
    def test_index_detection_in_pull(self, registry_ref, test_project, monkeypatch):
        """Test that pull detects and rejects manifest indexes."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push normal bundle first
        adapter = OrasAdapter()
        files = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        adapter.push_files(
            registry_ref=registry_ref,
            files=files,
            tag="latest",
            ctx=ctx
        )
        
        # Mock OrasAdapter.get_manifest_with_digest to return index manifest
        original_get_manifest = OrasAdapter.get_manifest_with_digest
        
        def mock_get_manifest(self, *args, **kwargs):
            manifest, digest, raw = original_get_manifest(self, *args, **kwargs)
            # Make it look like an index
            manifest["mediaType"] = "application/vnd.oci.image.index.v1+json"
            manifest["manifests"] = [{"digest": "sha256:fake"}]
            return manifest, digest, raw
        
        monkeypatch.setattr(OrasAdapter, 'get_manifest_with_digest', mock_get_manifest)
        
        # Pull preview should detect and fail
        with pytest.raises(ValueError) as exc:
            pull_preview(config, tracked, "latest", ctx=ctx)
        
        assert "manifest index/list" in str(exc.value)
        assert "multi-platform image" in str(exc.value)
    
    def test_manifest_list_detection(self, registry_ref, test_project, monkeypatch):
        """Test detection of Docker manifest lists."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push normal bundle
        adapter = OrasAdapter()
        files = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        adapter.push_files(
            registry_ref=registry_ref,
            files=files,
            tag="latest",
            ctx=ctx
        )
        
        # Mock OrasAdapter.get_manifest_with_digest to return Docker manifest list
        original_get_manifest = OrasAdapter.get_manifest_with_digest
        
        def mock_get_manifest(self, *args, **kwargs):
            manifest, digest, raw = original_get_manifest(self, *args, **kwargs)
            # Make it look like a Docker manifest list
            manifest["mediaType"] = "application/vnd.docker.distribution.manifest.list.v2+json"
            manifest["manifests"] = [{"digest": "sha256:platform1"}]
            return manifest, digest, raw
        
        monkeypatch.setattr(OrasAdapter, 'get_manifest_with_digest', mock_get_manifest)
        
        # Should detect and fail (create new adapter to use patched method)
        adapter2 = OrasAdapter()
        with pytest.raises(ValueError) as exc:
            adapter2.get_remote_state(registry_ref, "latest")
        
        assert "manifest index/list" in str(exc.value)


class TestDigestOptimizations:
    """Test optimizations for digest operations."""
    
    def test_head_optimization_used(self, registry_ref, test_project, monkeypatch):
        """Test that HEAD request is tried before GET for digest-only."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push test file
        adapter = OrasAdapter()
        files = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        digest = adapter.push_files(
            registry_ref=registry_ref,
            files=files,
            tag="latest",
            ctx=ctx
        )
        
        # Track HEAD vs GET calls
        head_called = []
        get_called = []
        original_do_request = adapter.client.do_request
        
        def mock_do_request(*args, **kwargs):
            if len(args) > 1:
                if "HEAD" in args[1]:
                    head_called.append(True)
                elif "GET" in args[1]:
                    get_called.append(True)
            return original_do_request(*args, **kwargs)
        
        monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
        
        # get_digest_only should try HEAD first
        result = adapter.get_digest_only(registry_ref, "latest")
        assert result == digest
        assert head_called  # HEAD was attempted
        assert not get_called  # GET not needed if HEAD succeeded
    
    def test_retry_with_backoff(self, registry_ref, test_project, monkeypatch):
        """Test retry logic with exponential backoff."""
        ctx, config, tracked = test_project
        config.registry_ref = registry_ref
        
        # Push test file
        adapter = OrasAdapter()
        files = [
            FileInfo(
                path="file1.txt",
                digest=compute_digest(ctx.root / "file1.txt"),
                size=(ctx.root / "file1.txt").stat().st_size
            )
        ]
        adapter.push_files(
            registry_ref=registry_ref,
            files=files,
            tag="latest",
            ctx=ctx
        )
        
        # Mock to fail first 2 attempts
        attempts = []
        original_do_request = adapter.client.do_request
        
        def mock_do_request(*args, **kwargs):
            attempts.append(time.time())
            if len(attempts) <= 2 and "GET" in args:
                resp = Mock()
                resp.status_code = 404
                resp.raise_for_status = Mock()
                return resp
            return original_do_request(*args, **kwargs)
        
        monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
        
        # Should retry and succeed
        manifest, digest, raw = adapter.get_manifest_with_digest(
            registry_ref, "latest", retries=5
        )
        
        assert len(attempts) >= 3  # At least 3 attempts
        if len(attempts) > 1:
            # Check backoff timing (roughly)
            delay1 = attempts[1] - attempts[0]
            assert delay1 >= 0.15  # ~200ms first backoff
            if len(attempts) > 2:
                delay2 = attempts[2] - attempts[1]
                assert delay2 >= 0.3  # ~400ms second backoff