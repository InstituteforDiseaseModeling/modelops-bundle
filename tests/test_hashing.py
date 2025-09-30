"""Tests for semantic hashing module."""

import tempfile
from pathlib import Path
import pytest

from modelops_bundle.hashing import (
    token_hash,
    file_hash,
    code_sig,
    compute_composite_digest,
    canonical_json,
)


class TestTokenHashing:
    """Test token-based semantic hashing of Python files."""

    def test_token_hash_ignores_formatting(self, tmp_path):
        """Token hash should be same despite formatting changes."""
        # Original code
        original = tmp_path / "model.py"
        original.write_text("""
def calculate(x, y):
    # Calculate sum
    result = x + y
    return result
""")

        # Reformatted code (different spacing, same logic)
        reformatted = tmp_path / "model_formatted.py"
        reformatted.write_text("""
def   calculate( x,y ):
        # Calculate sum
        result=x+y
        return    result
""")

        hash1 = token_hash(original)
        hash2 = token_hash(reformatted)

        assert hash1 == hash2, "Token hash should ignore formatting"

    def test_token_hash_ignores_comments(self, tmp_path):
        """Token hash should ignore comment changes."""
        without_comments = tmp_path / "no_comments.py"
        without_comments.write_text("""
def process(data):
    return data * 2
""")

        with_comments = tmp_path / "with_comments.py"
        with_comments.write_text("""
# This function processes data
def process(data):
    # Double the data
    return data * 2  # Return doubled value
""")

        hash1 = token_hash(without_comments)
        hash2 = token_hash(with_comments)

        assert hash1 == hash2, "Token hash should ignore comments"

    def test_token_hash_detects_semantic_changes(self, tmp_path):
        """Token hash should change when logic changes."""
        version1 = tmp_path / "v1.py"
        version1.write_text("""
def calculate(x, y):
    return x + y
""")

        version2 = tmp_path / "v2.py"
        version2.write_text("""
def calculate(x, y):
    return x * y  # Changed operation
""")

        hash1 = token_hash(version1)
        hash2 = token_hash(version2)

        assert hash1 != hash2, "Token hash should detect logic changes"

    def test_token_hash_detects_variable_rename(self, tmp_path):
        """Token hash detects variable name changes (limitation)."""
        original = tmp_path / "original.py"
        original.write_text("""
def calc(value):
    result = value * 2
    return result
""")

        renamed = tmp_path / "renamed.py"
        renamed.write_text("""
def calc(value):
    output = value * 2  # Variable renamed
    return output
""")

        hash1 = token_hash(original)
        hash2 = token_hash(renamed)

        # This is a known limitation - variable names matter
        assert hash1 != hash2, "Token hash detects variable renames (limitation)"


class TestFileHashing:
    """Test binary file hashing."""

    def test_file_hash_detects_any_change(self, tmp_path):
        """File hash should detect any byte change."""
        data_file = tmp_path / "data.csv"
        data_file.write_text("a,b,c\n1,2,3")

        hash1 = file_hash(data_file)

        # Change one character
        data_file.write_text("a,b,c\n1,2,4")  # Changed 3 to 4
        hash2 = file_hash(data_file)

        assert hash1 != hash2, "File hash should detect any change"

    def test_file_hash_deterministic(self, tmp_path):
        """File hash should be deterministic."""
        data_file = tmp_path / "data.txt"
        data_file.write_text("test content")

        hash1 = file_hash(data_file)
        hash2 = file_hash(data_file)

        assert hash1 == hash2, "File hash should be deterministic"


class TestCodeSignature:
    """Test code signature generation."""

    def test_code_sig_sorted_deterministic(self):
        """Code signature should be deterministic with sorting."""
        records1 = [
            ("src/b.py", "hash_b"),
            ("src/a.py", "hash_a"),
            ("src/c.py", "hash_c"),
        ]

        records2 = [
            ("src/c.py", "hash_c"),
            ("src/a.py", "hash_a"),
            ("src/b.py", "hash_b"),
        ]

        sig1 = code_sig(records1)
        sig2 = code_sig(records2)

        assert sig1 == sig2, "Code sig should be order-independent"

    def test_code_sig_changes_with_content(self):
        """Code signature changes when any hash changes."""
        records1 = [
            ("src/model.py", "hash_v1"),
            ("src/utils.py", "hash_utils"),
        ]

        records2 = [
            ("src/model.py", "hash_v2"),  # Different hash
            ("src/utils.py", "hash_utils"),
        ]

        sig1 = code_sig(records1)
        sig2 = code_sig(records2)

        assert sig1 != sig2, "Code sig should change when any hash changes"


class TestCompositeDigest:
    """Test composite digest with domain separation."""

    def test_composite_digest_deterministic(self):
        """Composite digest should be deterministic."""
        components = [
            ("MODEL_CODE", "src/model.py", "hash_model"),
            ("DATA", "data/pop.csv", "hash_data"),
            ("CODE_DEP", "src/utils.py", "hash_utils"),
        ]

        env_digest = "env_hash_12345"

        digest1 = compute_composite_digest(components, env_digest)
        digest2 = compute_composite_digest(components, env_digest)

        assert digest1 == digest2, "Composite digest should be deterministic"

    def test_composite_digest_order_independent(self):
        """Composite digest should be order-independent due to sorting."""
        components1 = [
            ("DATA", "data/pop.csv", "hash_data"),
            ("MODEL_CODE", "src/model.py", "hash_model"),
            ("CODE_DEP", "src/utils.py", "hash_utils"),
        ]

        components2 = [
            ("CODE_DEP", "src/utils.py", "hash_utils"),
            ("DATA", "data/pop.csv", "hash_data"),
            ("MODEL_CODE", "src/model.py", "hash_model"),
        ]

        env_digest = "env_hash"

        digest1 = compute_composite_digest(components1, env_digest)
        digest2 = compute_composite_digest(components2, env_digest)

        assert digest1 == digest2, "Order shouldn't matter after sorting"

    def test_composite_digest_changes_with_env(self):
        """Composite digest changes when environment changes."""
        components = [
            ("MODEL_CODE", "src/model.py", "hash_model"),
        ]

        digest1 = compute_composite_digest(components, "env1")
        digest2 = compute_composite_digest(components, "env2")

        assert digest1 != digest2, "Digest should change with environment"

    def test_composite_digest_domain_separation(self):
        """Domain separation should prevent collisions."""
        # These would collide without proper separation
        components1 = [
            ("MODEL_CODE", "ab", "cd"),
        ]

        components2 = [
            ("MODEL_CODE", "a", "bcd"),
        ]

        env_digest = "same_env"

        digest1 = compute_composite_digest(components1, env_digest)
        digest2 = compute_composite_digest(components2, env_digest)

        assert digest1 != digest2, "Domain separation should prevent collisions"


class TestCanonicalJson:
    """Test canonical JSON serialization."""

    def test_canonical_json_sorted_keys(self):
        """Canonical JSON should sort dictionary keys."""
        data = {"z": 1, "a": 2, "m": 3}
        result = canonical_json(data)

        # Keys should be in alphabetical order
        assert result == '{"a":2,"m":3,"z":1}'

    def test_canonical_json_nested_sorting(self):
        """Canonical JSON should sort nested dictionaries."""
        data = {
            "outer": {"z": 1, "a": 2},
            "another": {"b": 3, "c": 4}
        }

        result = canonical_json(data)
        expected = '{"another":{"b":3,"c":4},"outer":{"a":2,"z":1}}'

        assert result == expected

    def test_canonical_json_deterministic(self):
        """Canonical JSON should be deterministic."""
        data = {"key1": [1, 2, 3], "key2": {"nested": "value"}}

        result1 = canonical_json(data)
        result2 = canonical_json(data)

        assert result1 == result2