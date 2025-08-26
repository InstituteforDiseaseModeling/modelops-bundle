"""Tests for ignore pattern system."""

import os
import tempfile
from pathlib import Path
import pytest

from modelops_bundle.ignore import IgnoreSpec, DEFAULTS


class TestIgnoreSpec:
    """Test ignore pattern matching."""
    
    def test_default_patterns(self, tmp_path):
        """Test that default patterns are applied."""
        ignore = IgnoreSpec(tmp_path)
        
        # Test some default patterns
        assert ignore.is_ignored(".git/config")
        assert ignore.is_ignored("__pycache__/test.pyc")
        assert ignore.is_ignored("node_modules/package/index.js")
        assert ignore.is_ignored(".DS_Store")
        assert ignore.is_ignored("venv/lib/python3.9/site-packages/pip.py")
        
        # Test files that should NOT be ignored
        assert not ignore.is_ignored("src/main.py")
        assert not ignore.is_ignored("data/file.csv")
    
    def test_custom_patterns(self, tmp_path):
        """Test custom patterns from .modelopsignore file."""
        # Create .modelopsignore
        ignore_file = tmp_path / ".modelopsignore"
        ignore_file.write_text("""
# Comments should be ignored
*.log
temp/
!temp/keep.txt
data/*.csv
""")
        
        ignore = IgnoreSpec(tmp_path)
        
        # Test custom patterns
        assert ignore.is_ignored("debug.log")
        assert ignore.is_ignored("temp/file.txt")
        assert not ignore.is_ignored("temp/keep.txt")  # Negation pattern
        assert ignore.is_ignored("data/test.csv")
        assert not ignore.is_ignored("data/subdir/test.csv")  # Only direct children
    
    def test_should_traverse(self, tmp_path):
        """Test directory traversal optimization."""
        ignore = IgnoreSpec(tmp_path)
        
        # Should NOT traverse these
        assert not ignore.should_traverse(".modelops-bundle")
        assert not ignore.should_traverse(".git")
        assert not ignore.should_traverse("node_modules")
        assert not ignore.should_traverse("venv")
        assert not ignore.should_traverse("__pycache__")
        
        # Should traverse these
        assert ignore.should_traverse("src")
        assert ignore.should_traverse("data")
        assert ignore.should_traverse("tests")
    
    def test_windows_paths(self, tmp_path):
        """Test that Windows-style paths are handled correctly."""
        ignore = IgnoreSpec(tmp_path)
        
        # Convert Windows path to POSIX before checking
        windows_path = Path("src\\main.py")
        posix_path = windows_path.as_posix()
        
        assert not ignore.is_ignored(posix_path)
        
        # Ignored paths should work too
        windows_ignored = Path("__pycache__\\test.pyc")
        posix_ignored = windows_ignored.as_posix()
        
        assert ignore.is_ignored(posix_ignored)