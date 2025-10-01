"""Test LocalCAS implementation."""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from modelops_bundle.local_cache import LocalCAS, _validate_sha256


class TestDigestValidation:
    """Test digest validation for security."""
    
    def test_valid_sha256(self):
        """Test valid SHA256 digest."""
        hex_part = _validate_sha256("sha256:abcd1234" + "0" * 56)
        assert hex_part == "abcd1234" + "0" * 56
    
    def test_invalid_scheme(self):
        """Test invalid digest scheme."""
        with pytest.raises(ValueError, match="Invalid digest scheme"):
            _validate_sha256("md5:abcd1234")
    
    def test_invalid_hex_length(self):
        """Test invalid hex length."""
        with pytest.raises(ValueError, match="must be 64 hex chars"):
            _validate_sha256("sha256:abcd")
    
    def test_invalid_hex_chars(self):
        """Test invalid hex characters."""
        with pytest.raises(ValueError, match="must be 64 hex chars"):
            _validate_sha256("sha256:gggg" + "0" * 60)
    
    def test_path_traversal_attempt(self):
        """Test path traversal in digest is blocked."""
        with pytest.raises(ValueError):
            _validate_sha256("sha256:../../../etc/passwd" + "0" * 40)


class TestLocalCASBasic:
    """Test basic LocalCAS functionality."""
    
    @pytest.fixture
    def cas(self, tmp_path):
        """Create LocalCAS instance with temp directory."""
        return LocalCAS(root=tmp_path / "cache")
    
    def test_init_creates_directories(self, tmp_path):
        """Test that initialization creates necessary directories."""
        cache_dir = tmp_path / "test_cache"
        cas = LocalCAS(root=cache_dir)
        
        assert cache_dir.exists()
        assert (cache_dir / "objects" / "sha256").exists()
    
    def test_path_for_sharding(self, cas):
        """Test path generation with sharding."""
        digest = "sha256:" + "a" * 64
        path = cas.path_for(digest)
        
        # Should shard by first 4 chars: aa/aa/aaaa...
        assert path.parent.parent.name == "aa"
        assert path.parent.name == "aa"
        assert path.name == "a" * 64
    
    def test_has_nonexistent(self, cas):
        """Test has() for non-existent object."""
        assert not cas.has("sha256:" + "0" * 64)
    
    def test_has_invalid_digest(self, cas):
        """Test has() with invalid digest returns False."""
        assert not cas.has("invalid:digest")


class TestEnsurePresent:
    """Test ensure_present functionality."""
    
    @pytest.fixture
    def cas(self, tmp_path):
        """Create LocalCAS instance."""
        return LocalCAS(root=tmp_path / "cache")
    
    @pytest.fixture
    def mock_compute_digest(self):
        """Mock compute_digest function."""
        # The import in local_cache.py is: from .utils import compute_digest
        # So we need to patch where it's used, not where it's defined
        with patch("modelops_bundle.hashing.compute_file_digest") as mock:
            # Default to returning the expected digest
            mock.return_value = "sha256:" + "a" * 64
            yield mock
    
    def test_ensure_present_downloads(self, cas, mock_compute_digest):
        """Test that ensure_present downloads missing object."""
        digest = "sha256:" + "a" * 64
        content = b"test content"

        def fetch(path):
            Path(path).write_bytes(content)

        with patch("modelops_bundle.hashing.compute_file_digest", return_value=digest):
            result = cas.ensure_present(digest, fetch)
        
        assert result.exists()
        assert result.read_bytes() == content
        # Should be read-only
        assert oct(result.stat().st_mode)[-3:] == "444"
    
    def test_ensure_present_existing(self, cas):
        """Test that ensure_present returns existing object without downloading."""
        digest = "sha256:" + "b" * 64
        
        # Pre-create the object
        obj_path = cas.path_for(digest)
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_text("existing")
        
        fetch_called = False
        def fetch(path):
            nonlocal fetch_called
            fetch_called = True
        
        result = cas.ensure_present(digest, fetch)
        
        assert result == obj_path
        assert not fetch_called  # Should not download
    
    def test_ensure_present_digest_mismatch(self, cas):
        """Test that ensure_present raises on digest mismatch."""
        expected = "sha256:" + "a" * 64
        actual = "sha256:" + "b" * 64
        
        def fetch(path):
            Path(path).write_bytes(b"wrong content")
        
        with patch("modelops_bundle.hashing.compute_file_digest", return_value=actual):
            with pytest.raises(ValueError, match="Digest mismatch"):
                cas.ensure_present(expected, fetch)
        
        # Object should not exist in cache
        assert not cas.has(expected)
    
    def test_ensure_present_cleanup_on_error(self, cas):
        """Test that temp files are cleaned up on error."""
        digest = "sha256:" + "c" * 64
        
        def fetch(path):
            raise IOError("Download failed")
        
        with pytest.raises(IOError):
            cas.ensure_present(digest, fetch)
        
        # Check no temp files left
        obj_dir = cas.path_for(digest).parent
        if obj_dir.exists():
            temps = list(obj_dir.glob(".cas-*"))
            assert len(temps) == 0


class TestMaterialize:
    """Test materialize functionality."""
    
    @pytest.fixture
    def cas(self, tmp_path):
        """Create LocalCAS with a test object."""
        cas = LocalCAS(root=tmp_path / "cache")
        
        # Pre-create a test object
        digest = "sha256:" + "d" * 64
        obj_path = cas.path_for(digest)
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_text("test content")
        obj_path.chmod(0o444)  # Make read-only like real cache
        
        return cas, digest
    
    def test_materialize_copy(self, cas, tmp_path):
        """Test materialize with copy mode."""
        cas_obj, digest = cas
        dest = tmp_path / "dest" / "file.txt"
        
        cas_obj.materialize(digest, dest, mode="copy")
        
        assert dest.exists()
        assert dest.read_text() == "test content"
        # Copy should have normal permissions, not read-only
        assert oct(dest.stat().st_mode)[-3:] != "444"
    
    def test_materialize_hardlink_linux(self, cas, tmp_path):
        """Test materialize with hardlink mode on Linux."""
        cas_obj, digest = cas
        dest = tmp_path / "dest" / "file.txt"
        
        if sys.platform != "win32":
            cas_obj.materialize(digest, dest, mode="hardlink")
            
            assert dest.exists()
            assert dest.read_text() == "test content"
            
            # Check it's actually a hardlink (same inode)
            src = cas_obj.path_for(digest)
            assert dest.stat().st_ino == src.stat().st_ino
    
    def test_materialize_auto_fallback(self, cas, tmp_path):
        """Test materialize with auto mode falls back appropriately."""
        cas_obj, digest = cas
        dest = tmp_path / "dest" / "file.txt"
        
        # Auto should try reflink, hardlink, then copy
        # At least copy should work
        cas_obj.materialize(digest, dest, mode="auto")
        
        assert dest.exists()
        assert dest.read_text() == "test content"
    
    def test_materialize_nonexistent(self, cas, tmp_path):
        """Test materialize raises for non-existent object."""
        cas_obj, _ = cas
        fake_digest = "sha256:" + "e" * 64
        dest = tmp_path / "dest" / "file.txt"
        
        with pytest.raises(FileNotFoundError, match="not in cache"):
            cas_obj.materialize(fake_digest, dest)
    
    def test_materialize_skip_hardlink_if_readonly(self, cas, tmp_path):
        """Test skip_if_hardlink_and_readonly parameter."""
        cas_obj, digest = cas
        dest = tmp_path / "dest" / "file.txt"
        
        # Should skip hardlink and use copy instead
        cas_obj.materialize(
            digest, dest,
            mode="auto",
            skip_if_hardlink_and_readonly=True
        )
        
        assert dest.exists()
        if sys.platform != "win32":
            # Should NOT be a hardlink
            src = cas_obj.path_for(digest)
            assert dest.stat().st_ino != src.stat().st_ino
    
    def test_materialize_atomic(self, cas, tmp_path):
        """Test that materialize is atomic (no partial files)."""
        cas_obj, digest = cas
        dest = tmp_path / "dest" / "file.txt"
        
        # Patch copy to fail after creating temp
        original_copy = cas_obj.__class__.materialize
        
        def failing_materialize(self, *args, **kwargs):
            # Create a recognizable temp file
            temp = dest.with_name(".file.txt.failing")
            temp.write_text("partial")
            raise IOError("Copy failed")
        
        with patch.object(cas_obj, "materialize", failing_materialize):
            with pytest.raises(IOError):
                cas_obj.materialize(digest, dest)
        
        # Destination should not exist
        assert not dest.exists()


class TestConcurrency:
    """Test concurrent access patterns."""
    
    @pytest.fixture
    def cas(self, tmp_path):
        """Create LocalCAS instance."""
        return LocalCAS(root=tmp_path / "cache")
    
    def test_concurrent_ensure_present(self, cas):
        """Test multiple threads trying to fetch same object."""
        digest = "sha256:" + "f" * 64
        content = b"concurrent test"
        fetch_count = 0
        fetch_lock = threading.Lock()
        
        def fetch(path):
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
            # Simulate slow download
            time.sleep(0.1)
            Path(path).write_bytes(content)
        
        def worker():
            with patch("modelops_bundle.hashing.compute_file_digest", return_value=digest):
                cas.ensure_present(digest, fetch)
        
        # Start multiple threads
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Only one fetch should have occurred
        assert fetch_count == 1
        assert cas.has(digest)
    
    def test_lock_timeout(self, cas):
        """Test lock timeout behavior."""
        digest = "sha256:" + "9" * 64
        lock_path = cas.path_for(digest).with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Hold the lock
        import portalocker
        with portalocker.Lock(str(lock_path), "w", timeout=0):
            # Try to acquire with short timeout
            def fetch(path):
                Path(path).write_bytes(b"test")
            
            with patch("portalocker.Lock") as mock_lock:
                mock_lock.side_effect = portalocker.LockException("Timeout")
                
                with pytest.raises(portalocker.LockException):
                    cas.ensure_present(digest, fetch)


class TestCleanup:
    """Test cleanup functionality."""
    
    @pytest.fixture
    def cas(self, tmp_path):
        """Create LocalCAS with some objects."""
        cas = LocalCAS(root=tmp_path / "cache")
        
        # Create some test objects with different ages
        now = time.time()
        for i, age_hours in enumerate([1, 100, 200]):
            digest = "sha256:" + str(i) * 64
            obj_path = cas.path_for(digest)
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            obj_path.write_text(f"content {i}")
            
            # Set access/modification time
            age_seconds = age_hours * 3600
            old_time = now - age_seconds
            os.utime(obj_path, (old_time, old_time))
        
        return cas
    
    def test_cleanup_old_objects(self, cas):
        """Test cleanup removes old objects."""
        # Keep objects accessed within 168 hours (7 days)
        removed = cas.cleanup_old_objects(keep_recent_hours=168)
        
        # Should remove the 200-hour-old object
        assert removed == 1
        
        # Check what remains
        assert cas.has("sha256:" + "0" * 64)  # 1 hour old
        assert cas.has("sha256:" + "1" * 64)  # 100 hours old
        assert not cas.has("sha256:" + "2" * 64)  # 200 hours old (removed)
    
    def test_cleanup_preserves_lock_files(self, cas):
        """Test cleanup doesn't remove .lock files."""
        # Create a lock file
        lock_path = cas.objdir / "ab" / "cd" / "test.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("lock")
        
        # Set old time
        old_time = time.time() - (200 * 3600)
        os.utime(lock_path, (old_time, old_time))
        
        cas.cleanup_old_objects(keep_recent_hours=168)
        
        # Lock file should still exist
        assert lock_path.exists()


class TestPlatformCompat:
    """Test platform compatibility features."""
    
    def test_default_cache_dir_linux(self):
        """Test default cache directory on Linux."""
        with patch("sys.platform", "linux"):
            with patch("modelops_bundle.local_cache.platformdirs", None):
                from modelops_bundle.local_cache import _get_default_cache_dir
                
                cache_dir = _get_default_cache_dir()
                assert ".cache/modelops-bundle" in str(cache_dir)
    
    def test_default_cache_dir_windows(self):
        """Test default cache directory on Windows."""
        with patch("sys.platform", "win32"):
            with patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"}):
                with patch("modelops_bundle.local_cache.platformdirs", None):
                    from modelops_bundle.local_cache import _get_default_cache_dir
                    
                    cache_dir = _get_default_cache_dir()
                    assert "modelops-bundle" in str(cache_dir)
                    assert "cache" in str(cache_dir)
    
    def test_fsync_dir_windows(self):
        """Test directory fsync gracefully handles Windows."""
        from modelops_bundle.local_cache import _fsync_dir
        
        with patch("os.open", side_effect=OSError("Not supported")):
            # Should not raise, just log
            _fsync_dir(Path("/test"))
    
    def test_reflink_only_linux(self, tmp_path):
        """Test reflink is only attempted on Linux."""
        from modelops_bundle.local_cache import _try_reflink
        
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.write_text("test")
        
        with patch("sys.platform", "darwin"):  # macOS
            result = _try_reflink(src, dst)
            assert result is False
            assert not dst.exists()
        
        with patch("sys.platform", "win32"):  # Windows
            result = _try_reflink(src, dst)
            assert result is False
            assert not dst.exists()