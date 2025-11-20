import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modelops_bundle.repository import ModelOpsBundleRepository
from modelops_bundle.storage_models import BundleFileEntry, BundleIndex, StorageType


def _make_index() -> BundleIndex:
    return BundleIndex(
        created="2024-01-01T00:00:00Z",
        tool={},
        files={
            "file.txt": BundleFileEntry(
                path="file.txt",
                digest="sha256:" + "a" * 64,
                size=5,
                storage=StorageType.OCI,
            )
        },
        metadata={},
    )


@pytest.fixture()
def repo(tmp_path: Path) -> ModelOpsBundleRepository:
    repository = ModelOpsBundleRepository(
        registry_ref="example.com/models",
        cache_dir=str(tmp_path),
    )
    # Stub CAS to avoid actual filesystem blobs
    repository.cas = MagicMock()
    repository.cas.has.return_value = False

    def materialize(digest, dest, mode="auto"):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("cached")

    repository.cas.materialize.side_effect = materialize

    adapter = MagicMock()
    adapter.get_index.side_effect = lambda registry, ref: _make_index()

    def pull_selected(**kwargs):
        output_dir: Path = kwargs["output_dir"]
        (output_dir / "file.txt").write_text("downloaded")

    adapter.pull_selected.side_effect = pull_selected
    repository._adapter = adapter
    repository._auth_provider = MagicMock()
    return repository


def test_first_pull_creates_marker_and_files(repo: ModelOpsBundleRepository):
    digest = "sha256:" + "a" * 64
    _, bundle_path = repo.ensure_local(digest)

    assert (bundle_path / "file.txt").read_text() == "downloaded"
    assert (bundle_path / ".complete").exists()
    assert repo._adapter.pull_selected.call_count == 1


def test_subsequent_calls_use_complete_marker(repo: ModelOpsBundleRepository):
    digest = "sha256:" + "a" * 64
    repo.ensure_local(digest)
    repo._adapter.pull_selected.reset_mock()

    repo.ensure_local(digest)
    assert repo._adapter.pull_selected.call_count == 0


def test_missing_marker_triggers_redownload(repo: ModelOpsBundleRepository):
    digest = "sha256:" + "a" * 64
    _, bundle_path = repo.ensure_local(digest)
    (bundle_path / ".complete").unlink()
    repo._adapter.pull_selected.reset_mock()

    repo.ensure_local(digest)
    assert repo._adapter.pull_selected.call_count == 1


def test_materialize_from_cache_when_available(repo: ModelOpsBundleRepository):
    digest = "sha256:" + "a" * 64
    _, bundle_path = repo.ensure_local(digest)

    # Simulate new host: remove bundle dir, clear marker, and mark CAS as populated.
    shutil.rmtree(bundle_path)
    repo.cas.has.return_value = True
    repo._adapter.pull_selected.reset_mock()

    repo.ensure_local(digest)

    assert repo.cas.materialize.call_count >= 1
    assert repo._adapter.pull_selected.call_count == 0
