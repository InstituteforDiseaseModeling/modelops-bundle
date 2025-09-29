"""ModelOps BundleRepository adapter implementation.

This adapter implements the BundleRepository protocol from modelops-contracts,
allowing modelops-bundle to be used as a bundle source without ModelOps
directly depending on modelops-bundle.
"""

import hashlib
import logging
import shutil
import uuid
from pathlib import Path
from typing import Tuple, Optional

# Import the protocol we're implementing
from modelops_contracts.ports import BundleRepository

# Import our own modules
from .. import ops
from ..core import BundleConfig
from ..oras import OrasAdapter

logger = logging.getLogger(__name__)


class ModelOpsBundleRepository(BundleRepository):
    """Bundle repository using modelops-bundle for OCI artifacts.
    
    This is the reference implementation of BundleRepository for OCI registries.
    It provides:
    - OCI registry integration (pull from ghcr.io, ACR, etc.)
    - Digest-based caching for immutability
    - Atomic downloads with completeness markers
    - Configurable cache directory structure
    """
    
    def __init__(
        self,
        registry_ref: str,
        cache_dir: str,
        cache_structure: str = "digest",
        default_tag: str = "latest"
    ):
        """Initialize the repository.

        Args:
            registry_ref: OCI registry URL (e.g., "ghcr.io/org/models")
            cache_dir: Local directory for cached bundles
            cache_structure: How to structure the cache:
                - "digest": Full digest as directory name
                - "digest_short": First 12 chars of digest
                - "digest_nested": Git-style nested (ab/cd/ef...)
            default_tag: Default tag to use if not specified
        """
        self.registry_ref = registry_ref
        self.cache_dir = Path(cache_dir)
        self.cache_structure = cache_structure
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Create BundleConfig for modelops-bundle operations
        # Use default StoragePolicy (auto mode)
        from ..policy import StoragePolicy
        self.config = BundleConfig(
            environment="local",  # Default to local for adapter usage
            registry_ref=registry_ref,
            default_tag=default_tag,
            storage=StoragePolicy()  # Default storage policy
        )
        
        # Create OrasAdapter for registry operations
        self.oras = OrasAdapter()
    
    def ensure_local(self, bundle_ref: str) -> Tuple[str, Path]:
        """Ensure bundle is available locally and return its digest and path.
        
        This is the main method required by the BundleRepository protocol.
        
        Args:
            bundle_ref: Bundle reference - can be:
                - SHA256 digest: "sha256:abc123..."
                - Registry tag: "mymodel:v1.0.0" 
                - Full OCI ref: "oci://ghcr.io/org/model:tag"
                - file:// URLs are NOT supported (use FileBundleRepository)
            
        Returns:
            Tuple of (digest, local_path) where:
                - digest is the sha256 hex string (without "sha256:" prefix)
                - local_path is the Path to the extracted bundle
            
        Raises:
            ValueError: If bundle_ref format is invalid or fetch fails
        """
        if not bundle_ref:
            raise ValueError("Bundle reference cannot be empty")
        
        # Reject file:// URLs - those should use FileBundleRepository
        if bundle_ref.startswith("file://"):
            raise ValueError(
                "file:// URLs not supported by ModelOpsBundleRepository. "
                "Use FileBundleRepository for local bundles."
            )
        
        # Determine the ref format and resolve to digest
        digest = self._resolve_to_digest(bundle_ref)

        # Determine cache path based on structure setting
        bundle_path = self._get_cache_path(digest)

        # Check if already cached
        if self._is_cached(bundle_path):
            logger.info(f"Bundle {digest[:12]}... already cached at {bundle_path}")
            return digest, bundle_path

        # Fetch the bundle (handles concurrent access atomically)
        self._fetch_bundle(bundle_ref, digest, bundle_path)

        return digest, bundle_path
    
    def _resolve_to_digest(self, bundle_ref: str) -> str:
        """Resolve a bundle reference to its digest.
        
        Args:
            bundle_ref: Bundle reference (digest, tag, or full OCI ref)
            
        Returns:
            The sha256 digest (without "sha256:" prefix)
        """
        if bundle_ref.startswith("sha256:"):
            # Already a digest
            return bundle_ref[7:]
        
        # For OCI refs, we need to resolve tag to digest
        if bundle_ref.startswith("oci://"):
            ref_for_resolve = bundle_ref[6:]  # Remove "oci://" prefix
        else:
            # Assume it's a tag relative to our registry
            ref_for_resolve = bundle_ref
        
        # Use OrasAdapter to resolve to digest
        try:
            full_ref = f"{self.registry_ref}:{ref_for_resolve}"
            digest = self.oras.resolve_tag_to_digest(self.registry_ref, ref_for_resolve)
            
            # Remove "sha256:" prefix if present
            if digest.startswith("sha256:"):
                digest = digest[7:]
            
            logger.info(f"Resolved {bundle_ref} to digest {digest[:12]}...")
            return digest
            
        except Exception as e:
            raise ValueError(f"Failed to resolve bundle reference {bundle_ref}: {e}")
    
    def _get_cache_path(self, digest: str) -> Path:
        """Get the cache path for a digest based on cache_structure setting.
        
        Args:
            digest: The sha256 hex digest
            
        Returns:
            Path to the cache directory for this bundle
        """
        if self.cache_structure == "digest_short":
            # Use first 12 chars like Docker
            return self.cache_dir / digest[:12]
        elif self.cache_structure == "digest_nested":
            # Git-style nested directories (ab/cd/ef/rest...)
            return self.cache_dir / digest[:2] / digest[2:4] / digest[4:]
        else:
            # Default: full digest
            return self.cache_dir / digest
    
    def _is_cached(self, bundle_path: Path) -> bool:
        """Check if a bundle is fully cached.
        
        Args:
            bundle_path: Path to the bundle directory
            
        Returns:
            True if the bundle is fully cached and ready
        """
        if not bundle_path.exists():
            return False
        
        # Check for completeness marker
        marker_file = bundle_path / ".modelops_bundle_complete"
        return marker_file.exists()
    
    def _fetch_bundle(self, bundle_ref: str, digest: str, bundle_path: Path) -> None:
        """Fetch a bundle from the registry using atomic directory operations.

        This method is safe for concurrent access. Multiple processes can call this
        simultaneously for the same digest, and only one will succeed in placing
        the bundle at the final location.

        Args:
            bundle_ref: Original bundle reference
            digest: Resolved digest
            bundle_path: Where to store the bundle

        Raises:
            ValueError: If fetch fails
        """
        # Quick recheck in case another process just completed it
        if self._is_cached(bundle_path):
            logger.debug(f"Bundle {digest[:12]}... was cached during fetch preparation")
            return

        # Create temporary directory for atomic download
        temp_dir = bundle_path.parent / f".tmp-{digest[:12]}-{uuid.uuid4().hex[:8]}"
        logger.info(f"Fetching bundle {bundle_ref} (digest: {digest[:12]}...) to temp dir")

        try:
            # Ensure temp directory exists
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Use OrasAdapter directly to fetch the bundle
            # Get the index which contains the file entries
            index = self.oras.get_index(self.config.registry_ref, f"sha256:{digest}")

            # Download files to temp directory
            entries = list(index.files.values())
            self.oras.pull_selected(
                registry_ref=self.config.registry_ref,
                digest=f"sha256:{digest}",
                entries=entries,
                output_dir=temp_dir,
                blob_store=None,  # No blob storage for adapter
                cas=None,  # No local CAS for adapter
                link_mode="copy"
            )

            # Mark as complete in temp directory
            marker_file = temp_dir / ".modelops_bundle_complete"
            marker_file.touch()

            # Atomic rename to final location
            try:
                # Ensure parent directory exists
                bundle_path.parent.mkdir(parents=True, exist_ok=True)

                # Attempt atomic rename
                temp_dir.rename(bundle_path)
                logger.info(
                    f"Successfully placed bundle at {bundle_path}: "
                    f"{len(entries)} files"
                )

            except OSError as e:
                # Rename failed - check why
                if bundle_path.exists():
                    if self._is_cached(bundle_path):
                        # Another process won the race with a complete bundle
                        logger.debug(f"Bundle {digest[:12]}... was placed by another process")
                        # Clean up our temp directory
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    else:
                        # Incomplete bundle exists - clean it up and retry once
                        logger.warning(f"Removing incomplete bundle at {bundle_path}")
                        shutil.rmtree(bundle_path, ignore_errors=True)
                        try:
                            temp_dir.rename(bundle_path)
                            logger.info(f"Successfully replaced incomplete bundle at {bundle_path}")
                        except OSError:
                            # Still failed - give up
                            raise
                else:
                    # Some other error (permissions, disk full, etc.)
                    raise

        except Exception as e:
            # Clean up temp directory on any failure
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(f"Failed to fetch bundle {bundle_ref}: {e}")
    
    def compute_digest(self, bundle_path: Path) -> str:
        """Compute digest of a local bundle directory.
        
        This is an optional method that some implementations provide.
        For OCI bundles, we prefer the manifest digest from the registry
        as the authoritative source.
        
        Args:
            bundle_path: Path to bundle directory
            
        Returns:
            SHA256 hex digest string
        """
        hasher = hashlib.sha256()
        
        # Sort files for deterministic ordering
        for file_path in sorted(bundle_path.rglob("*")):
            if file_path.is_file():
                # Skip our marker file
                if file_path.name == ".modelops_bundle_complete":
                    continue
                
                # Include relative path in hash for structure
                rel_path = file_path.relative_to(bundle_path)
                hasher.update(str(rel_path).encode())
                
                # Include file content
                with open(file_path, "rb") as f:
                    while chunk := f.read(8192):
                        hasher.update(chunk)
        
        return hasher.hexdigest()