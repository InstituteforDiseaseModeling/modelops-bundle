"""ORAS adapter for OCI registry operations."""

from pathlib import Path
from typing import Dict, List, Optional
import json
import tempfile

from oras.provider import Registry
from oras.container import Container

from .context import ProjectContext
from .constants import BUNDLE_VERSION
from .core import FileInfo, RemoteState


class OrasAdapter:
    """Adapter for ORAS operations using real oras-py."""
    
    def __init__(self, insecure: bool = True):
        """Initialize ORAS Registry client."""
        self.client = Registry(insecure=insecure)
    
    def _build_target(self, registry_ref: str, tag: str) -> str:
        """Build full target reference."""
        return f"{registry_ref}:{tag}"
    
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
            file_refs.append(file_info.path)  # Pass relative path to ORAS
        
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
                "org.opencontainers.image.created": _get_timestamp(),
            }
            
            # Push with ORAS
            response = self.client.push(
                target=target,
                files=file_refs,
                annotation_file=anno_file.name,  # Pass temp file
                manifest_annotations=manifest_annotations,
            )
        
        # Fetch the manifest to get the correct digest
        # The response headers may not always include Docker-Content-Digest,
        # so we fetch the manifest to compute it ourselves
        manifest = self.get_manifest(registry_ref, tag)
        
        # Compute manifest digest (canonical JSON hash)
        import hashlib
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
        digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        
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
        target = self._build_target(registry_ref, tag)
        
        # Parse as container
        container = Container(target)
        
        # Get manifest using provider method
        manifest = self.client.get_manifest(container)
        
        return manifest
    
    def get_remote_state(
        self,
        registry_ref: str,
        tag: str = "latest"
    ) -> RemoteState:
        """Get remote state from manifest."""
        try:
            manifest = self.get_manifest(registry_ref, tag)
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
        
        # Get manifest digest (compute if not available)
        import hashlib
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
        manifest_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        
        return RemoteState(
            manifest_digest=manifest_digest,
            files=files
        )


# ============= Utilities =============

def _get_timestamp() -> str:
    """Get ISO timestamp."""
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"
