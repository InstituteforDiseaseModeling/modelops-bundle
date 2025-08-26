"""Test that manifest digest calculation is consistent and canonical."""

import os
import tempfile
from pathlib import Path
import pytest
import warnings

from modelops_bundle.oras import OrasAdapter
from modelops_bundle.core import FileInfo
from modelops_bundle.utils import compute_digest


@pytest.fixture
def registry_ref():
    """Generate unique registry reference for tests."""
    import uuid
    return f"localhost:5555/test_digest_{uuid.uuid4().hex[:12]}"


class TestDigestConsistency:
    """Test manifest digest calculation consistency."""
    
    def test_push_and_pull_digest_consistency(self, registry_ref):
        """Test that push returns same digest as get_remote_state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Create test files
                file1 = Path("file1.txt")
                file1.write_text("content1")
                Path("subdir").mkdir()
                file2 = Path("subdir/file2.txt")
                file2.write_text("content2")
                
                # Create file infos with proper digest and size
                files = [
                    FileInfo(
                        path="file1.txt",
                        digest=compute_digest(file1),
                        size=file1.stat().st_size
                    ),
                    FileInfo(
                        path="subdir/file2.txt",
                        digest=compute_digest(file2),
                        size=file2.stat().st_size
                    ),
                ]
                
                # Push and get digest
                adapter = OrasAdapter()
                push_digest = adapter.push_files(
                    registry_ref=registry_ref,
                    files=files,
                    tag="v1",
                    ctx=type('Context', (), {'root': Path.cwd()})()
                )
                
                # Get remote state and compare digests
                remote_state = adapter.get_remote_state(registry_ref, "v1")
                
                # Both digests should be identical
                assert push_digest == remote_state.manifest_digest, (
                    f"Push digest {push_digest} != remote state digest {remote_state.manifest_digest}"
                )
            finally:
                os.chdir(old_cwd)
    
    def test_manifest_digest_matches_header(self, registry_ref):
        """Test that our digest matches the registry's Docker-Content-Digest header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Create a simple test file
                test_file = Path("test.txt")
                test_file.write_text("test content")
                files = [FileInfo(
                    path="test.txt",
                    digest=compute_digest(test_file),
                    size=test_file.stat().st_size
                )]
                
                # Push to registry
                adapter = OrasAdapter()
                push_digest = adapter.push_files(
                    registry_ref=registry_ref,
                    files=files,
                    tag="latest",
                    ctx=type('Context', (), {'root': Path.cwd()})()
                )
            
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
    
    def test_digest_fallback_warning(self, registry_ref, monkeypatch):
        """Test that we warn when Docker-Content-Digest header is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Create test file
                test_file = Path("test.txt")
                test_file.write_text("test")
                files = [FileInfo(
                    path="test.txt",
                    digest=compute_digest(test_file),
                    size=test_file.stat().st_size
                )]
                
                # Push to registry
                adapter = OrasAdapter()
                adapter.push_files(
                    registry_ref=registry_ref,
                    files=files,
                    tag="test",
                    ctx=type('Context', (), {'root': Path.cwd()})()
                )
            
                # Mock the response to not have Docker-Content-Digest header
                original_do_request = adapter.client.do_request
                
                def mock_do_request(*args, **kwargs):
                    resp = original_do_request(*args, **kwargs)
                    # Remove the header if it exists
                    if hasattr(resp, 'headers') and 'Docker-Content-Digest' in resp.headers:
                        del resp.headers['Docker-Content-Digest']
                    return resp
                
                monkeypatch.setattr(adapter.client, 'do_request', mock_do_request)
            
                # Should warn about missing header
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    manifest, digest, raw = adapter.get_manifest_with_digest(registry_ref, "test")
                    
                    # Check that warning was issued
                    assert len(w) == 1
                    assert "Docker-Content-Digest" in str(w[0].message)
                    assert "using digest computed from raw manifest bytes" in str(w[0].message)
            finally:
                os.chdir(old_cwd)
    
    def test_digest_consistency_across_operations(self, registry_ref):
        """Test digest consistency across push, pull, and status operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                
                # Create test files
                app_file = Path("app.py")
                app_file.write_text("print('hello')")
                config_file = Path("config.yaml")
                config_file.write_text("key: value")
                
                files = [
                    FileInfo(
                        path="app.py",
                        digest=compute_digest(app_file),
                        size=app_file.stat().st_size
                    ),
                    FileInfo(
                        path="config.yaml",
                        digest=compute_digest(config_file),
                        size=config_file.stat().st_size
                    ),
                ]
                
                adapter = OrasAdapter()
                
                # Push and record digest
                digest1 = adapter.push_files(
                    registry_ref=registry_ref,
                    files=files,
                    tag="stable",
                    ctx=type('Context', (), {'root': Path.cwd()})()
                )
            
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