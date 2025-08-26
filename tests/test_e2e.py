"""End-to-end tests for modelops-bundle."""

import os
import tempfile
from pathlib import Path
import pytest

from modelops_bundle.core import (
    BundleConfig,
    ChangeType,
    RemoteState,
    SyncState,
    TrackedFiles,
)
from modelops_bundle.working_state import TrackedWorkingState
from modelops_bundle.ops import (
    load_config,
    load_state,
    load_tracked,
    push as ops_push,
    pull as ops_pull,
    save_config,
    save_tracked,
)
from modelops_bundle.oras import OrasAdapter

from tests.fixtures.sample_project import create_sample_project, get_expected_files


# Skip if no registry available
REGISTRY_AVAILABLE = os.environ.get("REGISTRY_URL", "localhost:5555")


@pytest.fixture
def sample_project(tmp_path):
    """Create a sample project in a temp directory."""
    return create_sample_project(tmp_path, "test_project")


@pytest.fixture
def registry_ref():
    """Get a unique registry reference for testing."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    return f"{REGISTRY_AVAILABLE}/test_{unique_id}"


class TestBundleWorkflow:
    """Test complete bundle workflow."""
    
    def test_init_and_add(self, sample_project):
        """Test initializing a bundle and adding files."""
        os.chdir(sample_project)
        
        # Initialize bundle
        config = BundleConfig(
            registry_ref=f"{REGISTRY_AVAILABLE}/test_init",
            default_tag="latest"
        )
        save_config(config)
        
        # Verify config was saved
        loaded_config = load_config()
        assert loaded_config.registry_ref == config.registry_ref
        
        # Add files
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        
        # Verify tracked files
        loaded_tracked = load_tracked()
        assert "src/model.py" in loaded_tracked.files
        assert "data/data.csv" in loaded_tracked.files
        assert len(loaded_tracked.files) == 2
    
    def test_working_tree_scan(self, sample_project):
        """Test scanning working tree."""
        os.chdir(sample_project)
        
        # Initialize project context
        from modelops_bundle.context import ProjectContext
        ctx = ProjectContext.init()
        
        # Track some files
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        
        # Create working state
        working_state = TrackedWorkingState.from_tracked(tracked, ctx)
        
        assert len(working_state.files) == 2
        assert "src/model.py" in working_state.files
        assert "data/data.csv" in working_state.files
        
        # Check file info
        model_info = working_state.files["src/model.py"]
        assert model_info.size > 0
        assert model_info.digest.startswith("sha256:")
    
    @pytest.mark.integration
    def test_push_and_pull(self, sample_project, registry_ref):
        """Test pushing and pulling from registry."""
        os.chdir(sample_project)
        
        # Initialize
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        
        # Push
        manifest_digest = ops_push(config, tracked)
        assert manifest_digest
        assert manifest_digest.startswith("sha256:") or ":" in manifest_digest
        
        # Verify remote state
        adapter = OrasAdapter()
        remote = adapter.get_remote_state(registry_ref)
        assert len(remote.files) == 2
        assert "src/model.py" in remote.files
        assert "data/data.csv" in remote.files
        
        # Pull to new location
        pull_dir = sample_project.parent / "pull_test"
        pull_dir.mkdir()
        os.chdir(pull_dir)
        
        # Initialize in new location
        save_config(config)
        save_tracked(TrackedFiles())  # Start with empty tracking
        
        # Pull
        plan = ops_pull(config, TrackedFiles(), overwrite=True)
        
        # Verify pulled files
        assert (pull_dir / "src" / "model.py").exists()
        assert (pull_dir / "data" / "data.csv").exists()
        
        # Verify content matches
        original_model = (sample_project / "src" / "model.py").read_text()
        pulled_model = (pull_dir / "src" / "model.py").read_text()
        assert original_model == pulled_model
    
    @pytest.mark.integration
    def test_diff_and_sync(self, sample_project, registry_ref):
        """Test diff detection and sync."""
        os.chdir(sample_project)
        
        # Initialize and track files
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"), Path("data/data.csv"))
        save_tracked(tracked)
        
        # Initial push
        ops_push(config, tracked)
        
        # Get initial state
        adapter = OrasAdapter()
        remote = adapter.get_remote_state(registry_ref)
        working_state = TrackedWorkingState.from_tracked(tracked)
        state = load_state()
        
        # Compute diff - should be unchanged
        diff = working_state.compute_diff(remote, state)
        assert all(c.change_type == ChangeType.UNCHANGED for c in diff.changes)
        
        # Modify a file
        model_path = sample_project / "src" / "model.py"
        original_content = model_path.read_text()
        model_path.write_text(original_content + "\n# Modified")
        
        # Scan again
        working_state = TrackedWorkingState.from_tracked(tracked)
        
        # Compute diff - should show modification
        diff = working_state.compute_diff(remote, state)
        changes_by_type = {c.path: c.change_type for c in diff.changes}
        assert changes_by_type["src/model.py"] == ChangeType.MODIFIED_LOCAL
        assert changes_by_type["data/data.csv"] == ChangeType.UNCHANGED
        
        # Push changes
        ops_push(config, tracked)
        
        # Verify remote updated
        remote_new = adapter.get_remote_state(registry_ref)
        assert remote_new.files["src/model.py"].digest != remote.files["src/model.py"].digest
    
    @pytest.mark.integration  
    def test_conflict_detection(self, sample_project, registry_ref):
        """Test conflict detection in 3-way merge."""
        os.chdir(sample_project)
        
        # Initialize
        config = BundleConfig(registry_ref=registry_ref)
        save_config(config)
        
        tracked = TrackedFiles()
        tracked.add(Path("src/model.py"))
        save_tracked(tracked)
        
        # Initial push
        ops_push(config, tracked)
        state = load_state()
        original_digest = state.last_synced_files["src/model.py"]
        
        # Simulate remote change (push from different location)
        model_path = sample_project / "src" / "model.py"
        original = model_path.read_text()
        model_path.write_text(original + "\n# Remote change")
        ops_push(config, tracked)
        
        # Reset to original and make different local change
        model_path.write_text(original + "\n# Local change")
        
        # Get states
        working_state = TrackedWorkingState.from_tracked(tracked)
        adapter = OrasAdapter()
        remote = adapter.get_remote_state(registry_ref)
        
        # Reset state to original push
        state.last_synced_files = {"src/model.py": original_digest}
        
        # Compute diff - should show conflict
        diff = working_state.compute_diff(remote, state)
        conflicts = [c for c in diff.changes if c.change_type == ChangeType.CONFLICT]
        assert len(conflicts) == 1
        assert conflicts[0].path == "src/model.py"


@pytest.mark.integration
def test_full_workflow_with_cli_commands(sample_project, registry_ref, monkeypatch):
    """Test using actual CLI operations."""
    import subprocess
    import sys
    
    os.chdir(sample_project)
    
    # Helper to run CLI commands
    def run_cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "modelops_bundle.cli"] + list(args),
            capture_output=True,
            text=True
        )
        return result
    
    # Initialize
    result = run_cli("init", registry_ref)
    assert result.returncode == 0
    
    # Add files
    result = run_cli("add", "src/model.py", "data/data.csv")
    assert result.returncode == 0
    
    # Check status
    result = run_cli("status")
    assert result.returncode == 0
    assert "src/model.py" in result.stdout
    
    # Push (no confirmation needed anymore)
    result = run_cli("push")
    assert result.returncode == 0
    assert "Pushed successfully" in result.stdout
    
    # Status should now show remote
    result = run_cli("status")
    assert result.returncode == 0
    # Should show files as unchanged if remote is accessible


