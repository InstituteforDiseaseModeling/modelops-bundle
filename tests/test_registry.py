"""Tests for model and target registry."""

import tempfile
from pathlib import Path
import yaml
import pytest

from modelops_bundle.registry import (
    ModelEntry,
    TargetEntry,
    BundleRegistry,
)


class TestModelEntry:
    """Test model registry entries."""

    def test_create_model_entry(self, tmp_path):
        """Test creating a model entry."""
        model_file = tmp_path / "model.py"
        model_file.write_text("class Model: pass")

        data_file = tmp_path / "data.csv"
        data_file.write_text("a,b,c")

        entry = ModelEntry(
            path=model_file,
            class_name="Model",
            outputs=["prevalence", "incidence"],
            data=[data_file],
            code=[]
        )

        assert entry.path == model_file
        assert entry.class_name == "Model"
        assert "prevalence" in entry.outputs
        assert data_file in entry.data

    def test_model_compute_digest(self, tmp_path):
        """Test computing model digest."""
        model_file = tmp_path / "model.py"
        model_file.write_text("""
def simulate(params):
    return params * 2
""")

        entry = ModelEntry(
            path=model_file,
            class_name="Model"
        )

        digest = entry.compute_digest()
        assert digest
        assert len(digest) == 64  # SHA256 hex

        # Should be deterministic
        digest2 = entry.compute_digest()
        assert digest == digest2

    def test_model_validate_dependencies(self, tmp_path):
        """Test dependency validation."""
        model_file = tmp_path / "model.py"
        model_file.write_text("class Model: pass")

        missing_data = tmp_path / "missing.csv"  # Doesn't exist
        existing_data = tmp_path / "data.csv"
        existing_data.write_text("data")

        entry = ModelEntry(
            path=model_file,
            class_name="Model",
            data=[existing_data, missing_data]
        )

        errors = entry.validate_dependencies()
        assert len(errors) == 1
        assert "missing.csv" in errors[0]
        assert "not found" in errors[0]

    def test_model_to_from_dict(self, tmp_path):
        """Test serialization/deserialization."""
        model_file = tmp_path / "model.py"
        model_file.write_text("class Model: pass")

        original = ModelEntry(
            path=model_file,
            class_name="TestModel",
            outputs=["output1"],
            data=[tmp_path / "data.csv"],
            model_digest="test_digest_123"
        )

        # Serialize
        data = original.to_dict()
        assert data["class_name"] == "TestModel"
        assert data["model_digest"] == "test_digest_123"

        # Deserialize
        restored = ModelEntry.from_dict(data)
        assert restored.class_name == original.class_name
        assert restored.path == original.path
        assert restored.model_digest == original.model_digest


class TestTargetEntry:
    """Test target registry entries."""

    def test_create_target_entry(self, tmp_path):
        """Test creating a target entry."""
        target_file = tmp_path / "target.py"
        target_file.write_text("def evaluate(): pass")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("time,value\n1,0.5")

        entry = TargetEntry(
            path=target_file,
            model_output="prevalence",
            observation=obs_file
        )

        assert entry.path == target_file
        assert entry.model_output == "prevalence"
        assert entry.observation == obs_file

    def test_target_compute_digests(self, tmp_path):
        """Test computing target and observation digests."""
        target_file = tmp_path / "target.py"
        target_file.write_text("def evaluate(model, obs): return loss")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("1,2,3")

        entry = TargetEntry(
            path=target_file,
            model_output="output",
            observation=obs_file
        )

        target_digest = entry.compute_digest()
        obs_digest = entry.compute_observation_digest()

        assert target_digest
        assert obs_digest
        assert target_digest != obs_digest

    def test_target_to_from_dict(self, tmp_path):
        """Test target serialization."""
        entry = TargetEntry(
            path=tmp_path / "target.py",
            model_output="prevalence",
            observation=tmp_path / "obs.csv",
            target_digest="digest_456"
        )

        data = entry.to_dict()
        restored = TargetEntry.from_dict(data)

        assert restored.model_output == entry.model_output
        assert restored.target_digest == entry.target_digest


class TestBundleRegistry:
    """Test the complete bundle registry."""

    def test_create_registry(self):
        """Test creating an empty registry."""
        registry = BundleRegistry()
        assert registry.version == "1.0"
        assert len(registry.models) == 0
        assert len(registry.targets) == 0

    def test_add_model(self, tmp_path):
        """Test adding a model to registry."""
        registry = BundleRegistry()

        model_file = tmp_path / "model.py"
        model_file.write_text("class Model: pass")

        entry = registry.add_model(
            model_id="test_model",
            path=model_file,
            class_name="Model",
            outputs=["output1", "output2"]
        )

        assert "test_model" in registry.models
        assert registry.models["test_model"] == entry
        assert entry.class_name == "Model"
        assert len(entry.outputs) == 2

    def test_add_target(self, tmp_path):
        """Test adding a target to registry."""
        registry = BundleRegistry()

        target_file = tmp_path / "target.py"
        target_file.write_text("def eval(): pass")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("data")

        entry = registry.add_target(
            target_id="test_target",
            path=target_file,
            model_output="output1",
            observation=obs_file
        )

        assert "test_target" in registry.targets
        assert registry.targets["test_target"] == entry

    def test_registry_validation(self, tmp_path):
        """Test registry validation."""
        registry = BundleRegistry()

        # Add model with missing file
        registry.add_model(
            model_id="bad_model",
            path=tmp_path / "missing.py",  # Doesn't exist
            class_name="Model"
        )

        errors = registry.validate()
        assert len(errors) > 0
        assert "bad_model" in errors[0]
        assert "not found" in errors[0]

    def test_save_load_registry(self, tmp_path):
        """Test saving and loading registry."""
        registry = BundleRegistry()

        # Create test files
        model_file = tmp_path / "model.py"
        model_file.write_text("class M: pass")

        target_file = tmp_path / "target.py"
        target_file.write_text("def t(): pass")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("1,2,3")

        # Add entries
        registry.add_model(
            model_id="model1",
            path=model_file,
            class_name="M",
            outputs=["out1"]
        )

        registry.add_target(
            target_id="target1",
            path=target_file,
            model_output="out1",
            observation=obs_file
        )

        # Save
        registry_file = tmp_path / "registry.yaml"
        registry.save(registry_file)

        assert registry_file.exists()

        # Load
        loaded = BundleRegistry.load(registry_file)

        assert "model1" in loaded.models
        assert "target1" in loaded.targets
        assert loaded.models["model1"].class_name == "M"
        assert loaded.targets["target1"].model_output == "out1"

    def test_compute_all_digests(self, tmp_path):
        """Test computing all digests in registry."""
        registry = BundleRegistry()

        # Create test files
        model_file = tmp_path / "model.py"
        model_file.write_text("def model(): return 42")

        target_file = tmp_path / "target.py"
        target_file.write_text("def target(): return 0")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("obs_data")

        # Add entries
        model = registry.add_model(
            model_id="m1",
            path=model_file,
            class_name="Model"
        )

        target = registry.add_target(
            target_id="t1",
            path=target_file,
            model_output="out",
            observation=obs_file
        )

        # Initially no digests
        assert model.model_digest is None
        assert target.target_digest is None

        # Compute all
        registry.compute_all_digests()

        # Should have digests now
        assert model.model_digest is not None
        assert target.target_digest is not None
        assert len(model.model_digest) == 64
        assert len(target.target_digest) == 64

    def test_registry_yaml_format(self, tmp_path):
        """Test YAML format of saved registry."""
        registry = BundleRegistry()

        model_file = tmp_path / "m.py"
        model_file.write_text("model")

        registry.add_model(
            model_id="test",
            path=model_file,
            class_name="TestModel",
            data=[tmp_path / "data.csv"]
        )

        registry_file = tmp_path / "registry.yaml"
        registry.save(registry_file)

        # Check YAML content
        with open(registry_file) as f:
            data = yaml.safe_load(f)

        assert data["version"] == "1.0"
        assert "models" in data
        assert "test" in data["models"]
        assert data["models"]["test"]["class_name"] == "TestModel"