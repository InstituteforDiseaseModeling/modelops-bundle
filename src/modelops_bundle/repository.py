"""ModelOps Bundle Repository for worker bundle fetching.

This module provides the BundleRepository implementation that ModelOps
workers use to fetch bundles from OCI registries.
"""

from pathlib import Path
from typing import Tuple, Optional
import logging

from .core import BundleConfig
from .errors import NotFoundError
from .local_cache import LocalCAS
from .oras import OrasAdapter
from .auth import get_auth_provider
from .storage_models import StorageType

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

        This is called by workers to get a bundle. It will:
        1. Check if bundle is already extracted in bundles directory
        2. If not, check if tarball is in CAS
        3. If not in CAS, pull from registry into CAS
        4. Extract from CAS to bundles directory
        5. Return path to extracted bundle

        Args:
            bundle_ref: Bundle reference (sha256:64-hex-chars)

        Returns:
            Tuple of (digest, local_path_to_bundle)

        Raises:
            NotFoundError: If bundle doesn't exist
            ValueError: If bundle_ref format is invalid
        """
        # Validate and parse the reference
        if not bundle_ref.startswith("sha256:"):
            raise ValueError(f"Bundle ref must be sha256 digest, got: {bundle_ref}")

        digest = bundle_ref.split(":", 1)[1]
        if len(digest) != 64:
            raise ValueError(f"Invalid digest length: expected 64 chars, got {len(digest)}")

        # Determine extraction path based on cache structure
        if self.cache_structure == "digest_short":
            bundle_dir = self.bundles_dir / digest[:12]
        elif self.cache_structure == "digest_full":
            bundle_dir = self.bundles_dir / digest
        else:
            bundle_dir = self.bundles_dir / digest[:12]

        # Check if already extracted
        if bundle_dir.exists() and any(bundle_dir.iterdir()):
            logger.debug(f"Bundle {digest[:12]} found extracted at {bundle_dir}")
            return bundle_ref, bundle_dir

        # Not in cache, need to pull from registry
        logger.info(f"Pulling bundle {digest[:12]} from registry {self.registry_ref}")

        try:
            # Ensure we have auth and adapter initialized
            if self._auth_provider is None:
                self._auth_provider = get_auth_provider(self.registry_ref)
            if self._adapter is None:
                self._adapter = OrasAdapter(
                    auth_provider=self._auth_provider,
                    registry_ref=self.registry_ref,
                    insecure=self.insecure
                )

            # Get the bundle index from the manifest
            logger.debug(f"Getting index for sha256:{digest}")
            index = self._adapter.get_index(self.registry_ref, f"sha256:{digest}")

            # Get list of files to pull
            entries = list(index.files.values())

            # Check if we need blob storage (we don't for inline storage)
            blob_store = None
            if any(e.storage == StorageType.BLOB for e in entries):
                # For workers, we don't expect BLOB storage, but handle gracefully
                logger.warning("Bundle contains BLOB storage entries, may fail if blob store not configured")

            # Ensure destination exists
            bundle_dir.mkdir(parents=True, exist_ok=True)

            # Pull all files directly to destination
            # Use LocalCAS for caching if available
            self._adapter.pull_selected(
                registry_ref=self.registry_ref,
                digest=f"sha256:{digest}",
                entries=entries,
                output_dir=bundle_dir,
                blob_store=blob_store,
                cas=self.cas,
                link_mode="auto"
            )

            if not bundle_dir.exists():
                raise RuntimeError(f"Bundle pull succeeded but path doesn't exist: {bundle_dir}")

            logger.info(f"Successfully pulled bundle {digest[:12]} to {bundle_dir}")
            return bundle_ref, bundle_dir

        except Exception as e:
            # Clean up failed extraction
            if bundle_dir.exists() and not any(bundle_dir.iterdir()):
                bundle_dir.rmdir()

            logger.error(f"Failed to pull bundle {digest[:12]}: {e}")
            raise NotFoundError(f"Could not fetch bundle {bundle_ref}: {e}")

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


# Export the repository class
__all__ = ["ModelOpsBundleRepository"]