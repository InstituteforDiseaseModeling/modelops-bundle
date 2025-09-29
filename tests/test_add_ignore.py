"""Tests for add command with ignore patterns."""

import os
from pathlib import Path
import pytest
from typer.testing import CliRunner

from modelops_bundle.cli import app
from modelops_bundle.context import ProjectContext
from modelops_bundle.core import TrackedFiles, BundleConfig
from modelops_bundle.ops import save_config, save_tracked, load_tracked


runner = CliRunner()


class TestAddWithIgnore:
    """Test add command respects ignore patterns."""
    
    def test_add_refuses_ignored_file(self, tmp_path, monkeypatch):
        """Test that add refuses to add ignored files without --force."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create an ignored file (.pyc is in default ignores)
        ignored_file = tmp_path / "test.pyc"
        ignored_file.write_bytes(b"bytecode")
        
        # Try to add it without --force
        result = runner.invoke(app, ["add", "test.pyc"])
        
        # Should refuse and show warning
        assert result.exit_code == 0
        assert "ignored" in result.stdout  # More flexible
        assert "test.pyc" in result.stdout
        assert "force" in result.stdout.lower()  # Case insensitive
        
        # Verify it was NOT added
        tracked = load_tracked(ctx)
        assert "test.pyc" not in tracked.files
    
    def test_add_with_force_adds_ignored_file(self, tmp_path, monkeypatch):
        """Test that --force allows adding ignored files."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create an ignored file
        ignored_file = tmp_path / "test.pyc"
        ignored_file.write_bytes(b"bytecode")
        
        # Add it with --force
        result = runner.invoke(app, ["add", "test.pyc", "--force"])
        
        # Should succeed
        assert result.exit_code == 0
        assert "Tracking" in result.stdout and "1" in result.stdout  # More flexible
        assert "test.pyc" in result.stdout
        
        # Verify it WAS added
        tracked = load_tracked(ctx)
        assert "test.pyc" in tracked.files
    
    def test_add_custom_ignore_patterns(self, tmp_path, monkeypatch):
        """Test that custom .modelopsignore patterns are respected."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create custom ignore file
        (tmp_path / ".modelopsignore").write_text("""
# Custom patterns
*.log
temp/
""")
        
        # Create files
        (tmp_path / "debug.log").write_text("log data")
        (tmp_path / "main.py").write_text("code")
        
        # Try to add both
        result = runner.invoke(app, ["add", "debug.log", "main.py"])
        
        # Should skip debug.log but add main.py
        assert result.exit_code == 0
        assert "ignored" in result.stdout  # More flexible
        assert "debug.log" in result.stdout
        assert "Tracking" in result.stdout and "1" in result.stdout
        assert "main.py" in result.stdout
        
        # Verify only main.py was added
        tracked = load_tracked(ctx)
        assert "main.py" in tracked.files
        assert "debug.log" not in tracked.files
    
    def test_add_multiple_with_some_ignored(self, tmp_path, monkeypatch):
        """Test adding multiple files where some are ignored."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create files
        (tmp_path / "main.py").write_text("code")
        (tmp_path / "test.pyc").write_bytes(b"bytecode")
        (tmp_path / "data.txt").write_text("data")
        (tmp_path / ".DS_Store").write_bytes(b"mac")
        
        # Add all
        result = runner.invoke(app, ["add", "main.py", "test.pyc", "data.txt", ".DS_Store"])
        
        # Should add non-ignored, skip ignored
        assert result.exit_code == 0
        assert "Tracking" in result.stdout and "2" in result.stdout
        assert "main.py" in result.stdout
        assert "data.txt" in result.stdout
        assert "force" in result.stdout.lower()
        
        # Verify correct files were added
        tracked = load_tracked(ctx)
        assert "main.py" in tracked.files
        assert "data.txt" in tracked.files
        assert "test.pyc" not in tracked.files
        assert ".DS_Store" not in tracked.files
    
    def test_add_directory_ignores_patterns(self, tmp_path, monkeypatch):
        """Test that adding from a directory respects ignore patterns."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create directory structure
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("code")
        (src / "test.pyc").write_bytes(b"bytecode")
        (src / "__pycache__").mkdir()
        (src / "__pycache__" / "cached.pyc").write_bytes(b"cache")
        
        # Add files from src
        result = runner.invoke(app, ["add", "src/main.py", "src/test.pyc"])
        
        # Should only add main.py
        assert result.exit_code == 0
        assert "Tracking 1 files" in result.stdout
        assert "+ src/main.py" in result.stdout
        assert "ignored by .modelopsignore" in result.stdout
        assert "src/test.pyc" in result.stdout
        
        # Verify
        tracked = load_tracked(ctx)
        assert "src/main.py" in tracked.files
        assert "src/test.pyc" not in tracked.files
    
    def test_tracked_stays_tracked_even_if_later_ignored(self, tmp_path, monkeypatch):
        """Test that once a file is tracked, it stays tracked even if later ignored."""
        monkeypatch.chdir(tmp_path)
        ctx = ProjectContext.init()
        
        # Initialize bundle
        config = BundleConfig(environment="local", registry_ref="test/registry")
        save_config(config, ctx)
        save_tracked(TrackedFiles(), ctx)
        
        # Create and track a file
        (tmp_path / "important.log").write_text("data")
        result = runner.invoke(app, ["add", "important.log"])
        assert result.exit_code == 0
        
        # Verify it was added
        tracked = load_tracked(ctx)
        assert "important.log" in tracked.files
        
        # Now add an ignore pattern for it
        (tmp_path / ".modelopsignore").write_text("*.log")
        
        # The file should still be tracked (tracked wins over ignored)
        # This is tested by the untracked scanner - it won't show tracked files as untracked
        from modelops_bundle.working_state import scan_untracked
        
        untracked = scan_untracked(ctx, tracked, include_ignored=True)
        paths = [f.path for f in untracked]
        
        # important.log should NOT appear in untracked (because it's tracked)
        assert "important.log" not in paths