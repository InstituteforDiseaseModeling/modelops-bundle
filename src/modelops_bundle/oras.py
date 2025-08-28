"""ORAS adapter for OCI registry operations."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import json
import logging
import tempfile
import time
import warnings

from oras.provider import Registry
from oras.container import Container

from .context import ProjectContext
from .constants import BUNDLE_VERSION, BUNDLE_INDEX_MEDIA_TYPE
from .core import FileInfo, RemoteState
from .errors import (
    AuthError,
    DigestMismatchError,
    MissingIndexError,
    NetworkError,
    NotFoundError,
    UnsupportedArtifactError,
)
from .storage_models import BundleIndex, BundleFileEntry, StorageType
from .utils import get_iso_timestamp, compute_digest

# OCI media types for manifest accept headers
OCI_ACCEPT = ",".join([
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
])


class OrasAdapter:
    """Adapter for ORAS operations using real oras-py."""
    
    def __init__(self, insecure: bool = True):
        """Initialize ORAS Registry client."""
        self.client = Registry(insecure=insecure)
    
    def _build_target(self, registry_ref: str, reference: str) -> str:
        """Build full target reference (works with tags or digests)."""
        if reference.startswith("sha256:"):
            # It's a digest, use @ notation
            return f"{registry_ref}@{reference}"
        else:
            # It's a tag, use : notation
            return f"{registry_ref}:{reference}"
    
    def _try_head_for_digest(self, container: Container) -> Optional[str]:
        """Try to get digest via HEAD request (faster, fewer bytes).
        
        Returns digest string if found, None otherwise.
        """
        try:
            head_url = f"{self.client.prefix}://{container.manifest_url()}"
            resp = self.client.do_request(
                head_url,
                "HEAD", 
                headers={"Accept": OCI_ACCEPT},
            )
            
            if resp.status_code == 200:
                return resp.headers.get("Docker-Content-Digest")
        except Exception:
            # HEAD might not be supported, fall back to GET
            pass
        return None
    
    def get_digest_only(
        self, registry_ref: str, reference: str = "latest"
    ) -> str:
        """Get just the digest for a manifest (optimized with HEAD first).
        
        Useful when you only need the digest, not the full manifest.
        """
        target = self._build_target(registry_ref, reference)
        container = Container(target)
        
        # Try HEAD first (faster, less bytes)
        digest = self._try_head_for_digest(container)
        if digest:
            return digest
        
        # Fall back to full GET
        _, digest, _ = self.get_manifest_with_digest(registry_ref, reference)
        return digest
    
    def resolve_tag_to_digest(
        self, registry_ref: str, tag: str = "latest"
    ) -> str:
        """Resolve a tag to its current digest (for race prevention)."""
        return self.get_digest_only(registry_ref, tag)
    
    def get_current_tag_digest(
        self, registry_ref: str, tag: str = "latest"
    ) -> Optional[str]:
        """Get current digest for a tag, or None if not found."""
        try:
            return self.get_digest_only(registry_ref, tag)
        except Exception:
            return None
    
    def get_manifest_with_digest(
        self, registry_ref: str, reference: str = "latest", retries: int = 3
    ) -> Tuple[dict, str, bytes]:
        """
        Return (manifest_json, canonical_digest, raw_bytes).
        
        Reference can be a tag (e.g., "latest") or digest (e.g., "sha256:...").
        Digest comes from Docker-Content-Digest header when available;
        otherwise it is computed from the exact raw bytes.
        Includes retry logic for eventual consistency after push.
        """
        target = self._build_target(registry_ref, reference)
        container = Container(target)
        
        # Build the manifest URL path
        get_manifest_url = f"{self.client.prefix}://{container.manifest_url()}"
        
        last_error = None
        for attempt in range(retries):
            try:
                # Use oras-py's do_request which handles auth for us
                resp = self.client.do_request(
                    get_manifest_url,
                    "GET",
                    headers={"Accept": OCI_ACCEPT},
                )
                
                # Check response status
                if resp.status_code == 404:
                    if attempt < retries - 1:
                        # Registry might have eventual consistency delay after push
                        time.sleep(0.2 * (attempt + 1))  # 200ms, 400ms backoff
                        continue
                    raise NotFoundError(f"Manifest not found: {target}")
                elif resp.status_code in (401, 403):
                    raise AuthError(f"Authentication failed for {target}")
                elif resp.status_code != 200:
                    if resp.status_code >= 500:
                        raise NetworkError(f"Registry error {resp.status_code}: {target}")
                    resp.raise_for_status()
                
                raw = resp.content or b""
                manifest = resp.json() if raw else {}
                
                # Check if this is an index/manifest list
                media_type = manifest.get("mediaType", "")
                if "index" in media_type or "list" in media_type or manifest.get("manifests"):
                    raise UnsupportedArtifactError(target, media_type or "manifest index/list")
                
                digest = resp.headers.get("Docker-Content-Digest")
                if not digest:
                    # Fallback: compute from raw bytes exactly as served
                    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
                    # Use logging instead of warnings for better CLI integration
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Registry did not return Docker-Content-Digest for {target}; "
                        "using digest computed from raw manifest bytes."
                    )
                
                return manifest, digest, raw
                
            except (NotFoundError, NetworkError) as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                # Map connection errors
                import requests
                if isinstance(e, requests.exceptions.ConnectionError):
                    raise NetworkError(f"Cannot connect to registry: {e}")
                raise
        
        # Should not reach here, but be defensive
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch manifest after {retries} attempts")
    
    def _create_path_annotations(self, abs_paths: List[str], rel_paths: List[str]) -> dict:
        """Create annotation mapping to preserve full paths.
        
        HACK: Works around oras-py stripping paths to basename.
        Maps absolute paths (what we pass to ORAS) to annotations that preserve relative paths.
        See: https://github.com/oras-project/oras-py/issues/217
        
        Args:
            abs_paths: Absolute paths passed to ORAS push
            rel_paths: Relative paths to preserve in annotations
        """
        annotations = {}
        for abs_path, rel_path in zip(abs_paths, rel_paths):
            annotations[abs_path] = {
                "org.opencontainers.image.title": rel_path
            }
        return annotations
    
    
    def get_manifest(
        self,
        registry_ref: str,
        reference: str = "latest"  # Can be tag or digest
    ) -> dict:
        """Get manifest from registry."""
        manifest, _, _ = self.get_manifest_with_digest(registry_ref, reference)
        return manifest
    
    def get_remote_state(
        self,
        registry_ref: str,
        reference: str = "latest"  # Can be tag or digest
    ) -> RemoteState:
        """Get remote state from BundleIndex (always required)."""
        # First resolve to digest
        manifest, manifest_digest, _ = self.get_manifest_with_digest(registry_ref, reference)
        
        # Guard against index/manifest list
        if manifest.get("manifests"):
            raise UnsupportedArtifactError(
                f"{registry_ref}:{reference}",
                manifest.get("mediaType", "manifest index/list")
            )
        
        # Get BundleIndex from manifest config
        try:
            index = self.get_index(registry_ref, manifest_digest)
        except ValueError as e:
            raise MissingIndexError(f"{registry_ref}:{reference}") from e
        
        # Convert index to RemoteState
        files = {}
        for path, entry in index.files.items():
            files[path] = FileInfo(
                path=path,
                digest=entry.digest,
                size=entry.size
            )
        
        return RemoteState(
            manifest_digest=manifest_digest,
            files=files
        )
    
    def list_tags(
        self,
        registry_ref: str
    ) -> List[str]:
        """List all tags for a repository.
        
        Returns list of tag names.
        """
        # The oras-py Registry client has a get_tags method
        tags = self.client.get_tags(registry_ref)
        return list(tags) if tags else []
    
    # ============= NEW INDEX-BASED METHODS =============
    
    def push_with_index_config(
        self,
        registry_ref: str,
        tag: str,
        oci_file_paths: List[Tuple[Path, str]],
        index: BundleIndex,
        manifest_annotations: Optional[Dict] = None,
    ) -> str:
        """
        Push files with BundleIndex as manifest config.
        
        The index is the sole source of truth, but we also preserve paths in layer 
        annotations for backward compatibility with get_remote_state.
        
        Args:
            registry_ref: Registry reference (e.g., localhost:5000/myrepo)
            tag: Tag to push to
            oci_file_paths: List of (absolute_path, relative_path) tuples for OCI layers
            index: BundleIndex to store as manifest config
            manifest_annotations: Optional manifest annotations
            
        Returns:
            Canonical digest of pushed manifest
        """
        target = self._build_target(registry_ref, tag)
        
        # Serialize index using deterministic JSON
        index_json = index.to_json_deterministic(indent=2).encode("utf-8")
        
        # Write index to temporary file
        with tempfile.NamedTemporaryFile(suffix=".json", mode="wb", delete=False) as tmp:
            tmp.write(index_json)
            tmp.flush()
            tmp_path = tmp.name
        
        # Create path annotations for OCI files only (for backward compat)
        anno_file_path = None
        abs_paths = []
        rel_paths = []
        if oci_file_paths:
            for abs_path, rel_path in oci_file_paths:
                abs_paths.append(str(abs_path))
                rel_paths.append(rel_path)
            
            # Create annotations with consistent absolute path keys
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as anno_file:
                annotations = self._create_path_annotations(abs_paths, rel_paths)
                json.dump(annotations, anno_file)
                anno_file.flush()
                anno_file_path = anno_file.name
        
        try:
            # Push with index as manifest config
            files_to_push = abs_paths  # Already converted to strings above
            
            # Push to registry
            push_args = {
                "target": target,
                "files": files_to_push,
                "manifest_config": f"{tmp_path}:{BUNDLE_INDEX_MEDIA_TYPE}",
                "manifest_annotations": manifest_annotations or {},
            }
            
            # Add annotation file if we have OCI files (for backward compat)
            if anno_file_path:
                push_args["annotation_file"] = anno_file_path
            
            resp = self.client.push(**push_args)
            
            # Try to get digest from response headers
            digest = resp.headers.get("Docker-Content-Digest") if resp else None
            
            if not digest:
                # Fallback: resolve via HEAD request
                digest = self.resolve_tag_to_digest(registry_ref, tag)
            
            return digest
            
        finally:
            # Clean up temp files
            Path(tmp_path).unlink(missing_ok=True)
            if anno_file_path:
                Path(anno_file_path).unlink(missing_ok=True)
    
    def get_index(self, registry_ref: str, digest: str) -> BundleIndex:
        """
        Get BundleIndex from manifest config (always by digest, never by tag).
        
        Args:
            registry_ref: Registry reference
            digest: Manifest digest (sha256:...)
            
        Returns:
            BundleIndex from manifest config
            
        Raises:
            MissingIndexError: If artifact is missing required BundleIndex config
        """
        target = self._build_target(registry_ref, digest)
        container = Container(target)
        
        # Get manifest
        try:
            manifest = self.client.get_manifest(container)
        except Exception as e:
            import requests
            if isinstance(e, requests.exceptions.HTTPError):
                if e.response.status_code == 404:
                    raise NotFoundError(f"Manifest not found: {target}")
                elif e.response.status_code in (401, 403):
                    raise AuthError(f"Authentication failed for {target}")
            raise
        
        # Extract config descriptor
        cfg = manifest.get("config") or {}
        mt = cfg.get("mediaType")
        dg = cfg.get("digest")
        
        # Validate it's a BundleIndex
        if mt != BUNDLE_INDEX_MEDIA_TYPE:
            raise MissingIndexError(f"{registry_ref}@{digest[:12]}...")
        
        if not dg:
            raise MissingIndexError(f"{registry_ref}@{digest[:12]}...")
        
        # Fetch config blob
        raw = self.client.get_blob(container, dg).content
        
        # Parse as BundleIndex
        return BundleIndex.model_validate_json(raw)
    
    def pull_selected(
        self,
        registry_ref: str,
        digest: str,
        entries: List[BundleFileEntry],
        output_dir: Path,
        blob_store=None,  # Optional[BlobStore]
    ) -> None:
        """
        Pull selected files to output directory with digest verification.
        
        Args:
            registry_ref: Registry reference
            digest: Manifest digest to pull from
            entries: List of BundleFileEntry to pull
            output_dir: Output directory
            blob_store: BlobStore instance (required if any BLOB entries)
            
        Raises:
            DigestMismatchError: If any file fails digest verification
        """
        from .errors import BlobProviderMissingError
        
        target = self._build_target(registry_ref, digest)
        container = Container(target)
        
        for entry in entries:
            dst = output_dir / entry.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            
            # Download based on storage type
            if entry.storage == StorageType.OCI:
                # Download from registry
                self.client.download_blob(container, entry.digest, str(dst))
            else:
                # Download from blob storage
                if not entry.blobRef or not blob_store:
                    raise BlobProviderMissingError()
                blob_store.get(entry.blobRef, dst)
            
            # Always verify digest
            actual_digest = compute_digest(dst)
            if actual_digest != entry.digest:
                dst.unlink()  # Remove corrupted file
                raise DigestMismatchError(entry.path, entry.digest, actual_digest)

