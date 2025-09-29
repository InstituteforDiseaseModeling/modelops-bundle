"""Tests for ModelOpsBundleRepository adapter implementation."""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from modelops_bundle.adapters.repository import ModelOpsBundleRepository
from modelops_bundle.core import BundleConfig
from modelops_bundle.ops import ensure_local
from tests.test_registry_utils import skip_if_no_registry


class TestModelOpsBundleRepository:
    """Unit tests for ModelOpsBundleRepository."""
    
    @pytest.fixture
    def temp_cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return cache_dir
    
    @pytest.fixture
    def mock_oras(self):
        """Mock OrasAdapter for unit tests."""
        with patch("modelops_bundle.adapters.repository.OrasAdapter") as mock:
            instance = Mock()
            mock.return_value = instance
            yield instance
    
    @pytest.fixture
    def repository(self, temp_cache_dir, mock_oras):
        """Create a repository instance with mocked dependencies."""
        return ModelOpsBundleRepository(
            registry_ref="ghcr.io/test/models",
            cache_dir=str(temp_cache_dir),
            cache_structure="digest",
            default_tag="latest"
        )
    
    def test_init(self, temp_cache_dir):
        """Test repository initialization."""
        repo = ModelOpsBundleRepository(
            registry_ref="ghcr.io/test/models",
            cache_dir=str(temp_cache_dir),
            cache_structure="digest_short",
            default_tag="v1.0"
        )
        
        assert repo.registry_ref == "ghcr.io/test/models"
        assert repo.cache_dir == temp_cache_dir
        assert repo.cache_structure == "digest_short"
        assert repo.config.default_tag == "v1.0"
        assert temp_cache_dir.exists()
    
    def test_reject_file_urls(self, repository):
        """Test that file:// URLs are properly rejected."""
        with pytest.raises(ValueError, match="file:// URLs not supported"):
            repository.ensure_local("file:///tmp/bundle.tar")
    
    def test_empty_reference(self, repository):
        """Test that empty references are rejected."""
        with pytest.raises(ValueError, match="Bundle reference cannot be empty"):
            repository.ensure_local("")
    
    class TestReferenceResolution:
        """Test different bundle reference formats."""
        
        def test_resolve_digest_reference(self, repository, mock_oras):
            """Test resolving a reference that's already a digest."""
            digest = "abc123def456789"
            result = repository._resolve_to_digest(f"sha256:{digest}")
            assert result == digest
            # Should not call OrasAdapter for digest refs
            mock_oras.resolve_tag_to_digest.assert_not_called()
        
        def test_resolve_tag_reference(self, repository, mock_oras):
            """Test resolving a tag to a digest."""
            mock_oras.resolve_tag_to_digest.return_value = "sha256:xyz789"
            
            result = repository._resolve_to_digest("v1.0.0")
            
            assert result == "xyz789"
            mock_oras.resolve_tag_to_digest.assert_called_once_with(
                "ghcr.io/test/models", "v1.0.0"
            )
        
        def test_resolve_oci_url(self, repository, mock_oras):
            """Test resolving a full OCI URL."""
            mock_oras.resolve_tag_to_digest.return_value = "sha256:abc123"
            
            result = repository._resolve_to_digest("oci://ghcr.io/other/model:v2")
            
            assert result == "abc123"
            mock_oras.resolve_tag_to_digest.assert_called_once_with(
                "ghcr.io/test/models", "ghcr.io/other/model:v2"
            )
        
        def test_resolve_tag_failure(self, repository, mock_oras):
            """Test handling of resolution failures."""
            mock_oras.resolve_tag_to_digest.side_effect = Exception("Registry error")
            
            with pytest.raises(ValueError, match="Failed to resolve bundle reference"):
                repository._resolve_to_digest("bad-tag")
    
    class TestCachePathGeneration:
        """Test different cache structure strategies."""
        
        def test_cache_path_full_digest(self, temp_cache_dir):
            """Test default full digest cache structure."""
            repo = ModelOpsBundleRepository(
                registry_ref="test",
                cache_dir=str(temp_cache_dir),
                cache_structure="digest"
            )
            
            digest = "abc123def456789012345678901234567890123456789012345678901234"
            path = repo._get_cache_path(digest)
            
            assert path == temp_cache_dir / digest
        
        def test_cache_path_short_digest(self, temp_cache_dir):
            """Test short digest cache structure (Docker-style)."""
            repo = ModelOpsBundleRepository(
                registry_ref="test",
                cache_dir=str(temp_cache_dir),
                cache_structure="digest_short"
            )
            
            digest = "abc123def456789012345678901234567890123456789012345678901234"
            path = repo._get_cache_path(digest)
            
            assert path == temp_cache_dir / "abc123def456"
        
        def test_cache_path_nested_digest(self, temp_cache_dir):
            """Test nested digest cache structure (Git-style)."""
            repo = ModelOpsBundleRepository(
                registry_ref="test",
                cache_dir=str(temp_cache_dir),
                cache_structure="digest_nested"
            )
            
            digest = "abc123def456789012345678901234567890123456789012345678901234"
            path = repo._get_cache_path(digest)
            
            expected = temp_cache_dir / "ab" / "c1" / "23def456789012345678901234567890123456789012345678901234"
            assert path == expected
    
    class TestCacheManagement:
        """Test cache hit/miss and cleanup behavior."""
        
        def test_cache_hit_with_marker(self, repository, temp_cache_dir, mock_oras):
            """Test cache hit when bundle exists with completeness marker."""
            digest = "abc123"
            bundle_path = temp_cache_dir / digest
            bundle_path.mkdir()
            
            # Create completeness marker
            marker = bundle_path / ".modelops_bundle_complete"
            marker.touch()
            
            # Create some content
            (bundle_path / "model.pkl").write_text("model data")
            
            mock_oras.resolve_tag_to_digest.return_value = f"sha256:{digest}"
            
            result_digest, result_path = repository.ensure_local("v1.0")
            
            assert result_digest == digest
            assert result_path == bundle_path
            # Should not fetch since cached
            assert mock_oras.resolve_tag_to_digest.call_count == 1
        
        def test_cache_miss_no_directory(self, repository, temp_cache_dir, mock_oras):
            """Test cache miss when directory doesn't exist."""
            digest = "newbundle123"
            mock_oras.resolve_tag_to_digest.return_value = f"sha256:{digest}"

            # Mock the get_index and pull_selected operations
            mock_index = Mock()
            mock_index.files = {"file1.txt": Mock(), "file2.txt": Mock()}
            mock_oras.get_index.return_value = mock_index
            mock_oras.pull_selected.return_value = None

            result_digest, result_path = repository.ensure_local("v2.0")

            assert result_digest == digest
            assert result_path == temp_cache_dir / digest
            mock_oras.get_index.assert_called_once()
            mock_oras.pull_selected.assert_called_once()
        
        def test_cache_incomplete_cleanup(self, repository, temp_cache_dir, mock_oras):
            """Test cleanup of incomplete bundle (no marker)."""
            digest = "incomplete123"
            bundle_path = temp_cache_dir / digest
            bundle_path.mkdir()

            # Create content but no marker
            (bundle_path / "partial.data").write_text("incomplete")

            mock_oras.resolve_tag_to_digest.return_value = f"sha256:{digest}"

            # Mock the get_index and pull_selected operations
            mock_index = Mock()
            mock_index.files = {"file1.txt": Mock()}
            mock_oras.get_index.return_value = mock_index
            mock_oras.pull_selected.return_value = None

            result_digest, result_path = repository.ensure_local("v3.0")

            # Should have cleaned up and re-fetched
            mock_oras.get_index.assert_called_once()
            mock_oras.pull_selected.assert_called_once()
            assert result_digest == digest
        
        def test_is_cached_checks_marker(self, repository, temp_cache_dir):
            """Test that _is_cached properly checks for completeness marker."""
            bundle_path = temp_cache_dir / "test"
            
            # No directory
            assert not repository._is_cached(bundle_path)
            
            # Directory but no marker
            bundle_path.mkdir()
            assert not repository._is_cached(bundle_path)
            
            # Directory with marker
            (bundle_path / ".modelops_bundle_complete").touch()
            assert repository._is_cached(bundle_path)
    
    class TestFetchBundle:
        """Test bundle fetching behavior."""
        
        def test_fetch_success(self, repository, temp_cache_dir, mock_oras):
            """Test successful bundle fetch."""
            digest = "fetched123"
            bundle_path = temp_cache_dir / digest

            # Mock the get_index and pull_selected operations
            mock_index = Mock()
            mock_index.files = {"file1.txt": Mock(), "file2.txt": Mock()}
            mock_oras.get_index.return_value = mock_index
            mock_oras.pull_selected.return_value = None

            repository._fetch_bundle("v4.0", digest, bundle_path)

            mock_oras.get_index.assert_called_once()
            mock_oras.pull_selected.assert_called_once()
            # Check marker was created
            assert (bundle_path / ".modelops_bundle_complete").exists()
        
        def test_fetch_failure_cleanup(self, repository, temp_cache_dir, mock_oras):
            """Test cleanup on fetch failure."""
            digest = "failed123"
            bundle_path = temp_cache_dir / digest
            bundle_path.mkdir()
            (bundle_path / "temp.file").touch()
            
            # Mock get_index to fail
            mock_oras.get_index.side_effect = Exception("Network error")

            with pytest.raises(ValueError, match="Failed to fetch bundle"):
                repository._fetch_bundle("v5.0", digest, bundle_path)
                
                # Directory should be cleaned up
                assert not bundle_path.exists()
    
    def test_compute_digest(self, repository, temp_cache_dir):
        """Test local digest computation."""
        bundle_path = temp_cache_dir / "test_bundle"
        bundle_path.mkdir()
        
        # Create some files
        (bundle_path / "file1.txt").write_text("content1")
        (bundle_path / "file2.txt").write_text("content2")
        subdir = bundle_path / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("content3")
        
        # Add completeness marker (should be excluded)
        (bundle_path / ".modelops_bundle_complete").touch()
        
        digest1 = repository.compute_digest(bundle_path)
        
        # Should be deterministic
        digest2 = repository.compute_digest(bundle_path)
        assert digest1 == digest2
        
        # Should change with content
        (bundle_path / "file1.txt").write_text("modified")
        digest3 = repository.compute_digest(bundle_path)
        assert digest3 != digest1


@pytest.mark.integration
class TestModelOpsBundleRepositoryIntegration:
    """Integration tests with real registry."""
    
    @pytest.fixture
    def registry_ref(self):
        """Get test registry reference."""
        import uuid
        registry = os.environ.get("REGISTRY_URL", "localhost:5555")
        unique_id = str(uuid.uuid4())[:8]
        return f"{registry}/test_adapter_{unique_id}"
    
    @pytest.fixture
    def sample_bundle(self, tmp_path):
        """Create a sample bundle for testing."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        
        # Create sample content
        (bundle_dir / "model.pkl").write_bytes(b"model binary data")
        (bundle_dir / "config.yaml").write_text("param: value")
        data_dir = bundle_dir / "data"
        data_dir.mkdir()
        (data_dir / "train.csv").write_text("id,value\n1,2\n3,4")
        
        return bundle_dir
    
    def test_end_to_end_workflow(self, tmp_path, registry_ref, sample_bundle):
        """Test complete workflow: push with modelops-bundle, pull with adapter."""
        skip_if_no_registry()
        
        # Step 1: Push bundle using modelops-bundle
        from modelops_bundle.core import BundleConfig
        from modelops_bundle.ops import push as ops_push
        
        config = BundleConfig(environment="local", registry_ref=registry_ref)
        
        # Initialize and push
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(sample_bundle)
            
            # Track files
            from modelops_bundle.ops import save_tracked
            from modelops_bundle.core import TrackedFiles
            from modelops_bundle.context import ProjectContext

            # Initialize bundle directory
            bundle_dir = sample_bundle / ".modelops-bundle"
            bundle_dir.mkdir(exist_ok=True)
            config_file = bundle_dir / "config.yaml"
            import yaml
            config_file.write_text(yaml.safe_dump(config.model_dump(), default_flow_style=False))

            tracked = TrackedFiles()
            for file_path in sample_bundle.rglob("*"):
                if file_path.is_file() and ".modelops-bundle" not in str(file_path):
                    rel_path = file_path.relative_to(sample_bundle)
                    tracked.files.add(str(rel_path))

            ctx = ProjectContext(start_path=sample_bundle)
            save_tracked(tracked, ctx)
            
            # Push to registry
            push_result = ops_push(
                config=config,
                tracked=tracked,
                tag="test-v1",
                ctx=ctx
            )
            
            pushed_digest = push_result  # push returns the digest string directly
            
        finally:
            os.chdir(original_cwd)
        
        # Step 2: Use adapter to pull
        cache_dir = tmp_path / "adapter_cache"
        repo = ModelOpsBundleRepository(
            registry_ref=registry_ref,
            cache_dir=str(cache_dir),
            cache_structure="digest_short"
        )
        
        # Test 1: Pull by tag
        digest1, path1 = repo.ensure_local("test-v1")
        assert digest1 in pushed_digest  # digest might have sha256: prefix
        assert path1.exists()
        assert (path1 / "model.pkl").exists()
        assert (path1 / "config.yaml").read_text() == "param: value"
        assert (path1 / "data" / "train.csv").exists()
        
        # Test 2: Pull by digest (should hit cache)
        digest2, path2 = repo.ensure_local(f"sha256:{digest1}")
        assert digest2 == digest1
        assert path2 == path1
        
        # Test 3: Verify cache structure
        expected_cache_path = cache_dir / digest1[:12]
        assert expected_cache_path == path1
    
    def test_concurrent_access(self, tmp_path, registry_ref, sample_bundle):
        """Test concurrent access to same bundle."""
        skip_if_no_registry()
        
        # First push a bundle
        from modelops_bundle.core import BundleConfig, TrackedFiles
        from modelops_bundle.ops import push as ops_push, save_tracked
        from modelops_bundle.context import ProjectContext

        config = BundleConfig(environment="local", registry_ref=registry_ref)

        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(sample_bundle)

            # Initialize bundle directory
            bundle_dir = sample_bundle / ".modelops-bundle"
            bundle_dir.mkdir(exist_ok=True)
            config_file = bundle_dir / "config.yaml"
            import yaml
            config_file.write_text(yaml.safe_dump(config.model_dump(), default_flow_style=False))

            tracked = TrackedFiles()
            for file_path in sample_bundle.rglob("*"):
                if file_path.is_file() and ".modelops-bundle" not in str(file_path):
                    rel_path = file_path.relative_to(sample_bundle)
                    tracked.files.add(str(rel_path))

            ctx = ProjectContext(start_path=sample_bundle)
            save_tracked(tracked, ctx)
            push_result = ops_push(config, tracked, "concurrent-test", ctx)
            
        finally:
            os.chdir(original_cwd)
        
        # Now test concurrent pulls
        cache_dir = tmp_path / "concurrent_cache"
        
        import threading
        results = []
        errors = []
        
        def pull_bundle():
            try:
                repo = ModelOpsBundleRepository(
                    registry_ref=registry_ref,
                    cache_dir=str(cache_dir)
                )
                digest, path = repo.ensure_local("concurrent-test")
                results.append((digest, path))
            except Exception as e:
                errors.append(e)
        
        # Launch multiple threads
        threads = []
        for _ in range(5):
            t = threading.Thread(target=pull_bundle)
            threads.append(t)
            t.start()
        
        # Wait for all to complete
        for t in threads:
            t.join()
        
        # All should succeed with same result
        assert len(errors) == 0
        assert len(results) == 5
        
        # All should have same digest and path
        first_digest, first_path = results[0]
        for digest, path in results[1:]:
            assert digest == first_digest
            assert path == first_path