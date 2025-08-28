"""Test that manifest digest calculation is consistent and canonical."""

import os
import tempfile
from pathlib import Path
import pytest
import warnings

from modelops_bundle.oras import OrasAdapter
from modelops_bundle.core import BundleConfig, FileInfo, TrackedFiles
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import push, save_config, save_tracked
from modelops_bundle.utils import compute_digest
from modelops_bundle.errors import UnsupportedArtifactError

from tests.test_registry_utils import skip_if_no_registry


@pytest.fixture
def registry_ref():
    """Generate unique registry reference for tests."""
    import uuid
    return f"localhost:5555/test_digest_{uuid.uuid4().hex[:12]}"


class TestDigestConsistency:
    """Test manifest digest calculation consistency."""
    
    def test_push_and_pull_digest_consistency(self, registry_ref):
        """Test that push returns same digest as get_remote_state."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create test files
                file1 = Path("file1.txt")
                file1.write_text("content1")
                Path("subdir").mkdir()
                file2 = Path("subdir/file2.txt")
                file2.write_text("content2")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="v1"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("file1.txt", "subdir/file2.txt")
                save_tracked(tracked, ctx)
                
                # Push using production function
                push_digest = push(config, tracked, tag="v1", ctx=ctx)
                
                # Get remote state and compare digests
                adapter = OrasAdapter()
                remote_state = adapter.get_remote_state(registry_ref, "v1")
                
                # Both digests should be identical
                assert push_digest == remote_state.manifest_digest, (
                    f"Push digest {push_digest} != remote state digest {remote_state.manifest_digest}"
                )
            finally:
                os.chdir(old_cwd)
    
    def test_manifest_digest_matches_header(self, registry_ref):
        """Test that our digest matches the registry's Docker-Content-Digest header."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create a simple test file
                test_file = Path("test.txt")
                test_file.write_text("test content")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="latest"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("test.txt")
                save_tracked(tracked, ctx)
                
                # Push to registry
                push_digest = push(config, tracked, tag="latest", ctx=ctx)
                
                adapter = OrasAdapter()
            
                # Get manifest with digest using new method
                manifest, header_digest, raw_bytes = adapter.get_manifest_with_digest(
                    registry_ref, "latest"
                )
                
                # All should match
                assert push_digest == header_digest, (
                    f"Push digest {push_digest} != header digest {header_digest}"
                )
            
                # Also verify with remote state
                remote_state = adapter.get_remote_state(registry_ref, "latest")
                assert remote_state.manifest_digest == header_digest, (
                    f"Remote state digest {remote_state.manifest_digest} != header digest {header_digest}"
                )
            finally:
                os.chdir(old_cwd)
    
    def test_digest_fallback_warning(self, registry_ref, monkeypatch, caplog):
        """Test that we warn when Docker-Content-Digest header is missing."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create test file
                test_file = Path("test.txt")
                test_file.write_text("test")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="test"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("test.txt")
                save_tracked(tracked, ctx)
                
                # Push to registry
                push(config, tracked, tag="test", ctx=ctx)
            
                # Create adapter for testing
                adapter = OrasAdapter()
            
                # Mock the response to not have Docker-Content-Digest header
                original_do_request = adapter.client.do_request
                
                def mock_do_request(*args, **kwargs):
                    resp = original_do_request(*args, **kwargs)
                    # Remove the header if it exists
                    if hasattr(resp, 'headers') and 'Docker-Content-Digest' in resp.headers:
                        del resp.headers['Docker-Content-Digest']
                    return resp
                
                monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
            
                # Should log warning about missing header
                import logging
                
                # Use caplog to capture log messages
                caplog.set_level(logging.WARNING, logger='modelops_bundle.oras')
                
                manifest, digest, raw = adapter.get_manifest_with_digest(registry_ref, "test")
                
                # Check that warning was logged
                assert len(caplog.records) > 0
                assert any("Docker-Content-Digest" in record.message for record in caplog.records)
                assert any("using digest computed from raw manifest bytes" in record.message for record in caplog.records)
            finally:
                os.chdir(old_cwd)
    
    def test_digest_consistency_across_operations(self, registry_ref):
        """Test digest consistency across push, pull, and status operations."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create test files
                app_file = Path("app.py")
                app_file.write_text("print('hello')")
                config_file = Path("config.yaml")
                config_file.write_text("key: value")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="stable"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("app.py", "config.yaml")
                save_tracked(tracked, ctx)
                
                # Push and record digest
                digest1 = push(config, tracked, tag="stable", ctx=ctx)
                
                adapter = OrasAdapter()
            
                # Get manifest directly
                manifest, digest2, _ = adapter.get_manifest_with_digest(registry_ref, "stable")
                
                # Get through remote state
                remote_state = adapter.get_remote_state(registry_ref, "stable")
                digest3 = remote_state.manifest_digest
                
                # All digests must match
                assert digest1 == digest2 == digest3, (
                    f"Digest mismatch: push={digest1}, manifest={digest2}, remote_state={digest3}"
                )
                
                # Verify it's a proper sha256 digest
                assert digest1.startswith("sha256:")
                assert len(digest1) == 71  # "sha256:" + 64 hex chars
            finally:
                os.chdir(old_cwd)
    
    def test_head_optimization_for_digest(self, registry_ref):
        """Test that HEAD optimization works for getting digest only."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create and push a test file
                test_file = Path("test.txt")
                test_file.write_text("test")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="head-test"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("test.txt")
                save_tracked(tracked, ctx)
                
                # Push to registry
                push_digest = push(config, tracked, tag="head-test", ctx=ctx)
                
                adapter = OrasAdapter()
                
                # Use get_digest_only which should try HEAD first
                digest_only = adapter.get_digest_only(registry_ref, "head-test")
                
                # Should match the push digest
                assert digest_only == push_digest
                assert digest_only.startswith("sha256:")
            finally:
                os.chdir(old_cwd)
    
    def test_retry_on_eventual_consistency(self, registry_ref, monkeypatch):
        """Test retry logic handles eventual consistency after push."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create and push a test file
                test_file = Path("test.txt")
                test_file.write_text("consistency test")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="retry-test"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("test.txt")
                save_tracked(tracked, ctx)
                
                # Push to registry
                push(config, tracked, tag="retry-test", ctx=ctx)
                
                adapter = OrasAdapter()
                
                # Mock to simulate 404 on first attempt, success on second
                original_do_request = adapter.client.do_request
                call_count = [0]
                
                def mock_do_request(*args, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 1 and "GET" in args:
                        # Simulate 404 on first GET
                        resp = type('Response', (), {
                            'status_code': 404,
                            'raise_for_status': lambda: None
                        })()
                        return resp
                    return original_do_request(*args, **kwargs)
                
                monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
                
                # Should retry and succeed
                manifest, digest, raw = adapter.get_manifest_with_digest(
                    registry_ref, "retry-test", retries=3
                )
                
                assert manifest is not None
                assert digest.startswith("sha256:")
                # Should have made at least 2 attempts
                assert call_count[0] >= 2
            finally:
                os.chdir(old_cwd)
    
    def test_index_manifest_detection(self, registry_ref, monkeypatch):
        """Test that index/manifest list is detected and raises error."""
        skip_if_no_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Initialize project
                ctx = ProjectContext.init()
                
                # Create normal bundle first
                test_file = Path("test.txt")
                test_file.write_text("test")
                
                # Set up config and tracked files
                config = BundleConfig(
                    registry_ref=registry_ref,
                    default_tag="normal"
                )
                save_config(config, ctx)
                
                tracked = TrackedFiles()
                tracked.add("test.txt")
                save_tracked(tracked, ctx)
                
                # Push to registry
                push(config, tracked, tag="normal", ctx=ctx)
                
                adapter = OrasAdapter()
                
                # Mock to return an index manifest
                original_do_request = adapter.client.do_request
                
                def mock_do_request(*args, **kwargs):
                    resp = original_do_request(*args, **kwargs)
                    # Check if this is a GET request for manifest
                    if len(args) > 1 and "GET" in args[1] and "manifests" in args[0]:
                        # Inject index manifest structure
                        original_json = resp.json
                        def mock_json():
                            data = original_json()
                            # Make it look like an index
                            data["mediaType"] = "application/vnd.oci.image.index.v1+json"
                            data["manifests"] = [{"digest": "sha256:fake"}]
                            return data
                        resp.json = mock_json
                    return resp
                
                monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
                
                # This should detect index and raise error
                with pytest.raises(UnsupportedArtifactError) as exc:
                    adapter.get_manifest_with_digest(registry_ref, "normal")
                
                assert "normal" in str(exc.value)
                assert "index" in str(exc.value).lower()
            finally:
                os.chdir(old_cwd)