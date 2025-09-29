"""Shared test fixtures and utilities."""

import os
import tempfile
from pathlib import Path
import pytest

# Enable insecure mode for all tests (localhost registry uses HTTP)
os.environ["MODELOPS_BUNDLE_INSECURE"] = "true"

from modelops_bundle.context import ProjectContext
from modelops_bundle.core import BundleConfig, TrackedFiles
from modelops_bundle.ops import save_config, save_tracked
from modelops_bundle.env_manager import pin_env


@pytest.fixture
def initialized_ctx(tmp_path, monkeypatch):
    """Create an initialized project context with config."""
    monkeypatch.chdir(tmp_path)
    ctx = ProjectContext.init()

    # Pin local environment for tests
    pin_env(ctx.storage_dir, "local")

    # Use fixed registry for tests (localhost:5555 is our test registry)
    config = BundleConfig(registry_ref="localhost:5555/test")
    save_config(config, ctx)

    return ctx, config


@pytest.fixture
def test_files(tmp_path):
    """Create common test files in tmp_path."""
    def make_files():
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")
        
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print('hello')")
        
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "data.csv").write_text("a,b,c\n1,2,3")
        
        return {
            "file1.txt": tmp_path / "file1.txt",
            "file2.txt": tmp_path / "file2.txt",
            "src/main.py": src_dir / "main.py",
            "data/data.csv": data_dir / "data.csv",
        }
    return make_files


@pytest.fixture  
def make_tracked(initialized_ctx):
    """Factory fixture to create and save tracked files."""
    ctx, config = initialized_ctx
    
    def _make_tracked(*paths):
        tracked = TrackedFiles()
        for path in paths:
            tracked.add(Path(path))
        save_tracked(tracked, ctx)
        return tracked
    
    return _make_tracked


@pytest.fixture
def write_file(tmp_path):
    """Factory fixture to write files relative to tmp_path."""
    def _write(path: str, content: str = "test content"):
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return file_path
    return _write