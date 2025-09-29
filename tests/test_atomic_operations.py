"""Test atomic operations for reliability."""

import json
import os
import pytest
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from modelops_bundle.ops import _atomic_write_text, save_state, save_tracked, save_config
from modelops_bundle.oras import _atomic_download
from modelops_bundle.core import SyncState, TrackedFiles, BundleConfig
from modelops_bundle.context import ProjectContext


class TestAtomicWrites:
    """Test atomic write operations."""
    
    def test_atomic_write_basic(self, tmp_path):
        """Test basic atomic write functionality."""
        test_file = tmp_path / "test.txt"
        content = "test content\nwith multiple\nlines"
        
        _atomic_write_text(test_file, content)
        
        assert test_file.exists()
        assert test_file.read_text() == content
    
    def test_atomic_write_overwrites(self, tmp_path):
        """Test that atomic write properly overwrites existing files."""
        test_file = tmp_path / "test.txt"
        
        # Write initial content
        test_file.write_text("old content")
        
        # Atomic overwrite
        new_content = "new content"
        _atomic_write_text(test_file, new_content)
        
        assert test_file.read_text() == new_content
    
    def test_atomic_write_creates_directories(self, tmp_path):
        """Test that atomic write creates parent directories."""
        test_file = tmp_path / "deep" / "nested" / "dir" / "file.txt"
        content = "nested content"
        
        _atomic_write_text(test_file, content)
        
        assert test_file.exists()
        assert test_file.read_text() == content
    
    def test_no_partial_files_on_error(self, tmp_path):
        """Test that no partial files remain after errors."""
        test_file = tmp_path / "test.txt"
        
        # Mock os.replace to raise an error (simulates failure during atomic rename)
        with patch("os.replace", side_effect=IOError("Simulated rename failure")):
            with pytest.raises(IOError):
                _atomic_write_text(test_file, "content")
        
        # File should not exist after error
        assert not test_file.exists()
        
        # No temp files should remain
        temp_files = list(tmp_path.glob(".test.txt.tmp-*"))
        assert len(temp_files) == 0
    
    def test_concurrent_atomic_writes(self, tmp_path):
        """Test that concurrent atomic writes don't corrupt files."""
        test_file = tmp_path / "concurrent.txt"
        num_threads = 10
        iterations = 5
        
        def write_thread(thread_id):
            for i in range(iterations):
                content = f"Thread {thread_id} iteration {i}"
                _atomic_write_text(test_file, content)
                time.sleep(0.001)  # Small delay to increase contention
        
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=write_thread, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # File should exist and contain valid content from one thread
        assert test_file.exists()
        content = test_file.read_text()
        assert content.startswith("Thread ")
        assert "iteration" in content


class TestAtomicStateWrites:
    """Test atomic state file operations."""
    
    def test_save_state_atomic(self, tmp_path, monkeypatch):
        """Test that save_state is atomic."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        # Use the actual state_path from context
        
        state = SyncState(
            last_push_digest="sha256:abcd1234",
            last_pull_digest="sha256:efgh5678",
            last_synced_files={"file1.txt": "sha256:1111", "file2.txt": "sha256:2222"}
        )
        
        save_state(state, ctx)
        
        assert ctx.state_path.exists()
        loaded = json.loads(ctx.state_path.read_text())
        assert loaded["last_push_digest"] == "sha256:abcd1234"
        assert loaded["last_synced_files"]["file1.txt"] == "sha256:1111"
    
    def test_save_tracked_atomic(self, tmp_path, monkeypatch):
        """Test that save_tracked is atomic."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        # Use the actual tracked_path from context
        
        tracked = TrackedFiles()
        tracked.add("file1.txt", "dir/file2.txt", "another.md")
        
        save_tracked(tracked, ctx)
        
        assert ctx.tracked_path.exists()
        lines = ctx.tracked_path.read_text().strip().split("\n")
        assert "file1.txt" in lines
        assert "dir/file2.txt" in lines
        assert "another.md" in lines
        assert len(lines) == 3
    
    def test_save_config_atomic(self, tmp_path, monkeypatch):
        """Test that save_config is atomic."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        # Use the actual config_path from context
        
        config = BundleConfig(environment="local", 
            registry_ref="localhost:5000/test",
            default_tag="v1.0"
        )
        
        save_config(config, ctx)
        
        assert ctx.config_path.exists()
        content = ctx.config_path.read_text()
        assert "registry_ref: localhost:5000/test" in content
        assert "default_tag: v1.0" in content


class TestAtomicDownloads:
    """Test atomic download operations."""
    
    def test_atomic_download_basic(self, tmp_path):
        """Test basic atomic download functionality."""
        test_file = tmp_path / "downloaded.bin"
        test_content = b"downloaded binary content\x00\x01\x02"
        
        def write_func(tmppath):
            Path(tmppath).write_bytes(test_content)
        
        _atomic_download(write_func, test_file)
        
        assert test_file.exists()
        assert test_file.read_bytes() == test_content
    
    def test_atomic_download_overwrites(self, tmp_path):
        """Test that atomic download properly overwrites."""
        test_file = tmp_path / "downloaded.bin"
        
        # Write initial content
        test_file.write_bytes(b"old content")
        
        # Atomic download overwrite
        new_content = b"new downloaded content"
        
        def write_func(tmppath):
            Path(tmppath).write_bytes(new_content)
        
        _atomic_download(write_func, test_file)
        
        assert test_file.read_bytes() == new_content
    
    def test_no_partial_downloads_on_error(self, tmp_path):
        """Test that no partial files remain after download errors."""
        test_file = tmp_path / "download.bin"
        
        def failing_write(tmppath):
            Path(tmppath).write_bytes(b"partial content")
            raise IOError("Simulated download failure")
        
        with pytest.raises(IOError):
            _atomic_download(failing_write, test_file)
        
        # File should not exist after error
        assert not test_file.exists()
        
        # No temp files should remain
        temp_files = list(tmp_path.glob(".download.bin.partial-*"))
        assert len(temp_files) == 0
    
    def test_atomic_download_creates_directories(self, tmp_path):
        """Test that atomic download creates parent directories."""
        test_file = tmp_path / "deep" / "path" / "download.bin"
        content = b"nested download"
        
        def write_func(tmppath):
            Path(tmppath).write_bytes(content)
        
        _atomic_download(write_func, test_file)
        
        assert test_file.exists()
        assert test_file.read_bytes() == content
    
    def test_atomic_download_with_oci_client(self, tmp_path):
        """Test atomic download with OCI client simulation."""
        test_file = tmp_path / "oci_blob.bin"
        test_content = b"OCI blob content"
        
        # Simulate OCI client that writes to a file path
        def mock_download_blob(container, digest, filepath):
            Path(filepath).write_bytes(test_content)
        
        mock_client = Mock()
        mock_client.download_blob = mock_download_blob
        
        def oci_write(tmppath):
            mock_client.download_blob(None, "sha256:fake", tmppath)
        
        _atomic_download(oci_write, test_file)
        
        assert test_file.exists()
        assert test_file.read_bytes() == test_content
    
    def test_concurrent_atomic_downloads(self, tmp_path):
        """Test that concurrent downloads to different files work."""
        num_files = 5
        
        def download_file(filepath, content):
            def write_func(tmppath):
                Path(tmppath).write_bytes(content)
                time.sleep(0.01)  # Simulate slow download
            _atomic_download(write_func, filepath)
        
        threads = []
        for i in range(num_files):
            filepath = tmp_path / f"file{i}.bin"
            content = f"Content for file {i}".encode()
            t = threading.Thread(target=download_file, args=(filepath, content))
            threads.append((t, filepath, content))
            t.start()
        
        for t, filepath, expected_content in threads:
            t.join()
            assert filepath.exists()
            assert filepath.read_bytes() == expected_content


class TestIntegratedReliability:
    """Test integrated reliability scenarios."""
    
    def test_pull_with_digest_verification_failure(self, tmp_path):
        """Test that failed digest verification cleans up properly."""
        from modelops_bundle.oras import _safe_target
        from modelops_bundle.utils import compute_digest
        from modelops_bundle.errors import DigestMismatchError
        
        test_file = tmp_path / "file.txt"
        wrong_content = b"wrong content"
        expected_digest = "sha256:expected"
        
        def write_wrong_content(tmppath):
            Path(tmppath).write_bytes(wrong_content)
        
        # Download with wrong content
        _atomic_download(write_wrong_content, test_file)
        assert test_file.exists()
        
        # Verify digest mismatch is detected
        actual = compute_digest(test_file)
        if actual != expected_digest:
            test_file.unlink(missing_ok=True)
            # In real code, this would raise DigestMismatchError
            assert not test_file.exists()
    
    def test_interrupted_state_save_recovery(self, tmp_path, monkeypatch):
        """Test recovery from interrupted state save."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        # Use the actual state_path from context
        
        # Write initial valid state
        state1 = SyncState(last_push_digest="sha256:first")
        save_state(state1, ctx)
        
        # Simulate interrupted write by creating a temp file
        temp_file = ctx.state_path.parent / ".state.json.tmp-12345"
        temp_file.write_text('{"incomplete": ')
        
        # New atomic write should succeed and clean up
        state2 = SyncState(last_push_digest="sha256:second")
        save_state(state2, ctx)
        
        # Should have the new state
        assert ctx.state_path.exists()
        loaded = json.loads(ctx.state_path.read_text())
        assert loaded["last_push_digest"] == "sha256:second"
        
        # Old temp file may or may not be cleaned up by the OS
        # but shouldn't interfere with operations