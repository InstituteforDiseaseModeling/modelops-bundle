"""Tests for ModelOpsBundleRepository."""

from pathlib import Path
import pytest
from unittest.mock import Mock, patch, MagicMock

from modelops_bundle.repository import ModelOpsBundleRepository
from modelops_bundle.errors import NotFoundError


class TestModelOpsBundleRepository:
    """Test ModelOpsBundleRepository."""

    def test_init(self, tmp_path):
        """Test repository initialization."""
        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path),
            cache_structure="digest_short"
        )

        # Verify LocalCAS is initialized
        assert repo.cas is not None
        assert repo.cas.root == tmp_path

        # Verify bundles directory is created
        assert repo.bundles_dir.exists()
        assert repo.bundles_dir == tmp_path / "bundles"

        # Verify config is created
        assert repo.config is not None
        assert repo.config.registry_ref == "localhost:5000/test"
        assert repo.config.default_tag == "latest"

    def test_ensure_local_validates_digest(self, tmp_path):
        """Test ensure_local validates digest format."""
        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path)
        )

        # Invalid format
        with pytest.raises(ValueError, match="must be sha256 digest"):
            repo.ensure_local("md5:abc123")

        # Invalid length
        with pytest.raises(ValueError, match="Invalid digest length"):
            repo.ensure_local("sha256:tooshort")

    def test_ensure_local_checks_extracted_cache_first(self, tmp_path):
        """Test ensure_local returns already extracted bundle."""
        digest = "a" * 64
        bundle_ref = f"sha256:{digest}"

        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path),
            cache_structure="digest_short"
        )

        # Pre-create extracted bundle
        bundle_dir = repo.bundles_dir / digest[:12]
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "test.txt").write_text("cached content")

        # Should return cached path without fetching
        ref, path = repo.ensure_local(bundle_ref)

        assert ref == bundle_ref
        assert path == bundle_dir
        assert (path / "test.txt").read_text() == "cached content"

    def test_ensure_local_pulls_from_registry(self, tmp_path):
        """Test ensure_local pulls from registry when not cached."""
        digest = "b" * 64
        bundle_ref = f"sha256:{digest}"

        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path),
            cache_structure="digest_short"
        )

        # Mock ensure_local from ops module
        with patch("modelops_bundle.repository.ensure_local") as mock_ensure:
            # Configure mock to simulate successful pull
            def side_effect(*args, **kwargs):
                # Create the bundle directory when ensure_local is called
                dest = kwargs.get("dest")
                if dest:
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "test.txt").write_text("pulled content")
                return MagicMock()

            mock_ensure.side_effect = side_effect

            # Call ensure_local (bundle is NOT cached yet)
            ref, path = repo.ensure_local(bundle_ref)

            # Verify ensure_local was called with correct args
            mock_ensure.assert_called_once()
            call_args = mock_ensure.call_args
            assert call_args[1]["ref"] == f"localhost:5000/test@sha256:{digest}"

            bundle_dir = repo.bundles_dir / digest[:12]
            assert call_args[1]["dest"] == bundle_dir
            assert call_args[1]["mirror"] is True

            # Verify results
            assert ref == bundle_ref
            assert path == bundle_dir
            assert (path / "test.txt").read_text() == "pulled content"

    def test_exists_checks_extracted_bundles(self, tmp_path):
        """Test exists method checks extracted bundles."""
        digest = "c" * 64
        bundle_ref = f"sha256:{digest}"

        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path),
            cache_structure="digest_short"
        )

        # Initially doesn't exist
        assert not repo.exists(bundle_ref)

        # Add to extracted bundles
        bundle_dir = repo.bundles_dir / digest[:12]
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "test.txt").touch()

        # Now should exist (found in extracted)
        assert repo.exists(bundle_ref)

    def test_exists_handles_invalid_refs(self, tmp_path):
        """Test exists returns False for invalid refs."""
        repo = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path)
        )

        assert not repo.exists("not-a-digest")
        assert not repo.exists("sha256:tooshort")
        assert not repo.exists("md5:" + "d" * 64)

    def test_cache_structure_variations(self, tmp_path):
        """Test different cache structure options."""
        digest = "e" * 64
        bundle_ref = f"sha256:{digest}"

        # Test digest_short
        repo_short = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path / "short"),
            cache_structure="digest_short"
        )

        # Pre-create bundle
        bundle_dir = repo_short.bundles_dir / digest[:12]
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "test.txt").touch()

        ref, path = repo_short.ensure_local(bundle_ref)
        assert path == bundle_dir

        # Test digest_full
        repo_full = ModelOpsBundleRepository(
            registry_ref="localhost:5000/test",
            cache_dir=str(tmp_path / "full"),
            cache_structure="digest_full"
        )

        # Pre-create bundle
        bundle_dir = repo_full.bundles_dir / digest
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "test.txt").touch()

        ref, path = repo_full.ensure_local(bundle_ref)
        assert path == bundle_dir