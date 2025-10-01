"""Tests for hashing module."""

import tempfile
from pathlib import Path
import pytest

from modelops_bundle.hashing import (
    compute_file_digest,
    file_hash,  # alias for compute_file_digest
    compute_composite_digest,
)


class TestFileHashing:
    """Test file-based hashing."""

    def test_file_hash_detects_changes(self, tmp_path):
        """File hash should detect any byte changes."""
        # Create a file
        file1 = tmp_path / "test.py"
        file1.write_text("def foo():\n    return 42")

        # Hash it
        hash1 = compute_file_digest(file1)

        # Change a single character
        file1.write_text("def foo():\n    return 43")

        # Hash should be different
        hash2 = compute_file_digest(file1)

        assert hash1 != hash2, "File hash should detect changes"

    def test_file_hash_alias(self, tmp_path):
        """file_hash should work as an alias."""
        file1 = tmp_path / "test.txt"
        file1.write_text("test content")

        # Both should give same result
        assert file_hash(file1) == compute_file_digest(file1)

    def test_file_hash_binary_files(self, tmp_path):
        """Should handle binary files correctly."""
        # Create binary file
        binary_file = tmp_path / "data.bin"
        binary_file.write_bytes(b'\x00\x01\x02\x03\x04')

        # Should hash without error
        hash_val = compute_file_digest(binary_file)
        assert hash_val.startswith("sha256:")
        assert len(hash_val) == 71  # "sha256:" (7) + 64 hex chars


class TestCompositeDigest:
    """Test composite digest computation."""

    def test_composite_digest_deterministic(self, tmp_path):
        """Composite digest should be deterministic."""
        # Create test files
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"content{i}")
            files.append(f)

        # Create components in different orders
        import random

        components1 = [(f"CODE_DEP", str(f), compute_file_digest(f)) for f in files]
        components2 = components1.copy()
        random.shuffle(components2)

        env = "test_env"

        # Should produce same digest regardless of input order
        digest1 = compute_composite_digest(components1, env)
        digest2 = compute_composite_digest(components2, env)

        assert digest1 == digest2, "Composite digest should be order-independent"

    def test_composite_digest_domain_separation(self, tmp_path):
        """Domain separation should prevent ambiguity."""
        # Create files
        file1 = tmp_path / "ab.txt"
        file1.write_text("ab")
        file2 = tmp_path / "c.txt"
        file2.write_text("c")
        file3 = tmp_path / "a.txt"
        file3.write_text("a")
        file4 = tmp_path / "bc.txt"
        file4.write_text("bc")

        # Two different component sets that would be ambiguous without separators
        components1 = [
            ("DATA", "ab", compute_file_digest(file1)),
            ("DATA", "c", compute_file_digest(file2)),
        ]
        components2 = [
            ("DATA", "a", compute_file_digest(file3)),
            ("DATA", "bc", compute_file_digest(file4)),
        ]

        env = "test"

        # Should produce different digests due to domain separation
        digest1 = compute_composite_digest(components1, env)
        digest2 = compute_composite_digest(components2, env)

        assert digest1 != digest2, "Domain separation should prevent ambiguity"

    def test_composite_digest_env_affects_hash(self):
        """Environment digest should affect the composite."""
        components = [("MODEL", "test.py", "abc123")]

        digest1 = compute_composite_digest(components, "env1")
        digest2 = compute_composite_digest(components, "env2")

        assert digest1 != digest2, "Different environments should produce different digests"