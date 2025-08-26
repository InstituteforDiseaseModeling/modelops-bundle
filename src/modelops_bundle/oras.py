"""ORAS adapter for OCI registry operations."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import json
import tempfile
import warnings

from oras.provider import Registry
from oras.container import Container

from .context import ProjectContext
from .constants import BUNDLE_VERSION
from .core import FileInfo, RemoteState
from .utils import get_iso_timestamp

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
    
    def _build_target(self, registry_ref: str, tag: str) -> str:
        """Build full target reference."""
        return f"{registry_ref}:{tag}"
    
    def get_manifest_with_digest(
        self, registry_ref: str, reference: str = "latest"
    ) -> Tuple[dict, str, bytes]:
        """
        Return (manifest_json, canonical_digest, raw_bytes).
        
        Digest comes from Docker-Content-Digest header when available;
        otherwise it is computed from the exact raw bytes.
        """
        target = self._build_target(registry_ref, reference)
        container = Container(target)
        
        # Build the manifest URL path
        get_manifest_url = f"{self.client.prefix}://{container.manifest_url()}"
        
        # Use oras-py's do_request which handles auth for us
        resp = self.client.do_request(
            get_manifest_url,
            "GET",
            headers={"Accept": OCI_ACCEPT},
        )
        
        # Check response status
        if resp.status_code != 200:
            resp.raise_for_status()
        
        raw = resp.content or b""
        manifest = resp.json() if raw else {}
        
        digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            # Fallback: compute from raw bytes exactly as served
            digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
            warnings.warn(
                f"Registry did not return Docker-Content-Digest for {target}; "
                "using digest computed from raw manifest bytes."
            )
        
        return manifest, digest, raw
    
    def _create_path_annotations(self, files: List[FileInfo]) -> dict:
        """Create annotation mapping to preserve full paths.
        
        HACK: Works around oras-py stripping paths to basename.
        Maps each file to annotations that override the title.
        See: https://github.com/oras-project/oras-py/issues/217
        """
        annotations = {}
        for file_info in files:
            annotations[file_info.path] = {
                "org.opencontainers.image.title": file_info.path
            }
        return annotations
    
    def push_files(
        self,
        registry_ref: str,
        files: List[FileInfo],
        tag: str = "latest",
        artifact_type: str = "application/vnd.modelops.bundle.v1",
        ctx: Optional[ProjectContext] = None
    ) -> str:
        """Push files to registry and return manifest digest."""
        if not files:
            raise ValueError("No files to push")
        
        if ctx is None:
            ctx = ProjectContext()
        
        # Prepare file references (need absolute paths for existence check)
        file_refs = []
        for file_info in files:
            # Use absolute path for checking, but pass relative to ORAS
            abs_path = ctx.root / file_info.path
            if not abs_path.exists():
                raise FileNotFoundError(f"File not found: {file_info.path}")
            file_refs.append(str(file_info.path))  # Pass relative path as string to ORAS
        
        # HACK: Work around oras-py basename issue
        # Create temp annotation file to preserve full paths in layer titles
        # See docs/developer-notes.md#oras-py-path-stripping-issue
        # and https://github.com/oras-project/oras-py/issues/217
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=True) as anno_file:
            annotations = self._create_path_annotations(files)
            json.dump(annotations, anno_file)
            anno_file.flush()
            
            target = self._build_target(registry_ref, tag)
            
            # Create manifest annotations
            manifest_annotations = {
                "modelops-bundle.version": BUNDLE_VERSION,
                "org.opencontainers.image.created": get_iso_timestamp(),
            }
            
            # Push with ORAS
            # Note: artifact_type is part of OCI Image Manifest v1.1.0 spec
            # but oras-py doesn't directly support setting it via push parameters.
            # It would need to be set in the manifest after creation.
            response = self.client.push(
                target=target,
                files=file_refs,
                annotation_file=anno_file.name,  # Pass temp file
                manifest_annotations=manifest_annotations,
            )
        
        # 1) Try digest from push response headers
        digest = None
        try:
            digest = getattr(response, "headers", {}).get("Docker-Content-Digest")
        except Exception:
            pass
        
        # 2) Fall back to GET manifest (header + bytes) for canonical digest
        if not digest:
            _, digest, _ = self.get_manifest_with_digest(registry_ref, tag)
        
        return digest
    
    def pull_files(
        self,
        registry_ref: str,
        tag: str = "latest",
        output_dir: Path = Path("."),
        ctx: Optional[ProjectContext] = None
    ) -> List[Path]:
        """Pull files from registry and return paths."""
        if ctx is None:
            ctx = ProjectContext()
        
        target = self._build_target(registry_ref, tag)
        
        # Pull with ORAS
        files = self.client.pull(
            target=target,
            outdir=str(output_dir)
        )
        
        return [Path(f) for f in files]
    
    def get_manifest(
        self,
        registry_ref: str,
        tag: str = "latest"
    ) -> dict:
        """Get manifest from registry."""
        manifest, _, _ = self.get_manifest_with_digest(registry_ref, tag)
        return manifest
    
    def get_remote_state(
        self,
        registry_ref: str,
        tag: str = "latest"
    ) -> RemoteState:
        """Get remote state from manifest."""
        try:
            manifest, manifest_digest, _ = self.get_manifest_with_digest(registry_ref, tag)
        except Exception as e:
            # Registry might be empty or unreachable
            raise RuntimeError(f"Failed to fetch manifest: {e}")
        
        # Parse manifest to extract file info
        files = {}
        for layer in manifest.get("layers", []):
            annotations = layer.get("annotations", {})
            title = annotations.get("org.opencontainers.image.title")
            
            if title:
                files[title] = FileInfo(
                    path=title,
                    digest=layer["digest"],
                    size=layer["size"]
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

