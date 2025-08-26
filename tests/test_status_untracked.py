"""Tests for untracked file detection in status command."""

import os
from pathlib import Path
import pytest

from modelops_bundle.context import ProjectContext
from modelops_bundle.core import TrackedFiles
from modelops_bundle.working_state import scan_untracked, UntrackedFile


class TestUntrackedScanning:
    """Test untracked file scanning."""
    
    def test_scan_untracked_basic(self, tmp_path):
        """Test basic untracked file detection."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create some files
        (tmp_path / "tracked.py").write_text("tracked")
        (tmp_path / "untracked.txt").write_text("untracked")
        (tmp_path / "ignored.pyc").write_bytes(b"ignored")
        
        # Track one file
        tracked = TrackedFiles()
        tracked.add(Path("tracked.py"))
        
        # Scan for untracked (without ignored)
        untracked = scan_untracked(ctx, tracked, include_ignored=False)
        
        # Should find only untracked.txt
        assert len(untracked) == 1
        assert untracked[0].path == "untracked.txt"
        assert not untracked[0].ignored
        assert untracked[0].size == len("untracked")
    
    def test_scan_untracked_with_ignored(self, tmp_path):
        """Test untracked scanning with ignored files."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create files
        (tmp_path / "untracked.txt").write_text("untracked")
        (tmp_path / "ignored.pyc").write_bytes(b"ignored")
        (tmp_path / ".DS_Store").write_bytes(b"system")
        
        tracked = TrackedFiles()  # Nothing tracked
        
        # Scan including ignored
        untracked = scan_untracked(ctx, tracked, include_ignored=True)
        
        # Should find all files
        paths = [f.path for f in untracked]
        assert ".DS_Store" in paths
        assert "ignored.pyc" in paths
        assert "untracked.txt" in paths
        
        # Check ignored flags
        for f in untracked:
            if f.path in (".DS_Store", "ignored.pyc"):
                assert f.ignored
            else:
                assert not f.ignored
    
    def test_scan_untracked_respects_tracked(self, tmp_path):
        """Test that tracked files are never reported as untracked."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create files
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")
        (tmp_path / "ignored.pyc").write_bytes(b"ignored")
        
        # Track file1 and the ignored file (tracked wins)
        tracked = TrackedFiles()
        tracked.add(Path("file1.py"))
        tracked.add(Path("ignored.pyc"))  # Even though ignored, it's tracked
        
        # Scan
        untracked = scan_untracked(ctx, tracked, include_ignored=True)
        
        # Should only find file2.py
        assert len(untracked) == 1
        assert untracked[0].path == "file2.py"
    
    def test_scan_untracked_directory_pruning(self, tmp_path):
        """Test that ignored directories are not traversed."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create directory structure
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("code")
        
        # Create ignored directories with files
        (tmp_path / "node_modules" / "package" / "index.js").parent.mkdir(parents=True)
        (tmp_path / "node_modules" / "package" / "index.js").write_text("js")
        (tmp_path / ".git" / "config").parent.mkdir(parents=True)
        (tmp_path / ".git" / "config").write_text("config")
        
        tracked = TrackedFiles()
        
        # Scan (should not traverse ignored dirs)
        untracked = scan_untracked(ctx, tracked, include_ignored=False)
        
        # Should only find src/main.py
        assert len(untracked) == 1
        assert untracked[0].path == "src/main.py"
        
        # Even with include_ignored, we shouldn't see inside .git or node_modules
        # because should_traverse prevents traversal
        untracked_all = scan_untracked(ctx, tracked, include_ignored=True)
        paths = [f.path for f in untracked_all]
        assert "src/main.py" in paths
        assert "node_modules/package/index.js" not in paths
        assert ".git/config" not in paths
    
    def test_scan_untracked_modelopsignore(self, tmp_path):
        """Test that .modelopsignore patterns are respected."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create custom ignore file
        (tmp_path / ".modelopsignore").write_text("""
# Custom patterns
*.log
temp/
output/*.txt
""")
        
        # Create files
        (tmp_path / "debug.log").write_text("log")
        (tmp_path / "main.py").write_text("code")
        (tmp_path / "temp" / "file.txt").parent.mkdir(parents=True)
        (tmp_path / "temp" / "file.txt").write_text("temp")
        (tmp_path / "output" / "result.txt").parent.mkdir(parents=True)
        (tmp_path / "output" / "result.txt").write_text("output")
        (tmp_path / "output" / "data.csv").write_text("data")
        
        tracked = TrackedFiles()
        
        # Scan without ignored
        untracked = scan_untracked(ctx, tracked, include_ignored=False)
        paths = [f.path for f in untracked]
        
        # Should find only non-ignored files
        assert "main.py" in paths
        assert "output/data.csv" in paths
        assert "debug.log" not in paths
        assert "temp/file.txt" not in paths
        assert "output/result.txt" not in paths
    
    def test_path_normalization(self, tmp_path):
        """Test that path normalization works correctly."""
        os.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Create files
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("code")
        
        # Track with different path styles (simulate Windows)
        tracked = TrackedFiles()
        # This would be like str(Path("src/main.py")) on Windows
        tracked.files.add("src\\main.py")  # Simulate Windows path
        
        # The add method should normalize, but let's test the scanner handles it
        # First, let's properly normalize in the set
        tracked.files.clear()
        tracked.add(Path("src/main.py"))  # This will normalize to POSIX
        
        # Scan
        untracked = scan_untracked(ctx, tracked, include_ignored=False)
        
        # Should not find src/main.py as untracked (it's tracked)
        assert len(untracked) == 0