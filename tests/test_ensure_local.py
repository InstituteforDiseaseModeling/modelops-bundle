"""Test ensure_local functionality."""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from modelops_bundle.core import BundleConfig
from modelops_bundle.context import ProjectContext
from modelops_bundle.ops import ensure_local, _scan_extras
from modelops_bundle.ops import EnsureLocalResult
from modelops_bundle.storage_models import BundleIndex, BundleFileEntry, StorageType
from modelops_bundle.oras import OrasAdapter


class MockOrasAdapter:
    """Mock OrasAdapter for testing ensure_local."""
    
    def __init__(self, index_files=None):
        self.index_files = index_files or {}
        self.pull_selected_called = False
        self.pull_selected_entries = None
    
    def resolve_tag_to_digest(self, registry_ref, tag):
        """Mock tag resolution."""
        return f"sha256:resolved_{tag}"
    
    def get_index(self, registry_ref, digest):
        """Mock index retrieval."""
        index = BundleIndex(
            version="1.0",
            created="2024-01-01T00:00:00Z",
            files=self.index_files
        )
        return index
    
    def pull_selected(self, registry_ref, digest, entries, output_dir, blob_store=None, cas=None, link_mode="auto"):
        """Mock file pulling."""
        self.pull_selected_called = True
        self.pull_selected_entries = entries
        
        # Create mock files
        for entry in entries:
            file_path = output_dir / entry.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(f"mock content for {entry.path}")


@pytest.fixture
def mock_config():
    """Create mock bundle config."""
    config = Mock(spec=BundleConfig)
    config.registry_ref = "test.registry.com/repo"
    config.default_tag = "latest"
    config.storage = Mock(uses_blob_storage=False)
    return config


@pytest.fixture
def mock_adapter():
    """Create mock OrasAdapter."""
    return MockOrasAdapter()


@pytest.fixture
def sample_index_files():
    """Create sample index files."""
    return {
        "file1.txt": BundleFileEntry(
            path="file1.txt",
            digest="sha256:file1",
            size=100,
            storage=StorageType.OCI
        ),
        "dir/file2.py": BundleFileEntry(
            path="dir/file2.py",
            digest="sha256:file2",
            size=200,
            storage=StorageType.OCI
        ),
        "nested/deep/file3.md": BundleFileEntry(
            path="nested/deep/file3.md",
            digest="sha256:file3",
            size=150,
            storage=StorageType.OCI
        ),
    }


@pytest.fixture
def mock_ctx(tmp_path):
    """Create mock ProjectContext."""
    ctx = Mock(spec=ProjectContext)
    ctx.root = tmp_path
    return ctx


class TestEnsureLocalBasic:
    """Test basic ensure_local functionality."""
    
    def test_ensure_local_basic(self, tmp_path, mock_config, mock_ctx):
        """Test basic ensure_local operation."""
        dest = tmp_path / "dest"
        mock_adapter = MockOrasAdapter({
            "test.txt": BundleFileEntry(
                path="test.txt",
                digest="sha256:test",
                size=50,
                storage=StorageType.OCI
            )
        })
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=False,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert isinstance(result, EnsureLocalResult)
        assert result.downloaded == 1
        assert result.bytes_downloaded == 50
        assert result.deleted == 0
        assert not result.dry_run
        assert (dest / "test.txt").exists()
    
    def test_ensure_local_with_subdirs(self, tmp_path, mock_config, sample_index_files, mock_ctx):
        """Test ensure_local with nested directories."""
        dest = tmp_path / "dest"
        mock_adapter = MockOrasAdapter(sample_index_files)
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=False,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert result.downloaded == 3
        assert result.bytes_downloaded == 450  # 100 + 200 + 150
        assert (dest / "file1.txt").exists()
        assert (dest / "dir" / "file2.py").exists()
        assert (dest / "nested" / "deep" / "file3.md").exists()
    
    def test_ensure_local_overwrite_existing(self, tmp_path, mock_config, mock_ctx):
        """Test that ensure_local overwrites existing files."""
        dest = tmp_path / "dest"
        dest.mkdir()
        existing = dest / "file1.txt"
        existing.write_text("old content")
        
        mock_adapter = MockOrasAdapter({
            "file1.txt": BundleFileEntry(
                path="file1.txt",
                digest="sha256:new",
                size=100,
                storage=StorageType.OCI
            )
        })
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=False,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert result.downloaded == 1
        content = existing.read_text()
        assert content == "mock content for file1.txt"
        assert "old content" not in content
    
    def test_ensure_local_with_digest_ref(self, tmp_path, mock_config, mock_ctx):
        """Test ensure_local with sha256: reference."""
        dest = tmp_path / "dest"
        mock_adapter = MockOrasAdapter({
            "file.txt": BundleFileEntry(
                path="file.txt",
                digest="sha256:file",
                size=50,
                storage=StorageType.OCI
            )
        })
        
        # Should not call resolve_tag_to_digest for sha256: refs
        mock_adapter.resolve_tag_to_digest = Mock(side_effect=Exception("Should not be called"))
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="sha256:abcd1234",
                dest=dest,
                mirror=False,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert result.resolved_digest == "sha256:abcd1234"
        assert result.downloaded == 1


class TestEnsureLocalMirror:
    """Test mirror mode functionality."""
    
    def test_ensure_local_mirror_deletes_extras(self, tmp_path, mock_config, mock_ctx):
        """Test that mirror mode deletes extra files."""
        dest = tmp_path / "dest"
        dest.mkdir()
        
        # Create extra files that aren't in bundle
        (dest / "extra1.txt").write_text("extra")
        (dest / "subdir").mkdir()
        (dest / "subdir" / "extra2.txt").write_text("extra")
        
        # Create a file that IS in bundle
        (dest / "keep.txt").write_text("old")
        
        mock_adapter = MockOrasAdapter({
            "keep.txt": BundleFileEntry(
                path="keep.txt",
                digest="sha256:keep",
                size=50,
                storage=StorageType.OCI
            )
        })
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=True,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert result.downloaded == 1
        assert result.deleted == 2  # extra1.txt and subdir/extra2.txt
        assert (dest / "keep.txt").exists()
        assert not (dest / "extra1.txt").exists()
        assert not (dest / "subdir" / "extra2.txt").exists()
    
    def test_ensure_local_mirror_preserves_exact(self, tmp_path, mock_config, sample_index_files, mock_ctx):
        """Test that mirror mode preserves exact bundle contents."""
        dest = tmp_path / "dest"
        
        mock_adapter = MockOrasAdapter(sample_index_files)
        
        # First ensure
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result1 = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=True,
                dry_run=False,
                ctx=mock_ctx
            )
        
        # Add an extra file
        (dest / "extra.txt").write_text("extra")
        
        # Second ensure with mirror should remove it
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result2 = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=True,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert result2.deleted == 1
        assert not (dest / "extra.txt").exists()
        
        # Only bundle files should exist
        files_in_dest = set()
        for root, _, files in os.walk(dest):
            for f in files:
                rel_path = Path(root).relative_to(dest) / f
                files_in_dest.add(rel_path.as_posix())
        
        expected_files = {"file1.txt", "dir/file2.py", "nested/deep/file3.md"}
        assert files_in_dest == expected_files


class TestEnsureLocalDryRun:
    """Test dry run mode."""
    
    def test_ensure_local_dry_run(self, tmp_path, mock_config, mock_ctx):
        """Test dry run doesn't modify filesystem."""
        dest = tmp_path / "dest"
        
        mock_adapter = MockOrasAdapter({
            "file.txt": BundleFileEntry(
                path="file.txt",
                digest="sha256:file",
                size=100,
                storage=StorageType.OCI
            )
        })
        
        # Ensure pull_selected is not called in dry run
        mock_adapter.pull_selected = Mock(side_effect=Exception("Should not pull in dry run"))
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=False,
                dry_run=True,
                ctx=mock_ctx
            )
        
        assert result.downloaded == 1
        assert result.bytes_downloaded == 100
        assert result.dry_run == True
        assert not dest.exists()  # Destination not even created
    
    def test_ensure_local_dry_run_with_mirror(self, tmp_path, mock_config, mock_ctx):
        """Test dry run with mirror mode calculates deletions."""
        dest = tmp_path / "dest"
        dest.mkdir()
        
        # Create files that would be deleted
        (dest / "extra1.txt").write_text("extra")
        (dest / "extra2.txt").write_text("extra")
        
        mock_adapter = MockOrasAdapter({
            "keep.txt": BundleFileEntry(
                path="keep.txt",
                digest="sha256:keep",
                size=50,
                storage=StorageType.OCI
            )
        })
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=True,
                dry_run=True,
                ctx=mock_ctx
            )
        
        assert result.downloaded == 1
        assert result.deleted == 2  # Would delete 2 extras
        assert result.dry_run == True
        # Files should still exist (dry run)
        assert (dest / "extra1.txt").exists()
        assert (dest / "extra2.txt").exists()


class TestEnsureLocalEdgeCases:
    """Test edge cases and error handling."""
    
    def test_ensure_local_default_ref(self, tmp_path, mock_config, mock_ctx):
        """Test ensure_local uses default tag when ref is None."""
        dest = tmp_path / "dest"
        mock_adapter = MockOrasAdapter()
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            with patch.object(mock_adapter, 'resolve_tag_to_digest') as mock_resolve:
                mock_resolve.return_value = "sha256:default"
                
                result = ensure_local(
                    mock_config,
                    ref=None,  # Should use default
                    dest=dest,
                    mirror=False,
                    dry_run=False,
                    ctx=mock_ctx
                )
                
                mock_resolve.assert_called_once_with("test.registry.com/repo", "latest")
    
    def test_ensure_local_blob_storage(self, tmp_path, mock_config, mock_ctx):
        """Test ensure_local with blob storage entries."""
        from modelops_bundle.storage_models import BlobReference
        
        dest = tmp_path / "dest"
        mock_adapter = MockOrasAdapter({
            "blob_file.txt": BundleFileEntry(
                path="blob_file.txt",
                digest="sha256:blob",
                size=100,
                storage=StorageType.BLOB,
                blobRef=BlobReference(uri="azure://container/blob")
            )
        })
        
        mock_blob_store = Mock()
        mock_config.storage.uses_blob_storage = True
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            with patch('modelops_bundle.ops.make_blob_store', return_value=mock_blob_store):
                result = ensure_local(
                    mock_config,
                    ref="v1.0",
                    dest=dest,
                    mirror=False,
                    dry_run=False,
                    ctx=mock_ctx
                )
        
        assert mock_adapter.pull_selected_called
        assert mock_blob_store is not None
    
    def test_ensure_local_creates_dest_dir(self, tmp_path, mock_config, mock_ctx):
        """Test that ensure_local creates destination directory."""
        dest = tmp_path / "new" / "nested" / "dest"
        assert not dest.exists()
        
        mock_adapter = MockOrasAdapter({
            "file.txt": BundleFileEntry(
                path="file.txt",
                digest="sha256:file",
                size=50,
                storage=StorageType.OCI
            )
        })
        
        with patch('modelops_bundle.ops.OrasAdapter', return_value=mock_adapter):
            result = ensure_local(
                mock_config,
                ref="v1.0",
                dest=dest,
                mirror=False,
                dry_run=False,
                ctx=mock_ctx
            )
        
        assert dest.exists()
        assert dest.is_dir()


class TestScanExtras:
    """Test _scan_extras helper function."""
    
    def test_scan_extras_basic(self, tmp_path):
        """Test scanning for extra files."""
        # Create test structure
        (tmp_path / "expected.txt").write_text("expected")
        (tmp_path / "extra.txt").write_text("extra")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "expected2.txt").write_text("expected")
        (tmp_path / "subdir" / "extra2.txt").write_text("extra")
        
        expected_files = {"expected.txt", "subdir/expected2.txt"}
        extras = _scan_extras(tmp_path, expected_files)
        
        assert set(extras) == {"extra.txt", "subdir/extra2.txt"}
    
    def test_scan_extras_empty_dir(self, tmp_path):
        """Test scanning empty directory."""
        extras = _scan_extras(tmp_path, {"file.txt"})
        assert extras == []
    
    def test_scan_extras_nonexistent_dir(self, tmp_path):
        """Test scanning non-existent directory."""
        extras = _scan_extras(tmp_path / "nonexistent", {"file.txt"})
        assert extras == []
    
    def test_scan_extras_no_extras(self, tmp_path):
        """Test when all files are expected."""
        (tmp_path / "file1.txt").write_text("content")
        (tmp_path / "file2.txt").write_text("content")
        
        extras = _scan_extras(tmp_path, {"file1.txt", "file2.txt"})
        assert extras == []