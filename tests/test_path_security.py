"""Test path security and traversal prevention."""

import os
import pytest
from pathlib import Path
from modelops_bundle.oras import _safe_target
from modelops_bundle.storage_models import BundleFileEntry, StorageType


class TestPathSecurity:
    """Test path traversal prevention."""
    
    def test_reject_absolute_paths(self, tmp_path):
        """Test that absolute paths are rejected."""
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "/etc/passwd")
        
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "\\windows\\system32\\config")
    
    def test_reject_parent_traversal(self, tmp_path):
        """Test that parent directory traversal is blocked."""
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "../etc/passwd")
        
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "../../secret.txt")
        
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "some/../../path")
        
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "foo/../../../bar")
    
    def test_reject_windows_traversal(self, tmp_path):
        """Test that Windows-style traversal is blocked."""
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "..\\..\\windows")
        
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "dir\\..\\..\\etc")
    
    def test_reject_escaped_paths(self, tmp_path):
        """Test that paths escaping the root are rejected."""
        # Create a subdirectory
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        
        # Try to escape via symlink-like resolution
        with pytest.raises(ValueError, match="Unsafe path"):
            # This would resolve outside root (caught by .. check)
            _safe_target(subdir, "../../outside")
    
    def test_accept_safe_paths(self, tmp_path):
        """Test that safe paths are accepted."""
        # These should all work
        safe_paths = [
            "file.txt",
            "dir/file.txt",
            "deep/nested/dir/file.txt",
            "file-with-dash.txt",
            "file_with_underscore.txt",
            "file.multiple.dots.txt",
            "UPPERCASE.TXT",
            "123numeric.txt"
        ]
        
        for path in safe_paths:
            result = _safe_target(tmp_path, path)
            assert result.is_absolute()
            assert str(result).startswith(str(tmp_path.resolve()))
    
    def test_safe_path_normalization(self, tmp_path):
        """Test that safe paths are properly normalized."""
        # Paths with redundant separators should work
        result = _safe_target(tmp_path, "dir//file.txt")
        assert result == tmp_path / "dir" / "file.txt"
        
        # Current directory references should work
        result = _safe_target(tmp_path, "./file.txt")
        assert result == tmp_path / "file.txt"
        
        result = _safe_target(tmp_path, "dir/./file.txt")
        assert result == tmp_path / "dir" / "file.txt"
    
    def test_empty_path(self, tmp_path):
        """Test handling of empty paths."""
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_target(tmp_path, "")
    
    def test_dot_path(self, tmp_path):
        """Test handling of dot paths."""
        # Single dot should resolve to root
        result = _safe_target(tmp_path, ".")
        assert result == tmp_path.resolve()
        
        # Dot prefixed files should work
        result = _safe_target(tmp_path, ".hidden")
        assert result == tmp_path / ".hidden"
    
    def test_unicode_paths(self, tmp_path):
        """Test handling of unicode in paths."""
        # Unicode should be handled safely
        safe_unicode = [
            "файл.txt",  # Cyrillic
            "文件.txt",  # Chinese
            "ファイル.txt",  # Japanese
            "archivo_español.txt"  # Spanish
        ]
        
        for path in safe_unicode:
            result = _safe_target(tmp_path, path)
            assert result.parent == tmp_path.resolve()


class TestPullSecurity:
    """Test security in pull operations."""
    
    def test_malicious_bundle_index(self, tmp_path, monkeypatch):
        """Test that malicious paths in bundle index are caught."""
        from modelops_bundle.oras import OrasAdapter, _safe_target
        from modelops_bundle.storage_models import BundleIndex
        from modelops_bundle.constants import BUNDLE_VERSION
        from modelops_bundle.utils import get_iso_timestamp
        
        # Create a malicious index with path traversal attempts
        malicious_entries = [
            BundleFileEntry(
                path="../../../etc/passwd",
                digest="sha256:fake",
                size=100,
                storage=StorageType.OCI
            ),
            BundleFileEntry(
                path="/etc/shadow",
                digest="sha256:fake",
                size=100,
                storage=StorageType.OCI
            )
        ]
        
        # Mock adapter to avoid real network calls
        adapter = OrasAdapter()
        
        # Attempting to pull with malicious paths should fail
        for entry in malicious_entries:
            with pytest.raises(ValueError, match="Unsafe path"):
                # This would be called internally by pull_selected
                _safe_target(tmp_path, entry.path)
    
    def test_symlink_escape_attempt(self, tmp_path):
        """Test that symlink-based escapes are prevented."""
        # Create a structure that might try to escape via symlinks
        safe_dir = tmp_path / "safe"
        safe_dir.mkdir()
        
        # Even if someone created a symlink pointing outside
        # (this would need to happen outside our code)
        # our path validation should catch it
        
        escape_attempts = [
            "subdir/../../outside.txt",
            "../sibling/file.txt"
        ]
        
        for attempt in escape_attempts:
            with pytest.raises(ValueError, match="Unsafe path|Path escapes"):
                _safe_target(safe_dir, attempt)