"""Test BundleService functionality."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from modelops_bundle.bundle_service import BundleService, BundleDeps
from modelops_bundle.context import ProjectContext
from modelops_bundle.core import BundleConfig, PushPlan, PullPreview, TrackedFiles, SyncState
from modelops_bundle.oras import OrasAdapter
from modelops_bundle.service_types import (
    AddResult,
    RemoveResult,
    PushResult,
    StatusReport,
    ProgressCallback,
)


class MockAdapter:
    """Mock OrasAdapter for testing."""

    def __init__(self, auth_provider=None):
        """Initialize mock adapter, ignoring auth_provider for testing."""
        pass

    def resolve_tag_to_digest(self, registry_ref, tag):
        return f"sha256:mock_{tag}"
    
    def get_remote_state(self, registry_ref, digest):
        from modelops_bundle.core import RemoteState, FileInfo
        return RemoteState(
            digest=digest,
            files={"remote.txt": FileInfo(path="remote.txt", size=100, digest="sha256:remote")}
        )
    
    def get_current_tag_digest(self, registry_ref, tag):
        return f"sha256:current_{tag}"


@pytest.fixture
def mock_deps():
    """Provide mock dependencies for testing."""
    mock_ctx = Mock(spec=ProjectContext)
    mock_adapter = MockAdapter()
    mock_ctx.root = Path("/test")
    mock_ctx.should_ignore = Mock(return_value=False)
    
    # Add path mocks with proper Path-like behavior for config
    mock_config_path = Mock()
    mock_config_path.exists.return_value = True
    # Create a mock file object that returns valid YAML
    mock_file = Mock()
    mock_file.read.return_value = 'registry_ref: test.com/repo\ndefault_tag: latest\n'
    mock_file.__enter__ = Mock(return_value=mock_file)
    mock_file.__exit__ = Mock(return_value=None)
    mock_config_path.open = Mock(return_value=mock_file)
    mock_ctx.config_path = mock_config_path
    
    # Add mock for tracked files
    mock_tracked_path = Mock()
    mock_tracked_path.exists.return_value = True
    mock_tracked_path.read_text.return_value = ""
    mock_ctx.tracked_path = mock_tracked_path
    
    # Add mock for state
    mock_state_path = Mock()
    mock_state_path.exists.return_value = True
    mock_state_path.read_text.return_value = '{"last_push_digest": null, "last_pull_digest": null, "last_synced_files": {}}'
    mock_ctx.state_path = mock_state_path
    
    return BundleDeps(
        ctx=mock_ctx,
        adapter=mock_adapter,
        now=lambda: 1234567890.0,
        blob_store_factory=Mock()
    )


@pytest.fixture
def service_with_mocks(mock_deps):
    """Create service with mocked dependencies."""
    return BundleService(deps=mock_deps)


@pytest.fixture
def real_service(tmp_path):
    """Create service with real filesystem."""
    ctx = ProjectContext.init(tmp_path)  # Fix: use correct parameter name
    deps = BundleDeps(ctx=ctx, adapter=MockAdapter())
    return BundleService(deps=deps)


class TestBundleServiceInit:
    """Test service initialization."""
    
    def test_service_default_init(self):
        """Test service initializes with defaults."""
        with patch('modelops_bundle.bundle_service.load_config') as mock_load:
            mock_load.return_value = Mock(spec=BundleConfig, registry_ref="test")
            with patch('modelops_bundle.auth.get_auth_provider') as mock_auth:
                mock_auth.return_value = None
                with patch('modelops_bundle.bundle_service.ProjectContext') as mock_ctx_class:
                    mock_ctx = Mock()
                    mock_ctx_class.return_value = mock_ctx

                    service = BundleService()
                    assert service.deps.ctx == mock_ctx
                    assert isinstance(service.deps.adapter, OrasAdapter)
    
    def test_service_with_deps(self, mock_deps):
        """Test service accepts custom dependencies."""
        service = BundleService(deps=mock_deps)
        assert service.deps == mock_deps
    
    def test_fresh_state_loading(self, service_with_mocks):
        """Test that state is loaded fresh each time."""
        mock_config = Mock(spec=BundleConfig)
        
        with patch('modelops_bundle.bundle_service.load_config') as mock_load_config:
            mock_load_config.return_value = mock_config
            
            # Access config twice
            config1 = service_with_mocks.config
            config2 = service_with_mocks.config
            
            # Should load fresh each time (no caching)
            assert mock_load_config.call_count == 2
            assert config1 == mock_config
            assert config2 == mock_config


class TestHighLevelOperations:
    """Test high-level service operations with real filesystem."""
    
    def test_add_files_single(self, real_service, tmp_path):
        """Test adding a single file."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        result = real_service.add_files(["test.txt"])
        
        assert isinstance(result, AddResult)
        assert "test.txt" in result.added
        assert result.total_size > 0
    
    def test_add_files_recursive(self, real_service, tmp_path):
        """Test adding directory recursively."""
        # Create test directory with files
        (tmp_path / "dir").mkdir()
        (tmp_path / "dir" / "file1.txt").write_text("content1")
        (tmp_path / "dir" / "file2.txt").write_text("content2")
        (tmp_path / "dir" / "subdir").mkdir()
        (tmp_path / "dir" / "subdir" / "file3.txt").write_text("content3")
        
        result = real_service.add_files(["dir"], recursive=True)
        
        assert isinstance(result, AddResult)
        assert len(result.added) == 3
        assert "dir/file1.txt" in result.added
        assert "dir/subdir/file3.txt" in result.added
    
    def test_add_files_already_tracked(self, real_service, tmp_path):
        """Test adding already tracked files."""
        # Create and add a file
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        # Add it twice
        result1 = real_service.add_files(["test.txt"])
        result2 = real_service.add_files(["test.txt"])
        
        assert "test.txt" in result1.added
        assert "test.txt" in result2.already_tracked
        assert len(result2.added) == 0
    
    def test_remove_files(self, real_service, tmp_path):
        """Test removing files from tracking."""
        # Create and add files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")
        real_service.add_files(["file1.txt", "file2.txt"])
        
        # Remove one
        result = real_service.remove_files(["file1.txt"])
        
        assert isinstance(result, RemoveResult)
        assert "file1.txt" in result.removed
        assert len(result.not_tracked) == 0
    
    def test_remove_files_pattern(self, real_service, tmp_path):
        """Test removing files with pattern."""
        # Create and add files
        (tmp_path / "test1.txt").write_text("content1")
        (tmp_path / "test2.txt").write_text("content2")
        (tmp_path / "keep.md").write_text("content3")
        real_service.add_files(["test1.txt", "test2.txt", "keep.md"])
        
        # Remove with pattern
        result = real_service.remove_files(["test*.txt"])
        
        assert len(result.removed) == 2
        assert "test1.txt" in result.removed
        assert "test2.txt" in result.removed


class TestProgressCallbacks:
    """Test progress callback support."""
    
    def test_progress_callback_interface(self):
        """Test that ProgressCallback protocol is defined correctly."""
        class MyProgress:
            def on_file_start(self, path: str, size: int) -> None:
                pass
            
            def on_file_complete(self, path: str) -> None:
                pass
            
            def on_file_error(self, path: str, error: str) -> None:
                pass
        
        progress = MyProgress()
        # Should be compatible with ProgressCallback protocol
        assert hasattr(progress, 'on_file_start')
        assert hasattr(progress, 'on_file_complete')
        assert hasattr(progress, 'on_file_error')