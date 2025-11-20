"""ModelOps Bundle Repository for worker bundle fetching.

This module provides the BundleRepository implementation that ModelOps
workers use to fetch bundles from OCI registries.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Tuple, Optional

import logging
import portalocker

from .auth import get_auth_provider
from .core import BundleConfig
from .errors import NotFoundError
from .local_cache import LocalCAS
from .oras import OrasAdapter
from .storage_models import BundleIndex, StorageType

logger = logging.getLogger(__name__)


class ModelOpsBundleRepository:
    """Bundle repository implementation for ModelOps workers.

    This is the adapter that workers use to fetch bundles from
    OCI registries. It implements the BundleRepository protocol
    from modelops-contracts.

    Key responsibilities:
    - Fetch bundles from OCI registry
    - Cache bundles locally for reuse
    - Verify bundle integrity
    - Handle authentication
    """

    def __init__(
        self,
        registry_ref: str,
        cache_dir: str,
        cache_structure: str = "digest_short",
        default_tag: str = "latest",
        insecure: bool = False
    ):
        """Initialize repository with registry connection.

        Args:
            registry_ref: Registry URL (e.g., ghcr.io/org/models)
            cache_dir: Local cache directory for bundles
            cache_structure: How to organize cache ("digest_short", "digest_full")
            default_tag: Default tag if not specified in ref
            insecure: Whether to use insecure HTTP (for local dev)
        """
        self.registry_ref = registry_ref
        self.cache_dir = Path(cache_dir)
        self.cache_structure = cache_structure
        self.default_tag = default_tag
        self.insecure = insecure

        # Initialize LocalCAS for content-addressed storage
        # LocalCAS uses its own directory structure under cache_dir
        self.cas = LocalCAS(root=self.cache_dir)

        # We also keep a separate bundles directory for extracted content
        # LocalCAS stores tarballs, we need extracted directories
        self.bundles_dir = self.cache_dir / "bundles"
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        self.indexes_dir = self.cache_dir / "indexes"
        self.indexes_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir = self.cache_dir / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)

        # Create minimal config for pulling operations
        self.config = BundleConfig(
            registry_ref=self.registry_ref,
            default_tag=self.default_tag,
            storage={
                "provider": "oci",  # Pull from OCI only
                "mode": "oci-inline",
                "threshold": 0  # Not used for pulling
            },
            cache_dir=str(self.cache_dir)  # Use our cache directory
        )

        # Create OrasAdapter for registry operations
        self._auth_provider = None
        self._adapter = None

    def ensure_local(self, bundle_ref: str) -> Tuple[str, Path]:
        """Ensure bundle is available locally.

        This method must be resilient under concurrent access. Every digest
        is protected by a filesystem lock and a `.complete` marker so:

        1. Workers never reuse a directory that another process is still
           downloading into.
        2. Crash recovery is deterministic – partial directories are removed
           and re-fetched before reuse.
        3. When all files already exist in the LocalCAS, we rematerialize
           directly from cache instead of re-downloading from ORAS.

        Args:
            bundle_ref: Bundle reference (sha256:64-hex-chars)

        Returns:
            Tuple of (digest, local_path_to_bundle)

        Raises:
            NotFoundError: If bundle doesn't exist
            ValueError: If bundle_ref format is invalid
        """
        # Parse repository and digest from bundle_ref
        repository = None
        if "@" in bundle_ref:
            # Format: repository@sha256:digest
            repository, digest_part = bundle_ref.split("@", 1)
            if not digest_part.startswith("sha256:"):
                raise ValueError(f"Bundle ref must contain sha256 digest, got: {bundle_ref}")
            bundle_ref = digest_part
        elif not bundle_ref.startswith("sha256:"):
            raise ValueError(f"Bundle ref must be sha256 digest or repository@sha256:digest, got: {bundle_ref}")

        digest = bundle_ref.split(":", 1)[1]
        if len(digest) != 64:
            raise ValueError(f"Invalid digest length: expected 64 chars, got {len(digest)}")

        bundle_dir = self._bundle_dir_for_digest(digest)
        complete_marker = bundle_dir / ".complete"
        lock_path = self.locks_dir / f"{digest}.lock"

        # Use repository-specific registry ref if repository was provided
        effective_registry = f"{self.registry_ref}/{repository}" if repository else self.registry_ref

        with portalocker.Lock(str(lock_path), "w", timeout=300):
            if self._is_complete(bundle_dir, complete_marker):
                return bundle_ref, bundle_dir

            if bundle_dir.exists():
                shutil.rmtree(bundle_dir)
            bundle_dir.mkdir(parents=True, exist_ok=True)

            try:
                index = self._load_cached_index(digest)
                if index and self._can_materialize_from_cache(index):
                    logger.debug("Materializing bundle %s from LocalCAS", digest[:12])
                    self._materialize_from_cache(index, bundle_dir)
                else:
                    logger.info(f"Pulling bundle {digest[:12]} from registry {effective_registry}")
                    index = self._pull_from_registry(effective_registry, digest, bundle_dir)
                    self._save_index(digest, index)

                self._write_complete_marker(complete_marker)
                return bundle_ref, bundle_dir
            except Exception as e:
                shutil.rmtree(bundle_dir, ignore_errors=True)
                logger.error(f"Failed to pull bundle {digest[:12]}: {e}")
                raise NotFoundError(f"Could not fetch bundle {bundle_ref}: {e}") from e

    def exists(self, bundle_ref: str) -> bool:
        """Check if bundle exists in repository.

        Args:
            bundle_ref: Bundle reference to check

        Returns:
            True if bundle exists in extracted cache or registry
        """
        # Parse the reference
        if not bundle_ref.startswith("sha256:"):
            return False

        try:
            digest = bundle_ref.split(":", 1)[1]
            if len(digest) != 64:
                return False
        except (IndexError, ValueError):
            return False

        # Check extracted bundles first (fastest)
        if self.cache_structure == "digest_short":
            bundle_dir = self.bundles_dir / digest[:12]
        elif self.cache_structure == "digest_full":
            bundle_dir = self.bundles_dir / digest
        else:
            bundle_dir = self.bundles_dir / digest[:12]

        if bundle_dir.exists() and any(bundle_dir.iterdir()):
            return True

        # Check registry (slower)
        # For now, we'll assume if it's not cached locally, we need to check the registry
        # The actual registry check would require implementing or using existing registry client
        # Since this is primarily for workers that will pull anyway, we can be optimistic

        # TODO: Implement actual registry check when needed
        logger.debug(f"Bundle {digest[:12]} not in local cache, assuming it might exist in registry")
        return False  # Conservative: only return True if we know for sure

    def _bundle_dir_for_digest(self, digest: str) -> Path:
        if self.cache_structure == "digest_short":
            return self.bundles_dir / digest[:12]
        if self.cache_structure == "digest_full":
            return self.bundles_dir / digest
        return self.bundles_dir / digest[:12]

    def _is_complete(self, bundle_dir: Path, marker: Path) -> bool:
        return bundle_dir.exists() and marker.exists()

    def _write_complete_marker(self, marker: Path) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.write("ok")
        os.replace(tmp, marker)

    def _load_cached_index(self, digest: str) -> BundleIndex | None:
        index_path = self.indexes_dir / f"{digest}.json"
        if not index_path.exists():
            return None
        try:
            return BundleIndex.model_validate_json(index_path.read_text())
        except Exception as exc:
            logger.warning("Failed to load cached index for %s: %s", digest[:12], exc)
            return None

    def _save_index(self, digest: str, index: BundleIndex) -> None:
        index_path = self.indexes_dir / f"{digest}.json"
        tmp = index_path.with_suffix(".tmp")
        tmp.write_text(index.to_json_deterministic())
        os.replace(tmp, index_path)

    def _can_materialize_from_cache(self, index: BundleIndex) -> bool:
        for entry in index.files.values():
            if entry.storage != StorageType.OCI:
                return False
            if not self.cas.has(entry.digest):
                return False
        return True

    def _materialize_from_cache(self, index: BundleIndex, bundle_dir: Path) -> None:
        for entry in index.files.values():
            dest = bundle_dir / entry.path
            self.cas.materialize(entry.digest, dest, mode="auto")

    def _pull_from_registry(self, registry: str, digest: str, bundle_dir: Path) -> BundleIndex:
        index = self._get_adapter().get_index(registry, f"sha256:{digest}")
        entries = list(index.files.values())

        blob_store = None
        if any(e.storage == StorageType.BLOB for e in entries):
            logger.warning(
                "Bundle %s contains BLOB entries; ensure blob storage is configured",
                digest[:12],
            )

        self._adapter.pull_selected(
            registry_ref=registry,
            digest=f"sha256:{digest}",
            entries=entries,
            output_dir=bundle_dir,
            blob_store=blob_store,
            cas=self.cas,
            link_mode="auto",
        )

        if not bundle_dir.exists():
            raise RuntimeError(f"Bundle pull succeeded but path doesn't exist: {bundle_dir}")
        return index

    def _get_adapter(self) -> OrasAdapter:
        if self._auth_provider is None:
            self._auth_provider = get_auth_provider(self.registry_ref)
        if self._adapter is None:
            self._adapter = OrasAdapter(
                auth_provider=self._auth_provider,
                registry_ref=self.registry_ref,
                insecure=self.insecure,
            )
        return self._adapter


# Export the repository class
__all__ = ["ModelOpsBundleRepository"]
