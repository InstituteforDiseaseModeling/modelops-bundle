"""Integration tests for the complete provenance system."""

import tempfile
from pathlib import Path
import json
import time
import pytest

from modelops_bundle.hashing import token_hash, file_hash, compute_composite_digest
from modelops_bundle.registry import BundleRegistry
from modelops_bundle.manifest import build_manifest, capture_environment


class TestProvenanceIntegration:
    """Integration tests for provenance system."""

    def test_complete_registration_flow(self, tmp_path):
        """Test the complete model registration and hashing flow."""
        # Create a model file
        model_file = tmp_path / "src" / "models" / "seir.py"
        model_file.parent.mkdir(parents=True)
        model_file.write_text("""
class StochasticSEIR:
    def simulate(self, params):
        # Simulate SEIR dynamics
        return {'infected': params['beta'] * 100}

    def extract_prevalence(self, raw, seed):
        return raw['infected'] / 1000
""")

        # Create data files
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        demographics = data_dir / "demographics.csv"
        demographics.write_text("age,population\n0-10,1000000\n10-20,950000")

        contact_matrix = data_dir / "contact_matrix.csv"
        contact_matrix.write_text("0-10,10-20\n12.5,8.3\n8.3,15.2")

        # Create code dependency
        utils_file = tmp_path / "src" / "utils" / "calculations.py"
        utils_file.parent.mkdir(parents=True)
        utils_file.write_text("""
def calculate_r0(beta, gamma):
    return beta / gamma
""")

        # Create registry
        registry = BundleRegistry()

        # Register model with dependencies
        model_entry = registry.add_model(
            model_id="seir",
            path=model_file,
            class_name="StochasticSEIR",
            outputs=["prevalence", "incidence"],
            data=[demographics, contact_matrix],
            code=[utils_file]
        )

        # Compute digests
        model_entry.model_digest = model_entry.compute_digest()

        # Verify all files are tracked
        assert len(model_entry.data) == 2
        assert len(model_entry.code) == 1

        # Verify digests are deterministic
        digest1 = model_entry.compute_digest()
        digest2 = model_entry.compute_digest()
        assert digest1 == digest2

        # Save and reload registry
        registry_file = tmp_path / ".modelops" / "registry.yaml"
        registry_file.parent.mkdir(exist_ok=True)
        registry.save(registry_file)

        loaded = BundleRegistry.load(registry_file)
        assert "seir" in loaded.models
        assert loaded.models["seir"].class_name == "StochasticSEIR"

    def test_cache_invalidation_on_data_change(self, tmp_path):
        """Test that changing data invalidates cache."""
        # Create files
        model_file = tmp_path / "model.py"
        model_file.write_text("def model(): return 42")

        data_file = tmp_path / "data.csv"
        data_file.write_text("initial,data\n1,2")

        # Compute initial composite digest
        components1 = [
            ("MODEL_CODE", str(model_file), token_hash(model_file)),
            ("DATA", str(data_file), file_hash(data_file))
        ]

        env_digest = "test_env_123"
        digest1 = compute_composite_digest(components1, env_digest)

        # Change data file
        data_file.write_text("modified,data\n3,4")

        # Compute new digest
        components2 = [
            ("MODEL_CODE", str(model_file), token_hash(model_file)),
            ("DATA", str(data_file), file_hash(data_file))  # Hash changed!
        ]

        digest2 = compute_composite_digest(components2, env_digest)

        # Digests must be different
        assert digest1 != digest2, "Data change should invalidate cache"

    def test_cache_preserved_on_formatting(self, tmp_path):
        """Test that code formatting doesn't invalidate cache."""
        # Original code
        model_file = tmp_path / "model.py"
        model_file.write_text("""
def calculate(x,y):
    result=x+y
    return result
""")

        data_file = tmp_path / "data.csv"
        data_file.write_text("a,b\n1,2")

        # Initial digest
        components1 = [
            ("MODEL_CODE", str(model_file), token_hash(model_file)),
            ("DATA", str(data_file), file_hash(data_file))
        ]

        env_digest = "test_env"
        digest1 = compute_composite_digest(components1, env_digest)

        # Reformat code (black-style)
        model_file.write_text("""
def calculate(x, y):
    result = x + y
    return result
""")

        # Compute new digest
        components2 = [
            ("MODEL_CODE", str(model_file), token_hash(model_file)),  # Token hash unchanged!
            ("DATA", str(data_file), file_hash(data_file))
        ]

        digest2 = compute_composite_digest(components2, env_digest)

        # Digests should be the same
        assert digest1 == digest2, "Formatting shouldn't invalidate cache"

    def test_environment_change_invalidates(self, tmp_path):
        """Test that environment changes invalidate cache."""
        # Create files
        model_file = tmp_path / "model.py"
        model_file.write_text("import numpy as np")

        components = [
            ("MODEL_CODE", str(model_file), token_hash(model_file))
        ]

        # Different environments
        env1 = "python3.11_numpy1.24"
        env2 = "python3.11_numpy1.25"  # NumPy version changed

        digest1 = compute_composite_digest(components, env1)
        digest2 = compute_composite_digest(components, env2)

        assert digest1 != digest2, "Environment change should invalidate cache"

    def test_manifest_with_provenance(self, tmp_path):
        """Test manifest generation with full provenance tracking."""
        # Create project structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        model_file = src_dir / "model.py"
        model_file.write_text("""
class SimpleModel:
    def simulate(self):
        return [1, 2, 3]
""")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        data_file = data_dir / "input.csv"
        data_file.write_text("x,y\n1,2\n3,4")

        # Build manifest
        manifest = build_manifest(
            models=[{
                "id": "simple",
                "class": "SimpleModel",
                "file": str(model_file),
                "files": [str(model_file), str(data_file)]
            }]
        )

        # Check manifest structure
        assert "bundle_digest" in manifest
        assert manifest["bundle_digest"].startswith("bundle:")
        assert "environment" in manifest
        assert "files" in manifest
        assert str(model_file) in manifest["files"]
        assert str(data_file) in manifest["files"]

        # Verify files have hashes
        for file_path, file_info in manifest["files"].items():
            assert "sha256" in file_info
            assert "size" in file_info
            assert len(file_info["sha256"]) == 64

    def test_dependency_validation(self, tmp_path):
        """Test that missing dependencies are caught."""
        registry = BundleRegistry()

        # Add model with non-existent dependencies
        model_entry = registry.add_model(
            model_id="test",
            path=tmp_path / "model.py",  # Doesn't exist
            class_name="Model",
            data=[tmp_path / "missing_data.csv"],  # Doesn't exist
            code=[tmp_path / "missing_code.py"]  # Doesn't exist
        )

        # Validation should catch all missing files
        errors = model_entry.validate_dependencies()
        assert len(errors) == 3  # model, data, code all missing
        assert all("not found" in err for err in errors)

        # Registry validation should also catch it
        registry_errors = registry.validate()
        assert len(registry_errors) > 0
        assert "test" in registry_errors[0]

    @pytest.mark.skipif(
        not Path("/Users/vsb/projects/work/modelops-contracts").exists(),
        reason="Requires modelops-contracts source"
    )
    def test_cross_project_integration(self, tmp_path):
        """Test integration with modelops-contracts."""
        try:
            from modelops_contracts import EnvironmentDigest
        except ImportError:
            pytest.skip("modelops-contracts not available")

        # Create environment
        env = EnvironmentDigest(
            python_version="3.11.5",
            platform="darwin-arm64",
            dependencies={"numpy": "1.24.0", "pandas": "2.0.0"}
        )

        # Create model file
        model_file = tmp_path / "model.py"
        model_file.write_text("def f(): pass")

        # Build composite digest using environment
        components = [
            ("MODEL_CODE", str(model_file), token_hash(model_file))
        ]

        bundle_digest = compute_composite_digest(
            components,
            env.compute_digest()
        )

        assert bundle_digest
        assert len(bundle_digest) == 64

        # Changing environment should change bundle digest
        env2 = env.with_dependencies({"numpy": "1.25.0", "pandas": "2.0.0"})
        bundle_digest2 = compute_composite_digest(
            components,
            env2.compute_digest()
        )

        assert bundle_digest != bundle_digest2

    def test_deterministic_ordering(self, tmp_path):
        """Test that component ordering doesn't affect digest."""
        # Create multiple files
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"# File {i}")
            files.append(f)

        # Create components in different orders
        import random

        components1 = [(f"CODE_DEP", str(f), token_hash(f)) for f in files]
        components2 = components1.copy()
        random.shuffle(components2)

        env = "test_env"

        digest1 = compute_composite_digest(components1, env)
        digest2 = compute_composite_digest(components2, env)

        assert digest1 == digest2, "Order shouldn't matter after sorting"

    def test_registry_persistence(self, tmp_path):
        """Test that registry persists correctly with all metadata."""
        registry = BundleRegistry()

        # Create real files
        model_file = tmp_path / "model.py"
        model_file.write_text("class M: pass")

        data_file = tmp_path / "data.csv"
        data_file.write_text("1,2,3")

        target_file = tmp_path / "target.py"
        target_file.write_text("def eval(): pass")

        obs_file = tmp_path / "obs.csv"
        obs_file.write_text("obs")

        # Add model with computed digest
        model = registry.add_model(
            model_id="m1",
            path=model_file,
            class_name="M",
            data=[data_file],
            outputs=["out1", "out2"]
        )
        model.model_digest = model.compute_digest()

        # Add target
        target = registry.add_target(
            target_id="t1",
            path=target_file,
            model_output="out1",
            observation=obs_file
        )
        target.target_digest = target.compute_digest()

        # Save
        reg_file = tmp_path / "registry.yaml"
        registry.save(reg_file)

        # Load and verify
        loaded = BundleRegistry.load(reg_file)

        assert loaded.models["m1"].model_digest == model.model_digest
        assert loaded.models["m1"].outputs == ["out1", "out2"]
        assert loaded.targets["t1"].target_digest == target.target_digest
        assert loaded.targets["t1"].model_output == "out1"