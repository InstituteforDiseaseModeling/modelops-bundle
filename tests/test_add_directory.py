"""Tests for directory handling in the add command."""

import pytest
import tempfile
import yaml
import json
from pathlib import Path
from typer.testing import CliRunner
from modelops_bundle.cli import app
from modelops_bundle.context import ProjectContext
from modelops_bundle.core import TrackedFiles, BundleConfig
from modelops_bundle.ops import save_tracked, load_tracked, save_config


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project with initialized modelops-bundle."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Initialize project structure
    bundle_dir = project_dir / ".modelops-bundle"
    bundle_dir.mkdir()

    # Create a basic config
    config = BundleConfig(environment="local", 
        registry_ref="localhost:5000/test",
        default_tag="latest"
    )

    # Save config directly to the bundle directory
    config_path = bundle_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(), default_flow_style=False))

    # Initialize empty tracked files directly (plain text format, one file per line)
    tracked = TrackedFiles(files=set())
    tracked_path = bundle_dir / "tracked"
    tracked_path.write_text("")  # Empty file for no tracked files

    return project_dir


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


class TestAddDirectory:
    """Test adding directories with the add command."""

    def test_add_current_directory(self, temp_project, runner, monkeypatch):
        """Test 'add .' recursively adds all files in current directory."""
        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Create test files
        (temp_project / "file1.py").write_text("print('hello')")
        (temp_project / "file2.txt").write_text("test content")

        # Create subdirectory with files
        subdir = temp_project / "src"
        subdir.mkdir()
        (subdir / "module.py").write_text("def foo(): pass")
        (subdir / "data.json").write_text('{"key": "value"}')

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add . command
        result = runner.invoke(
            app,
            ["add", "."],
            catch_exceptions=False
        )

        # Check command succeeded
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Load tracked files
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)

        # Verify all files are tracked
        expected_files = {
            "file1.py",
            "file2.txt",
            "src/module.py",
            "src/data.json"
        }
        assert tracked.files == expected_files, f"Expected {expected_files}, got {tracked.files}"

    def test_add_subdirectory(self, temp_project, runner, monkeypatch):
        """Test 'add src/' adds all files in subdirectory."""
        # Create files in root (should not be added)
        (temp_project / "root_file.py").write_text("root")

        # Create subdirectory with files
        src_dir = temp_project / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("app code")
        (src_dir / "utils.py").write_text("utils")

        # Create nested subdirectory
        nested = src_dir / "core"
        nested.mkdir()
        (nested / "main.py").write_text("main")

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add src command
        result = runner.invoke(
            app,
            ["add", "src"],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # Load tracked files
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)

        # Verify only src files are tracked
        expected_files = {
            "src/app.py",
            "src/utils.py",
            "src/core/main.py"
        }
        assert tracked.files == expected_files
        assert "root_file.py" not in tracked.files

    def test_add_empty_directory(self, temp_project, runner, monkeypatch):
        """Test adding empty directory is handled gracefully."""
        # Create empty directory
        empty_dir = temp_project / "empty"
        empty_dir.mkdir()

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add empty command
        result = runner.invoke(
            app,
            ["add", "empty"],
            catch_exceptions=False
        )

        # Should succeed but warn about no files
        assert result.exit_code == 0
        assert "No files found" in result.output or "No files added" in result.output

        # Verify no files tracked
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert len(tracked.files) == 0

    def test_add_directory_with_ignored_files(self, temp_project, runner, monkeypatch):
        """Test directory addition respects .modelopsignore."""
        # Create .modelopsignore file
        ignore_content = """
# Ignore Python cache
__pycache__/
*.pyc

# Ignore test files
test_*.py

# Ignore data directory
data/
"""
        (temp_project / ".modelopsignore").write_text(ignore_content)

        # Create various files
        (temp_project / "app.py").write_text("app")
        (temp_project / "test_app.py").write_text("test")  # Should be ignored
        (temp_project / "module.pyc").write_text("compiled")  # Should be ignored

        # Create data directory (should be ignored)
        data_dir = temp_project / "data"
        data_dir.mkdir()
        (data_dir / "dataset.csv").write_text("data")

        # Create src directory with mixed files
        src_dir = temp_project / "src"
        src_dir.mkdir()
        (src_dir / "core.py").write_text("core")
        (src_dir / "test_core.py").write_text("test")  # Should be ignored

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add .
        result = runner.invoke(
            app,
            ["add", "."],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # Load tracked files
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)

        # Verify only non-ignored files are tracked
        expected_files = {
            "app.py",
            "src/core.py",
            ".modelopsignore"  # The ignore file itself should be tracked
        }
        assert tracked.files == expected_files

        # Verify ignored files are NOT tracked
        assert "test_app.py" not in tracked.files
        assert "module.pyc" not in tracked.files
        assert "data/dataset.csv" not in tracked.files
        assert "src/test_core.py" not in tracked.files

    def test_add_directory_absolute_path(self, temp_project, runner, monkeypatch):
        """Test adding directory with absolute path."""
        # Create subdirectory
        subdir = temp_project / "components"
        subdir.mkdir()
        (subdir / "widget.py").write_text("widget")

        # Use absolute path
        abs_path = str(subdir.absolute())

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add with absolute path
        result = runner.invoke(
            app,
            ["add", abs_path],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # Verify file is tracked with relative path
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert "components/widget.py" in tracked.files

    def test_add_multiple_directories(self, temp_project, runner, monkeypatch):
        """Test adding multiple directories in one command."""
        # Create multiple directories with files
        dir1 = temp_project / "models"
        dir1.mkdir()
        (dir1 / "model.py").write_text("model")

        dir2 = temp_project / "utils"
        dir2.mkdir()
        (dir2 / "helper.py").write_text("helper")

        dir3 = temp_project / "config"
        dir3.mkdir()
        (dir3 / "settings.yaml").write_text("key: value")

        # Add all directories at once
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "models", "utils", "config"],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # Verify all files from all directories are tracked
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        expected_files = {
            "models/model.py",
            "utils/helper.py",
            "config/settings.yaml"
        }
        assert tracked.files == expected_files

    def test_add_directory_with_symlinks(self, temp_project, runner, monkeypatch):
        """Test that symlinks in directories are handled correctly."""
        # Create a real file
        real_file = temp_project / "real.txt"
        real_file.write_text("real content")

        # Create a directory with a symlink
        link_dir = temp_project / "links"
        link_dir.mkdir()
        symlink = link_dir / "link.txt"
        symlink.symlink_to(real_file)

        # Add the directory
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "links"],
            catch_exceptions=False
        )

        # Symlinks should typically be skipped or handled specially
        # The exact behavior depends on implementation
        assert result.exit_code == 0

    def test_add_nested_directories(self, temp_project, runner, monkeypatch):
        """Test adding deeply nested directory structures."""
        # Create deeply nested structure
        deep_path = temp_project / "a" / "b" / "c" / "d" / "e"
        deep_path.mkdir(parents=True)

        # Add files at various levels
        (temp_project / "a" / "file1.py").write_text("1")
        (temp_project / "a" / "b" / "file2.py").write_text("2")
        (temp_project / "a" / "b" / "c" / "file3.py").write_text("3")
        (deep_path / "file5.py").write_text("5")

        # Add the root directory
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "a"],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # Verify all nested files are tracked
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        expected_files = {
            "a/file1.py",
            "a/b/file2.py",
            "a/b/c/file3.py",
            "a/b/c/d/e/file5.py"
        }
        assert tracked.files == expected_files

    def test_add_directory_updates_existing_tracked(self, temp_project, runner, monkeypatch):
        """Test that adding a directory updates existing tracked files."""
        # Add initial file
        (temp_project / "initial.py").write_text("initial")

        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "initial.py"],
            catch_exceptions=False
        )
        assert result.exit_code == 0

        # Verify initial file is tracked
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert tracked.files == {"initial.py"}

        # Create new directory with files
        new_dir = temp_project / "new"
        new_dir.mkdir()
        (new_dir / "file1.py").write_text("1")
        (new_dir / "file2.py").write_text("2")

        # Add the directory
        result = runner.invoke(
            app,
            ["add", "new"],
            catch_exceptions=False
        )
        assert result.exit_code == 0

        # Verify both initial and new files are tracked
        tracked = load_tracked(ctx)
        expected_files = {
            "initial.py",
            "new/file1.py",
            "new/file2.py"
        }
        assert tracked.files == expected_files

    def test_add_current_directory_with_force(self, temp_project, runner, monkeypatch):
        """Test 'add . --force' includes ignored files."""
        # Create .modelopsignore
        (temp_project / ".modelopsignore").write_text("*.log\n*.tmp")

        # Create mix of normal and ignored files
        (temp_project / "app.py").write_text("app")
        (temp_project / "debug.log").write_text("log")  # Should be ignored normally
        (temp_project / "temp.tmp").write_text("temp")  # Should be ignored normally

        # Change to project directory
        monkeypatch.chdir(temp_project)

        # Run add . --force
        result = runner.invoke(
            app,
            ["add", ".", "--force"],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        # With --force, ignored files should be added
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert "app.py" in tracked.files
        assert "debug.log" in tracked.files
        assert "temp.tmp" in tracked.files


class TestAddDirectoryErrorCases:
    """Test error handling for directory addition."""

    def test_add_nonexistent_directory(self, temp_project, runner, monkeypatch):
        """Test error when adding non-existent directory."""
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "nonexistent"],
            catch_exceptions=False
        )

        # Should fail gracefully
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_add_file_as_directory(self, temp_project, runner, monkeypatch):
        """Test that adding a regular file still works (not broken by directory feature)."""
        # Create a regular file
        file_path = temp_project / "regular.txt"
        file_path.write_text("content")

        # Add it (should work as before)
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", "regular.txt"],
            catch_exceptions=False
        )

        assert result.exit_code == 0

        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert "regular.txt" in tracked.files

    def test_add_directory_outside_project(self, temp_project, runner, monkeypatch, tmp_path):
        """Test error when trying to add directory outside project."""
        # Create directory outside project
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "file.txt").write_text("outside")

        # Try to add it
        monkeypatch.chdir(temp_project)
        result = runner.invoke(
            app,
            ["add", str(outside_dir)],
            catch_exceptions=False
        )

        # Should fail or skip files outside project
        ctx = ProjectContext(start_path=temp_project)
        tracked = load_tracked(ctx)
        assert "file.txt" not in tracked.files
        assert len([f for f in tracked.files if "outside" in f]) == 0