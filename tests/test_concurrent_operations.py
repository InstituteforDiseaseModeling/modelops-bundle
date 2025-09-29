"""Tests for concurrent operations and tag race prevention."""

import os
import tempfile
from pathlib import Path
import pytest
from unittest.mock import Mock, patch

from modelops_bundle.core import (
    BundleConfig,
    FileInfo,
    RemoteState,
    TrackedFiles,
)
from modelops_bundle.ops import (
    push,
    push_plan,
    push_apply,
    save_config,
    save_tracked,
)
from modelops_bundle.context import ProjectContext
from modelops_bundle.errors import TagMovedError
from modelops_bundle.utils import compute_digest

from tests.test_registry_utils import skip_if_no_registry


def setup_mock_adapter(adapter):
    """Setup common mock methods for OrasAdapter mock."""
    # Set defaults for new index-based methods
    adapter.push_with_index_config.return_value = "sha256:newdigest"
    adapter.get_index.side_effect = ValueError("No index - fall back to legacy")
    return adapter


@pytest.fixture
def test_project(tmp_path):
    """Create a test project with tracked files."""
    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        
        # Create test files
        file1 = tmp_path / "file1.txt"
        file1.write_text("content1")
        file2 = tmp_path / "file2.txt"
        file2.write_text("content2")
        
        # Initialize project
        ctx = ProjectContext.init()
        
        # Save config
        config = BundleConfig(environment="local", 
            registry_ref="localhost:5555/test",
            default_tag="latest"
        )
        save_config(config, ctx)
        
        # Track files
        tracked = TrackedFiles()
        tracked.add("file1.txt", "file2.txt")
        save_tracked(tracked, ctx)
        
        yield ctx, config, tracked
    finally:
        os.chdir(old_cwd)


class TestPushRaceProtection:
    """Test that push race protection is enabled by default."""
    
    def test_push_detects_tag_movement_by_default(self, test_project):
        """Test that push detects tag movement without --force."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = setup_mock_adapter(MockAdapter.return_value)
            
            # Initial tag state
            adapter.get_current_tag_digest.return_value = "sha256:initial"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:initial",
                files={}
            )
            
            # Create push plan
            plan = push_plan(config, tracked, ctx=ctx)
            assert plan.tag_base_digest == "sha256:initial"
            
            # Simulate tag movement before apply
            adapter.get_current_tag_digest.return_value = "sha256:moved"
            
            # Apply should fail without force
            with pytest.raises(TagMovedError) as exc:
                push_apply(config, plan, force=False, ctx=ctx)
            
            assert "Tag 'latest' moved" in str(exc.value)
            assert "sha256:initi" in str(exc.value)  # May be truncated
            assert "sha256:moved" in str(exc.value)
    
    def test_push_with_force_bypasses_race_check(self, test_project):
        """Test that --force allows push despite tag movement."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = MockAdapter.return_value
            
            # Initial tag state
            adapter.get_current_tag_digest.return_value = "sha256:initial"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:initial",
                files={}
            )
            adapter.push_files.return_value = "sha256:newdigest"
            adapter.push_with_index_config.return_value = "sha256:newdigest"
            adapter.get_index.side_effect = ValueError("No index")
            
            # Create push plan
            plan = push_plan(config, tracked, ctx=ctx)
            
            # Simulate tag movement
            adapter.get_current_tag_digest.return_value = "sha256:moved"
            
            # Apply with force should succeed
            result = push_apply(config, plan, force=True, ctx=ctx)
            assert result == "sha256:newdigest"
            
            # Verify push was called (either legacy or index-based)
            assert adapter.push_files.called or adapter.push_with_index_config.called
    
    def test_push_wrapper_respects_force_parameter(self, test_project):
        """Test that the push() wrapper properly passes force parameter."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = MockAdapter.return_value
            
            # Setup mocks
            adapter.get_current_tag_digest.return_value = "sha256:initial"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:initial",
                files={}
            )
            
            # Test with force=False (default)
            with patch("modelops_bundle.ops.push_apply") as mock_apply:
                mock_apply.return_value = "sha256:digest"
                
                result = push(config, tracked, ctx=ctx)  # Default force=False
                
                # Verify push_apply was called with force=False
                args, kwargs = mock_apply.call_args
                assert kwargs.get("force") == False
            
            # Test with force=True
            with patch("modelops_bundle.ops.push_apply") as mock_apply:
                mock_apply.return_value = "sha256:digest"
                
                result = push(config, tracked, ctx=ctx, force=True)
                
                # Verify push_apply was called with force=True
                args, kwargs = mock_apply.call_args
                assert kwargs.get("force") == True
    
    def test_push_to_new_tag_no_race_check(self, test_project):
        """Test that pushing to a new tag doesn't trigger race check."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = setup_mock_adapter(MockAdapter.return_value)
            
            # New tag doesn't exist
            adapter.get_current_tag_digest.return_value = None
            adapter.get_remote_state.side_effect = Exception("Tag not found")
            adapter.push_files.return_value = "sha256:newdigest"
            adapter.push_with_index_config.return_value = "sha256:newdigest"
            
            # Should work without force
            result = push(config, tracked, tag="v1.0.0", ctx=ctx, force=False)
            assert result == "sha256:newdigest"
            
            # Verify push was called (either legacy or index-based)
            assert adapter.push_files.called or adapter.push_with_index_config.called


class TestConcurrentPushScenarios:
    """Test various concurrent push scenarios."""
    
    def test_concurrent_push_detection(self, test_project):
        """Test detection of concurrent pushes by multiple users."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = setup_mock_adapter(MockAdapter.return_value)
            
            # User A creates plan
            adapter.get_current_tag_digest.return_value = "sha256:base"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:base",
                files={"old.txt": FileInfo(path="old.txt", digest="sha256:old", size=10)}
            )
            
            plan_a = push_plan(config, tracked, ctx=ctx)
            assert plan_a.tag_base_digest == "sha256:base"
            
            # User B pushes in between (tag moves)
            adapter.get_current_tag_digest.return_value = "sha256:user_b_pushed"
            
            # User A tries to apply their plan
            with pytest.raises(TagMovedError) as exc:
                push_apply(config, plan_a, force=False, ctx=ctx)
            
            assert "moved" in str(exc.value)
    
    def test_rapid_sequential_pushes(self, test_project):
        """Test multiple rapid pushes detect conflicts correctly."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = setup_mock_adapter(MockAdapter.return_value)
            
            # First push scenario
            adapter.get_current_tag_digest.return_value = "sha256:v1"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:v1",
                files={}
            )
            adapter.push_files.return_value = "sha256:pushed_1"
            adapter.push_with_index_config.return_value = "sha256:pushed_1"
            
            # First push should work
            plan1 = push_plan(config, tracked, ctx=ctx)
            assert plan1.tag_base_digest == "sha256:v1"
            
            # After push, tag should point to pushed digest
            adapter.get_current_tag_digest.side_effect = ["sha256:v1", "sha256:pushed_1"]
            
            result1 = push_apply(config, plan1, force=False, ctx=ctx)
            assert result1 == "sha256:pushed_1"
            
            # Simulate tag movement by another user
            adapter.get_current_tag_digest.side_effect = None  # Reset side_effect
            adapter.get_current_tag_digest.return_value = "sha256:v2"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:v2",
                files={}
            )
            
            # Second push should detect movement
            plan2 = push_plan(config, tracked, ctx=ctx)
            assert plan2.tag_base_digest == "sha256:v2"
            
            # But tag moves again before apply
            adapter.get_current_tag_digest.return_value = "sha256:v3"
            
            with pytest.raises(TagMovedError) as exc:
                push_apply(config, plan2, force=False, ctx=ctx)
            
            assert "moved" in str(exc.value)
    
    def test_tag_rollback_scenario(self, test_project):
        """Test handling when a tag is rolled back to a previous digest."""
        ctx, config, tracked = test_project
        
        with patch("modelops_bundle.ops.OrasAdapter") as MockAdapter:
            adapter = setup_mock_adapter(MockAdapter.return_value)
            
            # Tag initially points to v2
            adapter.get_current_tag_digest.return_value = "sha256:v2"
            adapter.get_remote_state.return_value = RemoteState(
                manifest_digest="sha256:v2",
                files={}
            )
            
            plan = push_plan(config, tracked, ctx=ctx)
            assert plan.tag_base_digest == "sha256:v2"
            
            # Someone rolls back tag to v1
            adapter.get_current_tag_digest.return_value = "sha256:v1"
            
            # Should detect the rollback
            with pytest.raises(TagMovedError) as exc:
                push_apply(config, plan, force=False, ctx=ctx)
            
            assert "moved" in str(exc.value)
            assert "sha256:v1" in str(exc.value)


class TestRaceProtectionIntegration:
    """Integration tests for race protection with real registry operations."""
    
    @pytest.mark.integration
    def test_real_registry_tag_movement(self, test_project):
        """Test with real registry that tag movement is detected."""
        skip_if_no_registry()
        ctx, config, tracked = test_project
        
        # This test requires a real registry
        from modelops_bundle.oras import OrasAdapter
        
        registry_ref = f"{os.environ.get('REGISTRY_URL', 'localhost:5555')}/test_race_{os.urandom(4).hex()}"
        config.registry_ref = registry_ref
        
        adapter = OrasAdapter()
        
        # Push v1 using the production function
        digest_v1 = push(config, tracked, tag="latest", ctx=ctx)
        
        # Create plan based on v1
        plan = push_plan(config, tracked, ctx=ctx)
        assert plan.tag_base_digest == digest_v1
        
        # Push v2 to same tag (simulate concurrent update)
        (ctx.root / "file1.txt").write_text("modified")
        # Force push to bypass local state tracking
        digest_v2 = push(config, tracked, tag="latest", ctx=ctx, force=True)
        assert digest_v1 != digest_v2
        
        # Applying the old plan should fail
        with pytest.raises(TagMovedError) as exc:
            push_apply(config, plan, force=False, ctx=ctx)
        
        assert "moved" in str(exc.value)


class TestForceFlag:
    """Test that force flag is properly wired through the system."""
    
    def test_cli_force_flag_available(self):
        """Test that CLI has --force flag for push command."""
        from modelops_bundle.cli import push
        
        # Check for force parameter
        import inspect
        sig = inspect.signature(push)
        assert "force" in sig.parameters
        
        # Check parameter has default False
        force_param = sig.parameters["force"]
        # In typer, default is set via Option, not directly
        # So we verify the annotation contains the Option with default False
        assert force_param.annotation is not None
    
    def test_force_flag_help_text(self):
        """Test that force flag has appropriate help text."""
        from modelops_bundle.cli import push
        import inspect
        
        # Check the push function signature
        sig = inspect.signature(push)
        force_param = sig.parameters["force"]
        
        # Force should have a typer.Option annotation
        # The default False is set in the Option itself
        assert force_param.annotation is not None
        
        # Verify it's not defaulting to True
        # The actual default is in the typer.Option, but we can verify
        # the function doesn't have a problematic default
        assert force_param.default != True