"""Tests for model status system."""

import tempfile
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest

from modelops_bundle.context import ProjectContext
from modelops_bundle.core import SyncState
from modelops_bundle.model_state import (
    DigestSnapshot,
    FileDigestState,
    ModelDependencyState,
    ModelReadiness,
    ModelState,
    ModelStatusSnapshot,
    ModelSyncState,
    compute_model_digest,
)
from modelops_bundle.model_status_computer import ModelStatusComputer
from modelops_contracts import BundleRegistry, ModelEntry


class TestDigestSnapshot:
    """Test DigestSnapshot class."""

    def test_immutability(self):
        """Test that DigestSnapshot digests are immutable."""
        digests = {"file1.txt": "sha256:abc123"}
        snapshot = DigestSnapshot(
            timestamp=datetime.now(),
            digests=digests
        )

        # Should be MappingProxyType
        assert isinstance(snapshot.digests, types.MappingProxyType)

        # Should not be able to modify
        with pytest.raises(TypeError):
            snapshot.digests["file2.txt"] = "sha256:def456"

    def test_compare_against_expected(self):
        """Test comparing local against expected digests."""
        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={
                "unchanged.txt": "sha256:same",
                "modified.txt": "sha256:new",
                "newfile.txt": "sha256:local",
            }
        )

        expected = DigestSnapshot(
            timestamp=datetime.now(),
            digests={
                "unchanged.txt": "sha256:same",
                "modified.txt": "sha256:old",
                "deleted.txt": "sha256:gone",
            }
        )

        states = local.compare_against_expected(expected)

        assert states["unchanged.txt"] == FileDigestState.CURRENT
        assert states["modified.txt"] == FileDigestState.MODIFIED
        assert states["newfile.txt"] == FileDigestState.UNKNOWN
        assert states["deleted.txt"] == FileDigestState.MISSING


class TestModelDependencyState:
    """Test ModelDependencyState class."""

    def test_is_valid(self):
        """Test dependency validity check."""
        # Valid: exists and matches
        dep1 = ModelDependencyState(
            path="file.txt",
            expected_digest="sha256:abc",
            actual_digest="sha256:abc",
            file_state=FileDigestState.CURRENT,
            size=100,
            last_modified=datetime.now(),
        )
        assert dep1.is_valid

        # Valid: exists with unknown expected
        dep2 = ModelDependencyState(
            path="file.txt",
            expected_digest=None,
            actual_digest="sha256:abc",
            file_state=FileDigestState.UNKNOWN,
            size=100,
            last_modified=datetime.now(),
        )
        assert dep2.is_valid

        # Invalid: modified
        dep3 = ModelDependencyState(
            path="file.txt",
            expected_digest="sha256:abc",
            actual_digest="sha256:def",
            file_state=FileDigestState.MODIFIED,
            size=100,
            last_modified=datetime.now(),
        )
        assert not dep3.is_valid

        # Invalid: missing
        dep4 = ModelDependencyState(
            path="file.txt",
            expected_digest="sha256:abc",
            actual_digest=None,
            file_state=FileDigestState.MISSING,
            size=None,
            last_modified=None,
        )
        assert not dep4.is_valid


class TestModelState:
    """Test ModelState class."""

    def test_compute_readiness(self):
        """Test readiness computation."""
        # Create model with all files present
        model = ModelState(
            model_id="test",
            name="TestModel",
            entrypoint="test:TestModel",
            model_file="model.py",
            model_file_state=ModelDependencyState(
                path="model.py",
                expected_digest="sha256:model",
                actual_digest="sha256:model",
                file_state=FileDigestState.CURRENT,
                size=1000,
                last_modified=datetime.now(),
            ),
            data_dependencies=[
                ModelDependencyState(
                    path="data.csv",
                    expected_digest="sha256:data",
                    actual_digest="sha256:data",
                    file_state=FileDigestState.CURRENT,
                    size=5000,
                    last_modified=datetime.now(),
                )
            ],
            code_dependencies=[],
            local_readiness=ModelReadiness.UNKNOWN,
            cloud_sync_state=ModelSyncState.UNKNOWN,
        )

        # Should be READY when all files match
        assert model.compute_readiness() == ModelReadiness.READY

        # Modify a dependency
        model.data_dependencies[0].file_state = FileDigestState.MODIFIED
        assert model.compute_readiness() == ModelReadiness.STALE

        # Delete a dependency
        model.data_dependencies[0].file_state = FileDigestState.MISSING
        assert model.compute_readiness() == ModelReadiness.BROKEN

    def test_needs_push(self):
        """Test needs_push property."""
        model = ModelState(
            model_id="test",
            name="TestModel",
            entrypoint="test:TestModel",
            model_file="model.py",
            model_file_state=Mock(),
            data_dependencies=[],
            code_dependencies=[],
            local_readiness=ModelReadiness.READY,
            cloud_sync_state=ModelSyncState.AHEAD,
        )
        assert model.needs_push

        model.cloud_sync_state = ModelSyncState.DIVERGED
        assert model.needs_push

        model.cloud_sync_state = ModelSyncState.SYNCED
        assert not model.needs_push


class TestModelSyncStateComputation:
    """Test sync state computation algorithm."""

    def test_untracked_when_no_cloud(self):
        """Test UNTRACKED when no cloud state."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt", "file2.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={
                "file1.txt": "sha256:abc",
                "file2.txt": "sha256:def",
            }
        )

        # No cloud state
        result = computer._compute_sync_state(
            model_state, local, None, SyncState()
        )
        assert result == ModelSyncState.UNTRACKED

    def test_synced_when_all_match(self):
        """Test SYNCED when local and cloud match."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt", "file2.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={
                "file1.txt": "sha256:abc",
                "file2.txt": "sha256:def",
            }
        )

        cloud = DigestSnapshot(
            timestamp=datetime.now(),
            digests={
                "file1.txt": "sha256:abc",
                "file2.txt": "sha256:def",
            }
        )

        result = computer._compute_sync_state(
            model_state, local, cloud, SyncState()
        )
        assert result == ModelSyncState.SYNCED

    def test_ahead_when_local_changed(self):
        """Test AHEAD when only local has changes."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:new"}
        )

        cloud = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:old"}
        )

        sync_state = SyncState(
            last_synced_files={"file1.txt": "sha256:old"}
        )

        result = computer._compute_sync_state(
            model_state, local, cloud, sync_state
        )
        assert result == ModelSyncState.AHEAD

    def test_behind_when_cloud_changed(self):
        """Test BEHIND when only cloud has changes."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:old"}
        )

        cloud = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:new"}
        )

        sync_state = SyncState(
            last_synced_files={"file1.txt": "sha256:old"}
        )

        result = computer._compute_sync_state(
            model_state, local, cloud, sync_state
        )
        assert result == ModelSyncState.BEHIND

    def test_diverged_when_both_changed(self):
        """Test DIVERGED when both local and cloud changed."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:local_new"}
        )

        cloud = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:cloud_new"}
        )

        sync_state = SyncState(
            last_synced_files={"file1.txt": "sha256:old"}
        )

        result = computer._compute_sync_state(
            model_state, local, cloud, sync_state
        )
        assert result == ModelSyncState.DIVERGED

    def test_diverged_with_unknown_baseline(self):
        """Test DIVERGED when no baseline and files differ."""
        # Create proper mock context
        ctx = Mock()
        ctx.storage_dir = Path("/tmp/test")
        adapter = Mock()

        # Mock the registry loading
        with patch.object(ModelStatusComputer, '_load_registry', return_value=None):
            computer = ModelStatusComputer(ctx, adapter)

        model_state = Mock()
        model_state.dependency_paths = ["file1.txt"]

        local = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:local"}
        )

        cloud = DigestSnapshot(
            timestamp=datetime.now(),
            digests={"file1.txt": "sha256:cloud"}
        )

        # No sync history
        result = computer._compute_sync_state(
            model_state, local, cloud, SyncState()
        )
        assert result == ModelSyncState.DIVERGED


class TestComputeModelDigest:
    """Test Merkle-style model digest computation."""

    def test_deterministic_digest(self):
        """Test that model digest is deterministic."""
        paths = ["file1.txt", "file2.txt", "file3.txt"]
        digests = {
            "file1.txt": "sha256:abc",
            "file2.txt": "sha256:def",
            "file3.txt": "sha256:ghi",
        }

        # Should produce same result regardless of input order
        digest1 = compute_model_digest(paths, digests)
        digest2 = compute_model_digest(reversed(paths), digests)

        assert digest1 == digest2
        assert digest1.startswith("sha256:")

    def test_handles_missing_files(self):
        """Test that missing files get 'absent' marker."""
        paths = ["exists.txt", "missing.txt"]
        digests = {"exists.txt": "sha256:abc"}

        digest = compute_model_digest(paths, digests)
        assert digest.startswith("sha256:")

        # Different from when all present
        all_present = compute_model_digest(
            ["exists.txt"],
            {"exists.txt": "sha256:abc"}
        )
        assert digest != all_present


class TestModelStatusComputer:
    """Test ModelStatusComputer integration."""

    def test_full_status_no_registry(self):
        """Test computing status with no registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize project context
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir()
            ctx = ProjectContext.init(project_dir)

            # Mock adapter
            adapter = Mock()

            # Create computer
            computer = ModelStatusComputer(ctx, adapter)

            # Compute status with no registry
            config = Mock(registry_ref="test.io/repo", default_tag="latest")
            snapshot = computer.compute_full_status(
                config, "test.io/repo", "latest"
            )

            assert snapshot.models == {}
            assert snapshot.bundle_ref == "test.io/repo"
            assert snapshot.bundle_tag == "latest"

    @patch("modelops_bundle.ops.load_tracked")
    def test_full_status_with_models(self, mock_load_tracked, tmp_path):
        """Test computing full status with registered models."""
        # Setup project
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        ctx = ProjectContext.init(project_dir)

        # Create mock files first
        (project_dir / "model.py").write_text("class TestModel: pass")
        (project_dir / "data.csv").write_text("a,b,c")

        # Create registry with a model using absolute paths
        registry = BundleRegistry()
        registry.add_model(
            model_id="test_model",
            path=project_dir / "model.py",
            class_name="TestModel",
            data=[project_dir / "data.csv"],
        )
        registry_path = ctx.storage_dir / "registry.yaml"
        registry.save(registry_path)

        # Mock tracked files
        mock_tracked = Mock()
        mock_tracked.files = ["model.py", "data.csv"]
        mock_load_tracked.return_value = mock_tracked

        # Mock adapter for cloud state
        adapter = Mock()
        adapter.resolve_tag_to_digest.return_value = "sha256:clouddigest"
        adapter.get_index.return_value = Mock(
            files={
                "model.py": Mock(digest="sha256:cloudmodel"),
                "data.csv": Mock(digest="sha256:clouddata"),
            }
        )

        # Create computer and compute status
        computer = ModelStatusComputer(ctx, adapter)
        config = Mock(registry_ref="test.io/repo", default_tag="latest")
        snapshot = computer.compute_full_status(
            config, "test.io/repo", "latest"
        )

        # Should have one model
        assert len(snapshot.models) == 1
        assert "test_model" in snapshot.models

        model = snapshot.models["test_model"]
        assert model.name == "TestModel"
        assert model.model_file == "model.py"
        assert len(model.data_dependencies) == 1


class TestModelStatusSnapshot:
    """Test ModelStatusSnapshot aggregate properties."""

    def test_all_ready(self):
        """Test all_ready property."""
        snapshot = ModelStatusSnapshot(
            timestamp=datetime.now(),
            models={
                "model1": Mock(is_ready_locally=True),
                "model2": Mock(is_ready_locally=True),
            },
            bundle_ref="test.io/repo",
            bundle_tag="latest",
            tracked_files=set(),
            cloud_manifest_digest=None,
            cloud_file_digests={},
            cloud_timestamp=None,
        )
        assert snapshot.all_ready

        # Add unready model
        snapshot.models["model3"] = Mock(is_ready_locally=False)
        assert not snapshot.all_ready

    def test_get_models_by_state(self):
        """Test filtering models by state."""
        snapshot = ModelStatusSnapshot(
            timestamp=datetime.now(),
            models={
                "ready1": Mock(local_readiness=ModelReadiness.READY),
                "ready2": Mock(local_readiness=ModelReadiness.READY),
                "stale1": Mock(local_readiness=ModelReadiness.STALE),
                "broken1": Mock(local_readiness=ModelReadiness.BROKEN),
            },
            bundle_ref="test.io/repo",
            bundle_tag="latest",
            tracked_files=set(),
            cloud_manifest_digest=None,
            cloud_file_digests={},
            cloud_timestamp=None,
        )

        ready_models = snapshot.get_models_by_readiness(ModelReadiness.READY)
        assert len(ready_models) == 2

        stale_models = snapshot.get_models_by_readiness(ModelReadiness.STALE)
        assert len(stale_models) == 1

        broken_models = snapshot.get_models_by_readiness(ModelReadiness.BROKEN)
        assert len(broken_models) == 1