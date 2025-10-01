"""Integration tests for the complete provenance system."""

import tempfile
from pathlib import Path
import json
import time
import pytest

from modelops_bundle.hashing import compute_file_digest, file_hash, compute_composite_digest
from modelops_contracts import BundleRegistry
from modelops_bundle.manifest import build_manifest, capture_environment


class TestProvenanceIntegration:
    """Integration tests for provenance system."""

    def test_cache_invalidation_on_data_change(self, tmp_path):
        """Test that changing data invalidates cache."""
        # Create files
        model_file = tmp_path / "model.py"
        model_file.write_text("def model(): return 42")

        data_file = tmp_path / "data.csv"
        data_file.write_text("initial,data\n1,2")

        # Compute initial composite digest
        components1 = [
            ("MODEL_CODE", str(model_file), compute_file_digest(model_file)),
            ("DATA", str(data_file), file_hash(data_file))
        ]

        env_digest = "test_env_123"
        digest1 = compute_composite_digest(components1, env_digest)

        # Change data file
        data_file.write_text("modified,data\n3,4")

        # Compute new digest
        components2 = [
            ("MODEL_CODE", str(model_file), compute_file_digest(model_file)),
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
            ("MODEL_CODE", str(model_file), compute_file_digest(model_file)),
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
            ("MODEL_CODE", str(model_file), compute_file_digest(model_file)),  # Token hash unchanged!
            ("DATA", str(data_file), file_hash(data_file))
        ]

        digest2 = compute_composite_digest(components2, env_digest)

        # Digests will now be different since we removed token hashing
        # Formatting changes WILL invalidate the cache with simple file hashing
        assert digest1 != digest2, "Formatting now changes the digest (no token hashing)"

    def test_environment_change_invalidates(self, tmp_path):
        """Test that environment changes invalidate cache."""
        # Create files
        model_file = tmp_path / "model.py"
        model_file.write_text("import numpy as np")

        components = [
            ("MODEL_CODE", str(model_file), compute_file_digest(model_file))
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
            assert file_info["sha256"].startswith("sha256:")
            assert len(file_info["sha256"]) == 71  # "sha256:" + 64 hex chars

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

        components1 = [(f"CODE_DEP", str(f), compute_file_digest(f)) for f in files]
        components2 = components1.copy()
        random.shuffle(components2)

        env = "test_env"

        digest1 = compute_composite_digest(components1, env)
        digest2 = compute_composite_digest(components2, env)

        assert digest1 == digest2, "Order shouldn't matter after sorting"